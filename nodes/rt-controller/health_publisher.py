# nodes/rt-controller/health_publisher.py
from __future__ import annotations

import os
import platform
import socket
import time
from typing import Any, Dict

import redis


def _ns(cfg: Dict[str, Any]) -> str:
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)


def publish_controller_health(
    r: redis.Redis,
    cfg: Dict[str, Any],
    *,
    node_id: str,
    boot_ms: int,
    mqtt_ok: bool,
) -> None:
    """
    Writes a compact health snapshot to <ns>:system:health.
    Intended to be called on each heartbeat tick.
    """
    prefix = _ns(cfg)
    now_ms = int(time.time() * 1000)
    uptime_sec = max(0, int((now_ms - boot_ms) / 1000))

    schema = cfg.get("schema") or {}
    schema_id = str(schema.get("id") or "")
    schema_version = str(schema.get("version") or "")

    payload = {
        "node_id": node_id,
        "hostname": socket.gethostname(),
        "boot_ms": boot_ms,
        "last_seen_ms": now_ms,
        "uptime_sec": uptime_sec,
        "pid": os.getpid(),
        "python": platform.python_version(),
        "schema_id": schema_id,
        "schema_version": schema_version,
        "redis_ok": "1",
        "mqtt_ok": "1" if mqtt_ok else "0",
    }

    r.hset(_k(prefix, "system", "health"), mapping=payload)

    # Optional: keep system:info fresh with a couple fields (safe + handy)
    r.hset(_k(prefix, "system", "info"), mapping={"last_seen_ms": now_ms, "uptime_sec": uptime_sec})
