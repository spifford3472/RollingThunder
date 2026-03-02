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
NODE_ID = os.environ.get("RT_NODE_ID", "unknown-node")

# Reboot behavior:
# - "reboot" (default) -> systemctl reboot
# - "poweroff"         -> systemctl poweroff
REBOOT_MODE = os.environ.get("RT_NODE_REBOOT_MODE", "reboot").strip().lower()
SYSTEMCTL_TIMEOUT_SEC = float(os.environ.get("RT_SYSTEMCTL_TIMEOUT_SEC", "8.0"))

# Safety: default off unless explicitly enabled on that node.
# Canonical flag: RT_ALLOW_REBOOT=1
# Back-compat: also accept RT_ALLOW_NODE_REBOOT=1
ALLOW_NODE_REBOOT = (
    os.environ.get("RT_ALLOW_REBOOT", "0").strip() == "1"
    or os.environ.get("RT_ALLOW_NODE_REBOOT", "0").strip() == "1"
)

def env_truthy(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def now_ms() -> int:
    return int(time.time() * 1000)


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


def _truthy(x: Any) -> bool:
    if x is True:
        return True
    if isinstance(x, str) and x.strip().lower() in ("1", "true", "yes", "y", "on"):
        return True
    if isinstance(x, (int, float)) and x == 1:
        return True
    return False


def reboot_this_node() -> tuple[bool, str]:
    """
    Reboot/poweroff the local node. Must be run with sufficient privileges (root).
    """
    cmd = ["systemctl", "--no-wall"]
    if REBOOT_MODE == "poweroff":
        cmd += ["poweroff"]
    else:
        cmd += ["reboot"]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
        if res.returncode == 0:
            return True, f"{REBOOT_MODE}_initiated"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"{REBOOT_MODE}_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        # systemctl may block as the system is going down; treat as success-ish
        return True, f"{REBOOT_MODE}_initiated_timeout"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"


def main() -> None:
    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_bus(
        r,
        {
            "topic": "ui.intent.worker.hello",
            "node": NODE_ID,
            "ts_ms": now_ms(),
            "intents_channel": INTENTS_CH,
            "capabilities": {
                "node_reboot": ALLOW_NODE_REBOOT,
                "mode": REBOOT_MODE,
            },
        },
    )

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

        # Only handle node.reboot now (service restart removed by design)
        if intent != "node.reboot":
            continue

        target = str(params.get("nodeId") or params.get("node_id") or "").strip()
        confirm = _truthy(params.get("confirm"))

        # Always publish result so UI has deterministic feedback
        base = {
            "topic": "ui.node.reboot.result",
            "node": NODE_ID,
            "target": target,
            "ts_ms": now_ms(),
        }

        if not target:
            publish_bus(r, {**base, "ok": False, "msg": "bad_request:missing_nodeId"})
            continue

        if target != NODE_ID:
            # This worker only reboots its own node.
            publish_bus(r, {**base, "ok": False, "msg": "not_for_this_node"})
            continue

        if not ALLOW_NODE_REBOOT:
            publish_bus(r, {**base, "ok": False, "msg": "reboot_disabled"})
            continue

        if not confirm:
            publish_bus(r, {**base, "ok": False, "msg": "not_confirmed"})
            continue

        ok, msgtxt = reboot_this_node()
        publish_bus(r, {**base, "ok": ok, "msg": msgtxt})


if __name__ == "__main__":
    main()