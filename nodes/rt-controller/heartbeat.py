# nodes/rt-controller/heartbeat.py
from __future__ import annotations

import time
import socket
from typing import Any, Dict

import redis

from health_publisher import publish_controller_health


def _ns(cfg: Dict[str, Any]) -> str:
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)

def _host_ip_best_effort() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # best-effort; no dependency on reachability
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def run_redis_heartbeat(
    r: redis.Redis,
    cfg: Dict[str, Any],
    *,
    node_id: str,
    interval_sec: float,
    boot_ms: int,
    mqtt_ok: bool,
) -> None:
    """
    Forever loop:
    - updates rt:nodes:<node_id>.last_seen_ms
    - updates rt:system:health snapshot
    """
    prefix = _ns(cfg)
    node_key = _k(prefix, "nodes", node_id)

    while True:
        now_ms = int(time.time() * 1000)

        # Node heartbeat (existing behavior)
        ip = _host_ip_best_effort()
        hb = {
            "last_seen_ms": now_ms,
            "status": "Online",
            "hostname": socket.gethostname(),
        }
        if ip:
            hb["ip"] = ip
        r.hset(node_key, mapping=hb)


        # System health snapshot (new Phase 9)
        publish_controller_health(
            r,
            cfg,
            node_id=node_id,
            boot_ms=boot_ms,
            mqtt_ok=mqtt_ok,
        )

        time.sleep(interval_sec)

