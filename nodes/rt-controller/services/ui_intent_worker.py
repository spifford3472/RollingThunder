#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
UI_BUS_CH = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")

CONFIG_PATH = Path(os.environ.get("RT_CONFIG_PATH", "/opt/rollingthunder/config/app.json"))
NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

SYSTEMCTL_TIMEOUT_SEC = float(os.environ.get("RT_SYSTEMCTL_TIMEOUT_SEC", "8.0"))
SHUTDOWN_TIMEOUT_SEC = float(os.environ.get("RT_SHUTDOWN_TIMEOUT_SEC", "3.0"))

# For remote node restarts, you’ll eventually want SSH or an agent.
# For now, we only allow restarting *this* node, unless explicitly enabled.
ALLOW_REMOTE = (os.environ.get("RT_ALLOW_REMOTE_NODE_REBOOT", "0").strip() == "1")

def now_ms() -> int:
    return int(time.time() * 1000)

def load_config() -> Dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    r.ping()
    return r

def publish_bus(r: redis.Redis, payload: Dict[str, Any]) -> None:
    r.publish(UI_BUS_CH, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

def build_service_allowlist(cfg: Dict[str, Any]) -> Dict[str, str]:
    """
    Returns {serviceId: systemdUnit}
    Only includes services owned by this node and having an explicit unit.
    """
    out: Dict[str, str] = {}
    services = cfg.get("services") if isinstance(cfg.get("services"), dict) else {}
    for sid, sobj in services.items():
        if not isinstance(sobj, dict):
            continue
        if sobj.get("ownerNode") != NODE_ID:
            continue
        unit = None
        sysd = sobj.get("systemd")
        if isinstance(sysd, dict):
            unit = sysd.get("unit")
        if isinstance(unit, str) and unit.strip():
            out[str(sid)] = unit.strip()
    return out

def restart_unit(unit: str) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
        if res.returncode == 0:
            return True, "restarted"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"systemctl_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        return False, "systemctl_timeout"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"

def shutdown_this_node_halt() -> tuple[bool, str]:
    """
    HALT the system now (matches your '... -h 0' spirit).
    If you actually meant REBOOT, change '-h' to '-r'.
    """
    try:
        res = subprocess.run(
            ["/sbin/shutdown", "-h", "now"],
            capture_output=True,
            text=True,
            timeout=SHUTDOWN_TIMEOUT_SEC,
            check=False,
        )
        if res.returncode == 0:
            return True, "shutdown_initiated"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"shutdown_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        # shutdown may return slowly while system is already going down
        return True, "shutdown_initiated_timeout"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"

def main() -> None:
    cfg = load_config()
    svc_allow = build_service_allowlist(cfg)

    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_bus(r, {
        "topic": "ui.intent.worker.hello",
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "intents_channel": INTENTS_CH,
        "allowed_services": sorted(list(svc_allow.keys()))[:50],
        "features": {
            "service_restart": True,
            "node_reboot": True,
            "allow_remote_node_reboot": ALLOW_REMOTE,
        }
    })

    while True:
        msg = ps.get_message(timeout=1.0)
        if not msg or msg.get("type") != "message":
            time.sleep(0.05)
            continue

        raw = msg.get("data")
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            continue

        intent = str(obj.get("intent") or "").strip()
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}

        # -------------------------
        # service.restart (optional)
        # -------------------------
        if intent == "service.restart":
            service_id = str(params.get("serviceId") or params.get("service_id") or "").strip()
            unit = svc_allow.get(service_id)

            if not unit:
                publish_bus(r, {
                    "topic": "ui.service.restart.result",
                    "serviceId": service_id,
                    "ok": False,
                    "msg": "not_allowed",
                    "ts_ms": now_ms(),
                })
                continue

            ok, msgtxt = restart_unit(unit)
            publish_bus(r, {
                "topic": "ui.service.restart.result",
                "serviceId": service_id,
                "unit": unit,
                "ok": ok,
                "msg": msgtxt,
                "ts_ms": now_ms(),
            })
            continue

        # -------------------------
        # node.reboot (dangerous)
        # -------------------------
        if intent == "node.reboot":
            target = str(params.get("nodeId") or params.get("node_id") or "").strip()
            confirmed = params.get("confirm") is True

            if not confirmed:
                publish_bus(r, {
                    "topic": "ui.node.reboot.result",
                    "node": NODE_ID,
                    "target": target,
                    "ok": False,
                    "msg": "not_confirmed",
                    "ts_ms": now_ms(),
                })
                continue

            # Safety: only allow acting on this node unless remote explicitly enabled
            if target != NODE_ID and not ALLOW_REMOTE:
                publish_bus(r, {
                    "topic": "ui.node.reboot.result",
                    "node": NODE_ID,
                    "target": target,
                    "ok": False,
                    "msg": "remote_not_allowed",
                    "ts_ms": now_ms(),
                })
                continue

            if target != NODE_ID and ALLOW_REMOTE:
                # Placeholder for future: ssh/system agent.
                publish_bus(r, {
                    "topic": "ui.node.reboot.result",
                    "node": NODE_ID,
                    "target": target,
                    "ok": False,
                    "msg": "remote_not_implemented",
                    "ts_ms": now_ms(),
                })
                continue

            ok, msgtxt = shutdown_this_node_halt()
            publish_bus(r, {
                "topic": "ui.node.reboot.result",
                "node": NODE_ID,
                "target": target,
                "ok": ok,
                "msg": msgtxt,
                "ts_ms": now_ms(),
            })
            continue

        # ignore other intents

if __name__ == "__main__":
    main()