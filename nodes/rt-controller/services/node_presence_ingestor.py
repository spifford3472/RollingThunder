#!/usr/bin/env python3
"""
node_presence_ingestor.py — RollingThunder (rt-controller)

Phase 14.4:
- Subscribe to MQTT presence topics: rt/presence/+
- Ingest node presence payloads (JSON)
- Publish derived node presence state into Redis hashes:
    rt:nodes:<node_id>

Phase 14.5:
- TTL-based online/offline evaluation (controller-owned judgment)
- Periodically sweep rt:nodes:* and mark stale nodes offline

Event hardening / noise reduction:
- Publish state.changed only when semantic node state changes.
- Keep last_seen fresh for deterministic stale/offline detection.
- Bound sweep-derived age writes so Redis is not rewritten every sweep.

Constraints:
- Read-only on MQTT; write-only to Redis for derived state
- No control actions
- Bounded payload storage (no dumping unbounded JSON into Redis)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timezone

import redis
import threading

try:
    import paho.mqtt.client as mqtt  # type: ignore
except Exception:
    mqtt = None  # type: ignore


# -----------------------------
# Env config
# -----------------------------
REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

MQTT_HOST = os.environ.get("RT_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("RT_MQTT_PORT", "1883"))
MQTT_KEEPALIVE = int(os.environ.get("RT_MQTT_KEEPALIVE", "30"))

TOPIC_PREFIX = os.environ.get("RT_PRESENCE_TOPIC_PREFIX", "rt/presence")
NODE_KEY_PREFIX = os.environ.get("RT_KEY_NODE_PREFIX", "rt:nodes:")

DEPLOY_TOPIC_PREFIX = os.environ.get("RT_DEPLOY_TOPIC_PREFIX", "rt/deploy/report")
DEPLOY_KEY_PREFIX = os.environ.get("RT_KEY_DEPLOY_REPORT_PREFIX", "rt:deploy:report:")
DEPLOY_TTL_SEC = float(os.environ.get("RT_DEPLOY_TTL_SEC", "300"))

# Presence interval on nodes is ~2.5s; TTL should be comfortably larger.
SWEEP_SEC = float(os.environ.get("RT_PRESENCE_SWEEP_SEC", "2.0"))
STALE_AFTER_SEC = float(os.environ.get("RT_PRESENCE_STALE_AFTER_SEC", "12.0"))
OFFLINE_AFTER_SEC = float(os.environ.get("RT_PRESENCE_OFFLINE_AFTER_SEC", "30.0"))
CONTROLLER_NODE_ID = os.environ.get("RT_CONTROLLER_NODE_ID", "rt-controller")

# Bound sweep-derived age/status writes when only age_sec would change.
AGE_WRITE_INTERVAL_SEC = float(os.environ.get("RT_PRESENCE_AGE_WRITE_INTERVAL_SEC", "10.0"))

# System bus for state.changed notifications. UI bus is projector-only.
SYSTEM_BUS_CHANNEL = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")
UI_BUS_MAX_KEYS = int(os.environ.get("RT_UI_BUS_MAX_KEYS", "25"))

# Fields that should wake downstream consumers when they change.
# Volatile heartbeat fields are intentionally excluded.
PRESENCE_SEMANTIC_FIELDS = (
    "id",
    "role",
    "status",
    "hostname",
    "ip",
    "ui_render_ok",
    "publisher_error",
)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_ms() -> int:
    return int(time.time() * 1000)


def publish_state_changed(r: redis.Redis, keys: list[str], source: str) -> None:
    # hard bounds
    if not keys:
        return
    keys = [k for k in keys if isinstance(k, str) and k.startswith("rt:")]
    keys = keys[:UI_BUS_MAX_KEYS]
    if not keys:
        return

    evt = {
        "topic": "state.changed",
        "payload": {"keys": keys},
        "ts_ms": now_ms(),
        "source": source,
    }
    try:
        r.publish(SYSTEM_BUS_CHANNEL, json.dumps(evt, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        pass


def is_deploy_report(msg: Dict[str, Any]) -> bool:
    return msg.get("schema") == "deploy.report.v1"


def store_deploy_report(r: redis.Redis, report: Dict[str, Any]) -> None:
    node_id = report.get("node_id")
    if not isinstance(node_id, str) or not node_id.strip():
        return

    key = f"{DEPLOY_KEY_PREFIX}{node_id.strip()}"
    payload = json.dumps(report, separators=(",", ":"), ensure_ascii=False)

    try:
        previous = r.get(key)
    except Exception:
        previous = None

    if previous != payload:
        r.set(key, payload)
        publish_state_changed(r, [key], source="deploy_report_ingestor")

    # Refresh TTL whether or not the payload changed.
    try:
        r.expire(key, int(DEPLOY_TTL_SEC))
    except Exception:
        pass


def safe_str(v: Any, max_len: int = 200) -> str:
    s = "" if v is None else str(v)
    if len(s) > max_len:
        return s[:max_len]
    return s


def parse_json(payload_bytes: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        obj = json.loads(payload_bytes.decode("utf-8", errors="replace"))
        if not isinstance(obj, dict):
            return None, "payload_not_object"
        return obj, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def derive_node_fields(msg: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, str]]:
    """
    Extract a bounded subset of fields from a presence payload.
    Returns (node_id, mapping_for_redis_hash)
    """
    node_id = msg.get("node_id")
    if not isinstance(node_id, str) or not node_id.strip():
        return None, {"publisher_error": "missing_node_id"}

    role = msg.get("role")
    role_s = safe_str(role, 50) if isinstance(role, str) else "unknown"

    ip = None
    net = msg.get("net")
    if isinstance(net, dict):
        ip_val = net.get("ip")
        if isinstance(ip_val, str) and ip_val.strip():
            ip = ip_val.strip()

    if not ip:
        ip_val = msg.get("ip")
        if isinstance(ip_val, str) and ip_val.strip():
            ip = ip_val.strip()

    render_ok = None
    ui = msg.get("ui")
    if isinstance(ui, dict):
        ro = ui.get("render_ok")
        if isinstance(ro, bool):
            render_ok = ro

    hostname = msg.get("hostname")
    hostname_s = safe_str(hostname, 80) if isinstance(hostname, str) else ""

    node_ts = msg.get("timestamp") or msg.get("ts_iso") or msg.get("ts") or ""
    node_ts_s = safe_str(node_ts, 40) if isinstance(node_ts, str) else ""

    ms = now_ms()

    mapping: Dict[str, str] = {
        "id": node_id.strip(),
        "role": role_s,
        "status": "online",
        "age_sec": "0",
        "last_seen_ms": str(ms),
        "last_seen_ts": now_iso_utc(),
        "node_ts": node_ts_s,
        "last_update_ms": str(ms),
        "publisher_error": "",
    }

    mapping["hostname"] = hostname_s
    mapping["ip"] = ip or ""
    mapping["ui_render_ok"] = (
        "true" if render_ok is True
        else "false" if render_ok is False
        else ""
    )

    return node_id.strip(), mapping


def semantic_presence_changed(prev: Dict[str, str], new: Dict[str, str]) -> bool:
    for field in PRESENCE_SEMANTIC_FIELDS:
        if (prev.get(field) or "") != (new.get(field) or ""):
            return True
    return False


def hset_changed_fields(r: redis.Redis, key: str, prev: Dict[str, str], mapping: Dict[str, str]) -> bool:
    changed_fields = {
        field: value
        for field, value in mapping.items()
        if (prev.get(field) or "") != (value or "")
    }
    if not changed_fields:
        return False
    r.hset(key, mapping=changed_fields)
    return True


def should_write_sweeper_age(prev: Dict[str, str], ms_now: int) -> bool:
    try:
        last_update_ms = int(prev.get("last_update_ms") or "0")
    except Exception:
        last_update_ms = 0
    return (ms_now - last_update_ms) >= int(max(1.0, AGE_WRITE_INTERVAL_SEC) * 1000)


def update_presence_status(r: redis.Redis, key: str, stale_after_ms: int, offline_after_ms: int) -> None:
    """
    Compute age_sec and controller-derived status for the node hash:
      - online  (age <= stale_after)
      - stale   (stale_after < age <= offline_after)
      - offline (age > offline_after)
    """
    try:
        h = r.hgetall(key)
        last_seen_ms = h.get("last_seen_ms")
        if not last_seen_ms:
            return

        try:
            last_seen_i = int(last_seen_ms)
        except Exception:
            return

        ms_now = now_ms()
        age_ms = ms_now - last_seen_i
        age_sec = max(0, int(age_ms / 1000))

        if age_ms <= stale_after_ms:
            status = "online"
        elif age_ms <= offline_after_ms:
            status = "stale"
        else:
            status = "offline"

        prev_status = (h.get("status") or "").strip()
        status_changed = status != prev_status

        if status_changed or should_write_sweeper_age(h, ms_now):
            r.hset(key, mapping={
                "status": status,
                "age_sec": str(age_sec),
                "last_update_ms": str(ms_now),
            })

        if status_changed:
            publish_state_changed(r, [key], source="presence_sweeper")

    except Exception:
        return


def main() -> int:
    if mqtt is None:
        print("[presence_ingestor] ERROR: paho-mqtt not installed")
        return 2

    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
    )

    try:
        r.ping()
    except Exception as e:
        print(f"[presence_ingestor] WARN: Redis ping failed at startup: {type(e).__name__}: {e}")

    stale_after_ms = int(STALE_AFTER_SEC * 1000)
    offline_after_ms = int(OFFLINE_AFTER_SEC * 1000)

    def on_connect(client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        if rc == 0:
            presence_topic = f"{TOPIC_PREFIX}/+"
            deploy_topic = f"{DEPLOY_TOPIC_PREFIX}/+"
            client.subscribe(presence_topic, qos=0)
            client.subscribe(deploy_topic, qos=0)
            print(f"[presence_ingestor] MQTT connected; subscribed {presence_topic} and {deploy_topic}")
        else:
            print(f"[presence_ingestor] MQTT connect failed rc={rc}")

    def on_message(client: mqtt.Client, userdata: Any, msg_in: Any) -> None:
        payload, _perr = parse_json(msg_in.payload)
        if payload is None:
            return

        if is_deploy_report(payload):
            try:
                store_deploy_report(r, payload)
            except Exception:
                pass
            return

        node_id, mapping = derive_node_fields(payload)
        if not node_id:
            return

        key = f"{NODE_KEY_PREFIX}{node_id}"
        try:
            prev = r.hgetall(key)
            semantic_changed = semantic_presence_changed(prev, mapping)

            # Always keep last_seen fresh for deterministic TTL handling, but
            # only publish when stable/semantic fields changed.
            hset_changed_fields(r, key, prev, mapping)

            if semantic_changed:
                publish_state_changed(r, [key], source="presence_ingestor")

        except Exception as e:
            try:
                err_mapping = {
                    "publisher_error": safe_str(f"redis_write_failed: {type(e).__name__}: {e}", 240),
                    "last_update_ms": str(now_ms()),
                }
                prev = r.hgetall(key)
                error_changed = semantic_presence_changed(prev, err_mapping)
                hset_changed_fields(r, key, prev, err_mapping)
                if error_changed:
                    publish_state_changed(r, [key], source="presence_ingestor")
            except Exception:
                pass

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    except Exception as e:
        print(f"[presence_ingestor] ERROR: MQTT connect failed: {type(e).__name__}: {e}")
        while True:
            time.sleep(2.0)
            try:
                client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
                break
            except Exception:
                continue

    def sweeper_loop() -> None:
        while True:
            try:
                for k in r.scan_iter(match=f"{NODE_KEY_PREFIX}*"):
                    update_presence_status(r, k, stale_after_ms, offline_after_ms)
            except Exception:
                pass
            time.sleep(SWEEP_SEC)

    t = threading.Thread(target=sweeper_loop, name="presence-sweeper", daemon=True)
    t.start()

    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
