# nodes/rt-controller/state_publisher.py
from __future__ import annotations

import socket
import time
from typing import Any, Dict

import redis


class StatePublishError(RuntimeError):
    pass


def _ns(cfg: Dict[str, Any]) -> str:
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ip_best_effort() -> str:
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except Exception:
        return ""


def publish_initial_state(
    r: redis.Redis,
    cfg: Dict[str, Any],
    *,
    node_id: str = "rt-controller",
    mqtt_connected: bool = False,
    redis_connected: bool = True,
    boot_ms: int,
) -> None:
    prefix = _ns(cfg)
    hostname = socket.gethostname()
    now_ms = _now_ms()
    ip = _ip_best_effort()

    schema = cfg.get("schema") or {}
    schema_id = str(schema.get("id") or "")
    schema_version = str(schema.get("version") or "")

    pages = cfg.get("pages") if isinstance(cfg.get("pages"), list) else []
    panels = cfg.get("panels") if isinstance(cfg.get("panels"), list) else []
    services = cfg.get("services") if isinstance(cfg.get("services"), dict) else {}

    try:
        pipe = r.pipeline(transaction=False)

        # rt:system:*
        pipe.hset(
            _k(prefix, "system", "info"),
            mapping={
                "node_id": node_id,
                "hostname": hostname,
                "boot_ms": str(boot_ms),
                "schema_id": schema_id,
                "schema_version": schema_version,
                "pages_count": str(len(pages)),
                "panels_count": str(len(panels)),
                "services_count": str(len(services)),
                "redis_connected": "1" if redis_connected else "0",
                "mqtt_connected": "1" if mqtt_connected else "0",
            },
        )
        pipe.set(_k(prefix, "system", "boot_ms"), str(boot_ms))

        pipe.sadd(_k(prefix, "system", "nodes"), node_id)
        if services:
            pipe.sadd(_k(prefix, "system", "services"), *sorted(services.keys()))

        # rt:nodes:*
        pipe.hset(
            _k(prefix, "nodes", node_id),
            mapping={
                "id": node_id,
                "role": "controller",
                "status": "online",
                "boot_ms": str(boot_ms),
                "hostname": hostname,
                "ip": ip,
                "last_seen_ms": str(now_ms),
                "last_update_ms": str(now_ms),
                "publisher_error": "",
            },
        )

        # rt:services:*
        for sid in sorted(services.keys()):
            sobj = services.get(sid)
            if not isinstance(sobj, dict):
                continue

            scope = sobj.get("scope")
            owner = sobj.get("ownerNode")

            lifecycle = sobj.get("lifecycle") if isinstance(sobj.get("lifecycle"), dict) else {}
            start_policy = lifecycle.get("startPolicy")
            stop_policy = lifecycle.get("stopPolicy")

            pipe.hset(
                _k(prefix, "services", sid),
                mapping={
                    "id": sid,
                    "scope": str(scope) if scope is not None else "",
                    "ownerNode": str(owner) if owner is not None else "",
                    "startPolicy": str(start_policy) if start_policy is not None else "",
                    "stopPolicy": str(stop_policy) if stop_policy is not None else "",
                    "state": "unknown",
                    "last_update_ms": str(boot_ms),
                },
            )

        pipe.execute()

    except Exception as e:
        raise StatePublishError(f"Failed to publish initial state to Redis: {e}") from e
