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

# UI bus publish (read-only consumers)
UI_BUS_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")
UI_BUS_MAX_KEYS = int(os.environ.get("RT_UI_BUS_MAX_KEYS", "25"))  # safety for any future batching

def is_deploy_report(msg: Dict[str, Any]) -> bool:
    return msg.get("schema") == "deploy.report.v1"

def store_deploy_report(r: redis.Redis, report: Dict[str, Any]) -> None:
    node_id = report.get("node_id")
    if not isinstance(node_id, str) or not node_id.strip():
        return

    key = f"{DEPLOY_KEY_PREFIX}{node_id.strip()}"
    # Compact encoding; bounded by nature (we control what publisher sends)
    payload = json.dumps(report, separators=(",", ":"), ensure_ascii=False)

    r.set(key, payload)
    # TTL so we can detect staleness
    try:
        r.expire(key, int(DEPLOY_TTL_SEC))
    except Exception:
        pass

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
        r.publish(UI_BUS_CHANNEL, json.dumps(evt, separators=(",", ":"), ensure_ascii=False))
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

    # --- IP extraction: accept either top-level "ip" or net.ip (preferred) ---
    ip = None

    # 1) net.ip (preferred)
    net = msg.get("net")
    if isinstance(net, dict):
        ip_val = net.get("ip")
        if isinstance(ip_val, str) and ip_val.strip():
            ip = ip_val.strip()

    # 2) top-level ip (compat / legacy / simple publishers)
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

    # Controller-derived fields:
    ms = now_ms()

    mapping: Dict[str, str] = {
        "id": node_id.strip(),
        "role": role_s,
        "status": "online",
        "age_sec": "0",
        "last_seen_ms": str(ms),
        "last_seen_ts": now_iso_utc(),  # controller timestamp (authoritative)
        "node_ts": node_ts_s,           # node timestamp (informational)
        "last_update_ms": str(ms),
    }



    if hostname_s:
        mapping["hostname"] = hostname_s
    if ip:
        mapping["ip"] = ip
    if render_ok is not None:
        mapping["ui_render_ok"] = "true" if render_ok else "false"

    # Clear old errors on healthy ingestion
    mapping["publisher_error"] = ""

    return node_id.strip(), mapping


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

        r.hset(key, mapping={
            "status": status,
            "age_sec": str(age_sec),
            "last_update_ms": str(ms_now),
        })

        # Publish only when status changes (online/stale/offline)
        if status != prev_status:
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

    # Verify Redis reachable early (but keep service resilient)
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
        payload, perr = parse_json(msg_in.payload)
        if payload is None:
            return

        # 1) Deploy report path (MQTT -> Redis string key)
        if is_deploy_report(payload):
            try:
                store_deploy_report(r, payload)
            except Exception:
                pass
            return

        # 2) Presence path (existing logic)
        node_id, mapping = derive_node_fields(payload)

        if not node_id:
            return

        key = f"{NODE_KEY_PREFIX}{node_id}"
        try:
            # Compare against previous to avoid publish spam
            prev = r.hgetall(key)
            r.hset(key, mapping=mapping)

            # Publish if any meaningful field changed
            # (ignore controller timestamps that always change)
            meaningful = ("role", "ip", "hostname", "ui_render_ok", "publisher_error")

            changed = False
            for f in meaningful:
                if (prev.get(f) or "") != (mapping.get(f) or ""):
                    changed = True
                    break
            if changed:
                publish_state_changed(r, [key], source="presence_ingestor")
  
        except Exception as e:
            try:
                r.hset(key, mapping={
                    "publisher_error": safe_str(f"redis_write_failed: {type(e).__name__}: {e}", 240),
                    "last_update_ms": str(now_ms()),
                })
            except Exception:
                pass


    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    except Exception as e:
        print(f"[presence_ingestor] ERROR: MQTT connect failed: {type(e).__name__}: {e}")
        # Keep trying forever (systemd will restart, but we can also retry)
        while True:
            time.sleep(2.0)
            try:
                client.connect(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
                break
            except Exception:
                continue

    def sweeper_loop() -> None:
        # Runs forever; controller-owned TTL enforcement
        while True:
            try:
                for k in r.scan_iter(match=f"{NODE_KEY_PREFIX}*"):
                    update_presence_status(r, k, stale_after_ms, offline_after_ms)
            except Exception:
                pass
            time.sleep(SWEEP_SEC)

    t = threading.Thread(target=sweeper_loop, name="presence-sweeper", daemon=True)
    t.start()


    # Blocking loop (simple + appliance-style)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
