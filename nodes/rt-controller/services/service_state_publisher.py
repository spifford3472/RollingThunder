#!/usr/bin/env python3
"""
service_state_publisher.py — RollingThunder (rt-controller)

Phase 13:
- Poll systemd for local service runtime state (rt-controller-owned services)
- Publish into Redis hashes rt:services:<service_id>:
    - state
    - last_update_ms

Constraints:
- Read-only for systemd; only Redis updates.
- No schema changes; only populates existing fields already present.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Dict, Optional, Tuple

import redis


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")
LOCAL_NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

POLL_SEC = float(os.environ.get("RT_POLL_SEC", "5.0"))

# Mapping from service id -> systemd unit name on this node.
# Keep this small and explicit. Add entries as you bring up more units.
DEFAULT_UNIT_MAP: Dict[str, str] = {
    # Core controller plumbing (adjust names to match your actual unit files)
    "mqtt_bus": "rt-mqtt-bus.service",
    "logging": "rt-logging.service",
    "node_health": "rt-node-health.service",
    "redis_state": "rt-redis-state.service",
    "gps_ingest": "rt-gps-ingest.service",
    "noaa_same": "rt-noaa-same.service",
    "meshtastic_c2": "rt-meshtastic-c2.service",
    # Snapshot API itself (optional, but useful)
    "ui_snapshot_api": "rt-ui-snapshot-api.service",
}

# Optional override via env var containing JSON dict: {"service_id":"unit.service", ...}
UNIT_MAP_JSON = os.environ.get("RT_UNIT_MAP_JSON", "")

def set_error(r: redis.Redis, key: str, msg: str) -> None:
    try:
        r.hset(key, mapping={
            "publisher_error": msg[:300],
            "last_update_ms": str(now_ms()),
        })
    except Exception:
        pass

def now_ms() -> int:
    return int(time.time() * 1000)


def load_unit_map() -> Dict[str, str]:
    m = dict(DEFAULT_UNIT_MAP)
    if UNIT_MAP_JSON.strip():
        try:
            extra = json.loads(UNIT_MAP_JSON)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if isinstance(k, str) and isinstance(v, str) and v.endswith(".service"):
                        m[k] = v
        except Exception:
            pass
    return m


def run_systemctl_show(unit: str) -> Optional[Dict[str, str]]:
    """
    Returns dict with ActiveState/SubState/MainPID if unit exists; else None.
    """
    try:
        # systemctl show outputs key=value lines
        out = subprocess.check_output(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "--no-pager"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1.5,
        )
        d: Dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        # If systemd doesn't know the unit, ActiveState is often "inactive" but MainPID=0.
        # We'll check existence separately via 'systemctl status' would be slower; instead:
        # systemctl show returns "LoadState=not-found" if asked; but we didn't request it.
        # So do a lightweight existence probe:
        out2 = subprocess.check_output(
            ["systemctl", "show", unit, "-p", "LoadState", "--no-pager"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1.5,
        )
        load_state = out2.strip().split("=", 1)[-1].strip() if "=" in out2 else out2.strip()
        if load_state == "not-found":
            return None
        d["LoadState"] = load_state
        return d
    except Exception:
        return None


def normalize_state(info: Optional[Dict[str, str]]) -> str:
    if not info:
        return "missing"
    active = info.get("ActiveState", "")
    sub = info.get("SubState", "")
    if active == "active":
        return "running"
    if active == "inactive":
        return "stopped"
    if active == "failed":
        return "failed"
    # other possibilities: activating/deactivating/reloading
    if active:
        return active if not sub else f"{active}:{sub}"
    return "unknown"


def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
    )

    unit_map = load_unit_map()

    while True:
        start = time.time()
        try:
            r.ping()
        except Exception:
            time.sleep(POLL_SEC)
            continue

        # Scan existing service hashes
        for key in r.scan_iter(match=f"{SERVICE_PREFIX}*"):
            try:
                if r.type(key) != "hash":
                    continue

                h = r.hgetall(key)
                sid = (h.get("id") or key.split(":", 2)[-1]).strip()
                owner = (h.get("ownerNode") or "").strip()

                # Only update services owned by this node
                if owner != LOCAL_NODE_ID:
                    continue

                unit = unit_map.get(sid)
                if not unit:
                    r.hset(key, mapping={
                        "state": "unknown",
                        "last_update_ms": str(now_ms()),
                    })
                    r.hdel(key, "publisher_error")
                    continue


                info = run_systemctl_show(unit)
                state = normalize_state(info)

                mapping = {
                    "state": state,
                    "last_update_ms": str(now_ms()),
                }

                # If we have a unit mapping but systemd can't find it, record it.
                if state == "missing":
                    mapping["publisher_error"] = f"mapped_unit_missing: {unit}"
                else:
                    # Clear any previous error on a healthy observation
                    r.hdel(key, "publisher_error")

                r.hset(key, mapping=mapping)


            except Exception as e:
                set_error(r, key, f"{type(e).__name__}: {e}")
                continue


        elapsed = time.time() - start
        sleep_for = max(0.2, POLL_SEC - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
