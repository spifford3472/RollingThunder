#!/usr/bin/env python3
"""
RollingThunder - UI Intent Worker (Redis PubSub -> system actions)

DROP-IN REPLACEMENT for ui_intent_worker.py

Changes vs your current version:
- Default behavior: DO NOT restart individual services.
- New behavior: handle node reboot intents in a *distributed* way:
    * All nodes run this worker.
    * UI publishes an intent with params.nodeId.
    * Each node only acts if nodeId == RT_NODE_ID (self-targeted).
    * This avoids the controller trying to “remote reboot” other nodes.

Intents handled:
- intent == "node.reboot"
    params: { nodeId: "<target>", confirm: true }

Publishes results onto UI bus:
- ui.node.reboot.requested   (when the node accepts the request)
- ui.node.reboot.result      (best-effort; may not publish if reboot proceeds immediately)

Safety knobs (env):
- RT_UI_ALLOW_NODE_REBOOT: default "1"
- RT_UI_ALLOW_SERVICE_RESTART: default "0"  (kept for compatibility; off by default)
- RT_NODE_REBOOT_TIMEOUT_SEC: default "4.0"
"""

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
REBOOT_TIMEOUT_SEC = float(os.environ.get("RT_NODE_REBOOT_TIMEOUT_SEC", "4.0"))

ALLOW_NODE_REBOOT = os.environ.get("RT_UI_ALLOW_NODE_REBOOT", "1").strip() == "1"
ALLOW_SERVICE_RESTART = os.environ.get("RT_UI_ALLOW_SERVICE_RESTART", "0").strip() == "1"


def now_ms() -> int:
    return int(time.time() * 1000)


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
    # UI SSE handler wraps as {channel, ts, server_time_ms, data: ...}
    r.publish(UI_BUS_CH, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def build_service_allowlist(cfg: Dict[str, Any]) -> Dict[str, str]:
    """
    Returns {serviceId: systemdUnit}
    Only includes services owned by this node and having an explicit unit.
    (Kept for compatibility; service restart is OFF by default.)
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


def reboot_self() -> tuple[bool, str]:
    """
    Reboot THIS node. Assumes this service runs with sufficient privileges (usually root).
    Uses systemctl reboot for consistency with systemd.
    """
    try:
        res = subprocess.run(
            ["systemctl", "reboot", "--no-wall"],
            capture_output=True,
            text=True,
            timeout=REBOOT_TIMEOUT_SEC,
            check=False,
        )
        # If reboot is accepted, returncode is often 0 (system may reboot immediately after)
        if res.returncode == 0:
            return True, "reboot_initiated"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"reboot_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        # A timeout here can actually mean reboot is in-progress and systemctl didn't return cleanly.
        # Treat as "likely initiated" but mark uncertain.
        return True, "reboot_timeout_assume_initiated"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"


def parse_json(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def main() -> None:
    cfg = load_config()
    service_allow = build_service_allowlist(cfg)  # compat only

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
            "ui_bus_channel": UI_BUS_CH,
            "features": {
                "node_reboot": ALLOW_NODE_REBOOT,
                "service_restart": ALLOW_SERVICE_RESTART,
            },
            "allowed_services": sorted(list(service_allow.keys()))[:50] if ALLOW_SERVICE_RESTART else [],
        },
    )

    while True:
        try:
            msg = ps.get_message(timeout=1.0)
        except Exception:
            # Redis hiccup; back off a bit and continue
            time.sleep(0.25)
            continue

        if not msg or msg.get("type") != "message":
            time.sleep(0.05)
            continue

        obj = parse_json(msg.get("data"))

        intent = str(obj.get("intent") or "").strip()
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}

        # ---- Node reboot (preferred) ----
        if intent == "node.reboot":
            node_id = str(params.get("nodeId") or params.get("node_id") or "").strip()
            confirm = params.get("confirm") is True  # must be explicit true

            # Ignore malformed
            if not node_id:
                publish_bus(
                    r,
                    {
                        "topic": "ui.node.reboot.result",
                        "node": NODE_ID,
                        "target": node_id,
                        "ok": False,
                        "msg": "bad_request:no_nodeId",
                        "ts_ms": now_ms(),
                    },
                )
                continue

            # Distributed targeting: only act if request is for THIS node
            if node_id != NODE_ID:
                continue

            if not ALLOW_NODE_REBOOT:
                publish_bus(
                    r,
                    {
                        "topic": "ui.node.reboot.result",
                        "node": NODE_ID,
                        "target": node_id,
                        "ok": False,
                        "msg": "not_allowed",
                        "ts_ms": now_ms(),
                    },
                )
                continue

            if not confirm:
                publish_bus(
                    r,
                    {
                        "topic": "ui.node.reboot.result",
                        "node": NODE_ID,
                        "target": node_id,
                        "ok": False,
                        "msg": "not_confirmed",
                        "ts_ms": now_ms(),
                    },
                )
                continue

            # Ack first (best chance UI sees something before we disappear)
            publish_bus(
                r,
                {
                    "topic": "ui.node.reboot.requested",
                    "node": NODE_ID,
                    "target": node_id,
                    "ok": True,
                    "msg": "accepted",
                    "ts_ms": now_ms(),
                },
            )

            ok, msgtxt = reboot_self()

            # Best-effort result; may not be delivered if reboot proceeds fast.
            publish_bus(
                r,
                {
                    "topic": "ui.node.reboot.result",
                    "node": NODE_ID,
                    "target": node_id,
                    "ok": ok,
                    "msg": msgtxt,
                    "ts_ms": now_ms(),
                },
            )
            # If reboot initiates, this process will likely terminate shortly.
            continue

        # ---- Service restart (compat only; OFF by default) ----
        if intent == "service.restart":
            if not ALLOW_SERVICE_RESTART:
                service_id = str(params.get("serviceId") or params.get("service_id") or "").strip()
                publish_bus(
                    r,
                    {
                        "topic": "ui.service.restart.result",
                        "node": NODE_ID,
                        "serviceId": service_id,
                        "ok": False,
                        "msg": "feature_disabled",
                        "ts_ms": now_ms(),
                    },
                )
                continue

            service_id = str(params.get("serviceId") or params.get("service_id") or "").strip()
            unit = service_allow.get(service_id)

            if not unit:
                publish_bus(
                    r,
                    {
                        "topic": "ui.service.restart.result",
                        "node": NODE_ID,
                        "serviceId": service_id,
                        "ok": False,
                        "msg": "not_allowed",
                        "ts_ms": now_ms(),
                    },
                )
                continue

            ok, msgtxt = restart_unit(unit)
            publish_bus(
                r,
                {
                    "topic": "ui.service.restart.result",
                    "node": NODE_ID,
                    "serviceId": service_id,
                    "unit": unit,
                    "ok": ok,
                    "msg": msgtxt,
                    "ts_ms": now_ms(),
                },
            )
            continue

        # Unknown/ignored intents: do nothing


if __name__ == "__main__":
    main()