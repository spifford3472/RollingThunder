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

RT_UNIT_PREFIX = os.environ.get("RT_UNIT_PREFIX", "rt-")          # systemd unit prefix
RT_UNIT_SUFFIX = os.environ.get("RT_UNIT_SUFFIX", ".service")     # only services
RT_EXCLUDE_AT = os.environ.get("RT_EXCLUDE_AT", "1") == "1"       # exclude '@' templates/instances
RT_PRUNE_MISSING = os.environ.get("RT_PRUNE_MISSING", "1") == "1" # delete stale rt:services:* keys


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")
LOCAL_NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

POLL_SEC = float(os.environ.get("RT_POLL_SEC", "5.0"))
POLL_SEC = min(POLL_SEC, 60.0)  # must check at least every 60 seconds

DISCOVER_SEC = float(os.environ.get("RT_DISCOVER_SEC", "30.0"))
DISCOVER_SEC = min(DISCOVER_SEC, 60.0)  # redis keys must adapt within 60 seconds

UI_BUS_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")


DEFAULT_UNIT_MAP: Dict[str, str] = {
    # These IDs already exist in Redis (rt:services:<id>) and are owned by rt-controller
    "mqtt_bus": "mosquitto.service",
    "redis_state": "redis-server.service",
    "gps_ingest": "rt-gps-state-publisher.service",

    # These exist in Redis but you don't currently have matching systemd units on rt-controller.
    # Leave them unmapped for now (recommended), or map them once the units exist.
    # "logging": ???,
    # "node_health": ???,
    # "meshtastic_c2": ???,
    # "noaa_same": ???,
}

def unit_exists(unit: str) -> bool:
    info = run_systemctl_show(unit)
    return bool(info) and info.get("LoadState") != "not-found"

def _unit_to_service_id(unit: str) -> str:
    # rt-gps-state-publisher.service -> gps_state_publisher
    name = unit
    if name.startswith(RT_UNIT_PREFIX):
        name = name[len(RT_UNIT_PREFIX):]
    if name.endswith(RT_UNIT_SUFFIX):
        name = name[: -len(RT_UNIT_SUFFIX)]
    return name.replace("-", "_")

def discover_rt_units() -> list[str]:
    """
    Return list of systemd unit names like: rt-foo.service
    Excludes '@' templates/instances if RT_EXCLUDE_AT=1.
    """
    cmd = [
        "systemctl",
        "list-units",
        "--type=service",
        "--all",
        "--no-legend",
        "--no-pager",
    ]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)

    units: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # first column is UNIT
        unit = line.split(None, 1)[0].strip()
        if not unit.startswith(RT_UNIT_PREFIX):
            continue
        if not unit.endswith(RT_UNIT_SUFFIX):
            continue
        if RT_EXCLUDE_AT and "@" in unit:
            continue
        units.append(unit)

    units.sort()
    return units

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
    unit_map = load_unit_map()  # keep for non-rt services like redis/mosquitto
    last_discover = 0.0
    discovered_services: Dict[str, str] = {}  # sid -> unit

    while True:
        start = time.time()
        try:
            r.ping()
        except Exception:
            time.sleep(POLL_SEC)
            continue

        now_t = time.time()

        # ---- (A) Redis+systemd truth refresh: discover rt-* services at least every DISCOVER_SEC ----
        if (now_t - last_discover) >= DISCOVER_SEC:
            last_discover = now_t
            discovered_services = {}

            # 1) Discover all rt-*.service units (excluding @)
            try:
                rt_units = discover_rt_units()
                for unit in rt_units:
                    sid = _unit_to_service_id(unit)
                    discovered_services[sid] = unit
            except Exception:
                # If discovery fails, keep last known discovered_services (don’t nuke state)
                pass

            # 2) OPTIONAL: include mapped “non-rt” services ONLY if they exist
            # This keeps mqtt_bus / redis_state if you still want them visible.
            for sid, unit in unit_map.items():
                # only include if systemd actually knows the unit
                if unit and unit_exists(unit):
                    discovered_services.setdefault(sid, unit)

            # 3) Prune rt:services:* keys that aren’t in discovered_services
            if RT_PRUNE_MISSING:
                try:
                    keep = set(discovered_services.keys())
                    for key in r.scan_iter(match=f"{SERVICE_PREFIX}*"):
                        if r.type(key) != "hash":
                            continue
                        sid = key.split(":", 2)[-1]
                        if sid not in keep:
                            r.delete(key)
                except Exception:
                    pass

        # ---- (B) Publish state for everything in discovered_services ----
        for sid, unit in discovered_services.items():
            key = f"{SERVICE_PREFIX}{sid}"

            try:
                info = run_systemctl_show(unit)
                state = normalize_state(info)

                mapping = {
                    "id": sid,                       # safe; helps when hashes are newly created
                    "ownerNode": LOCAL_NODE_ID,      # safe; allows your UI to filter by node
                    "state": state,
                    "last_update_ms": str(now_ms()),
                }

                if state == "missing":
                    mapping["publisher_error"] = f"unit_missing: {unit}"
                else:
                    # clear any previous error
                    try:
                        r.hdel(key, "publisher_error")
                    except Exception:
                        pass

                # Read prev state for eventing
                prev = {}
                try:
                    if r.type(key) == "hash":
                        prev = r.hgetall(key)
                except Exception:
                    prev = {}

                prev_state = (prev.get("state") or "").strip()
                prev_error = (prev.get("publisher_error") or "").strip()
                new_error = (mapping.get("publisher_error") or "").strip()

                state_changed = (state != prev_state)
                error_changed = (new_error != prev_error)

                r.hset(key, mapping=mapping)

                if state_changed or error_changed:
                    evt = {
                        "topic": "state.changed",
                        "payload": {"keys": [key]},
                        "ts_ms": now_ms(),
                        "source": "service_state_publisher",
                    }
                    try:
                        r.publish(UI_BUS_CHANNEL, json.dumps(evt, separators=(",", ":")))
                    except Exception:
                        pass

            except Exception as e:
                set_error(r, key, f"{type(e).__name__}: {e}")
                continue

        elapsed = time.time() - start
        time.sleep(max(0.2, POLL_SEC - elapsed))


if __name__ == "__main__":
    main()
