# nodes/rt-controller/state_publisher.py
from __future__ import annotations

import time
import socket
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import redis


class StatePublishError(RuntimeError):
    pass


def _ns(cfg: Dict[str, Any]) -> str:
    """
    Authoritative namespace from globals.state.namespace.
    SCHEMA_VALIDATION guarantees it exists and is non-empty.
    """
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)


def _now_ms() -> int:
    return int(time.time() * 1000)


def publish_initial_state(
    r: redis.Redis,
    cfg: Dict[str, Any],
    *,
    node_id: str = "rt-controller",
    mqtt_connected: bool = False,
    redis_connected: bool = True,
    boot_ms: int,
) -> None:
    """
    Publish a deterministic initial snapshot to Redis.

    This is intentionally:
    - write-only (no reads)
    - idempotent (safe to run repeatedly)
    - minimal (no runtime loops)
    """
    prefix = _ns(cfg)
    hostname = socket.gethostname()
    schema = cfg.get("schema") or {}
    schema_id = str(schema.get("id") or "")
    schema_version = str(schema.get("version") or "")

    pages = cfg.get("pages") if isinstance(cfg.get("pages"), list) else []
    panels = cfg.get("panels") if isinstance(cfg.get("panels"), list) else []
    services = cfg.get("services") if isinstance(cfg.get("services"), dict) else {}

    try:
        pipe = r.pipeline(transaction=False)

        # ----------------------------
        # rt:system:*
        # ----------------------------
        pipe.hset(
            _k(prefix, "system", "info"),
            mapping={
                "node_id": node_id,
                "hostname": hostname,
                "boot_ms": boot_ms,
                "schema_id": schema_id,
                "schema_version": schema_version,
                "pages_count": len(pages),
                "panels_count": len(panels),
                "services_count": len(services),
                "redis_connected": "1" if redis_connected else "0",
                "mqtt_connected": "1" if mqtt_connected else "0",
            },
        )
        pipe.set(_k(prefix, "system", "boot_ms"), boot_ms)

        # Handy indices (not required, but makes state browsable)
        pipe.sadd(_k(prefix, "system", "nodes"), node_id)
        pipe.sadd(_k(prefix, "system", "services"), *sorted(services.keys())) if services else None

        # ----------------------------
        # rt:nodes:*
        # ----------------------------
        # Minimal node record; health fields can be expanded later
        pipe.hset(
            _k(prefix, "nodes", node_id),
            mapping={
                "node_id": node_id,
                "hostname": hostname,
                "role": "controller",
                "boot_ms": boot_ms,
                "status": "up",
            },
        )

        # ----------------------------
        # rt:services:*
        # ----------------------------
        # One hash per service, plus an index set already added above.
        for sid in sorted(services.keys()):
            sobj = services.get(sid)
            if not isinstance(sobj, dict):
                continue

            # Keep this minimal and stable (don’t dump full objects yet)
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
                    "state": "unknown",   # runtime manager will set later
                    "last_update_ms": boot_ms,
                },
            )

        pipe.execute()

    except Exception as e:
        raise StatePublishError(f"Failed to publish initial state to Redis: {e}") from e
