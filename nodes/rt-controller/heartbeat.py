# nodes/rt-controller/heartbeat.py
from __future__ import annotations

import time
from typing import Any, Dict

import redis


def _ns(cfg: Dict[str, Any]) -> str:
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)


def run_redis_heartbeat(
    r: redis.Redis,
    cfg: Dict[str, Any],
    *,
    node_id: str,
    interval_sec: float,
) -> None:
    """
    Forever loop: updates rt:nodes:<node_id>.last_seen_ms.
    """
    prefix = _ns(cfg)
    key = _k(prefix, "nodes", node_id)

    while True:
        now_ms = int(time.time() * 1000)
        r.hset(key, mapping={"last_seen_ms": now_ms, "status": "up"})
        time.sleep(interval_sec)
