#!/usr/bin/env python3
"""
service_state_publisher.py — RollingThunder

In-memory state publisher:
- Polls local systemd service state.
- Keeps previous state in memory.
- Writes Redis only when state/error changes.
- Does not use Redis reads for normal change detection.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Dict, Optional

import redis

RT_UNIT_PREFIX = os.environ.get("RT_UNIT_PREFIX", "rt-")
RT_UNIT_SUFFIX = os.environ.get("RT_UNIT_SUFFIX", ".service")
RT_EXCLUDE_AT = os.environ.get("RT_EXCLUDE_AT", "1") == "1"
RT_PRUNE_MISSING = os.environ.get("RT_PRUNE_MISSING", "1") == "1"

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")
LOCAL_NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

POLL_SEC = min(float(os.environ.get("RT_POLL_SEC", "5.0")), 60.0)
DISCOVER_SEC = min(float(os.environ.get("RT_DISCOVER_SEC", "30.0")), 60.0)

SYSTEM_BUS_CHANNEL = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")

DEFAULT_UNIT_MAP: Dict[str, str] = {
    "mqtt_bus": "mosquitto.service",
    "redis_state": "redis-server.service",
    "gps_ingest": "rt-gps-state-publisher.service",
}

UNIT_MAP_JSON = os.environ.get("RT_UNIT_MAP_JSON", "")


def now_ms() -> int:
    return int(time.time() * 1000)


def run_systemctl_show(unit: str) -> Optional[Dict[str, str]]:
    try:
        out = subprocess.check_output(
            [
                "systemctl",
                "show",
                unit,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "-p",
                "LoadState",
                "--no-pager",
            ],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1.5,
        )

        d: Dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()

        if d.get("LoadState") == "not-found":
            return None

        return d
    except Exception:
        return None


def unit_exists(unit: str) -> bool:
    info = run_systemctl_show(unit)
    return bool(info) and info.get("LoadState") != "not-found"


def _unit_to_service_id(unit: str) -> str:
    name = unit

    if name.startswith(RT_UNIT_PREFIX):
        name = name[len(RT_UNIT_PREFIX):]

    if name.endswith(RT_UNIT_SUFFIX):
        name = name[: -len(RT_UNIT_SUFFIX)]

    return name.replace("-", "_")


def discover_rt_units() -> list[str]:
    out = subprocess.check_output(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--all",
            "--no-legend",
            "--no-pager",
        ],
        stderr=subprocess.STDOUT,
        text=True,
        timeout=3.0,
    )

    units: list[str] = []

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue

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


def load_unit_map() -> Dict[str, str]:
    m = dict(DEFAULT_UNIT_MAP) if LOCAL_NODE_ID == "rt-controller" else {}

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

    if active:
        return active if not sub else f"{active}:{sub}"

    return "unknown"


def publish_state_changed(r: redis.Redis, key: str) -> None:
    evt = {
        "topic": "state.changed",
        "payload": {"keys": [key]},
        "ts_ms": now_ms(),
        "source": "service_state_publisher",
    }

    try:
        r.publish(SYSTEM_BUS_CHANNEL, json.dumps(evt, separators=(",", ":")))
    except Exception:
        pass


def write_service_state(
    r: redis.Redis,
    sid: str,
    unit: str,
    state: str,
    error: str,
) -> None:
    key = f"{SERVICE_PREFIX}{sid}"

    mapping = {
        "id": sid,
        "ownerNode": LOCAL_NODE_ID,
        "unit": unit,
        "state": state,
        "last_update_ms": str(now_ms()),
    }

    if error:
        mapping["publisher_error"] = error

    r.hset(key, mapping=mapping)

    if not error:
        r.hdel(key, "publisher_error")

    publish_state_changed(r, key)


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

    discovered_services: Dict[str, str] = {}
    last_discover = 0.0

    last_state: Dict[str, str] = {}
    last_error: Dict[str, str] = {}

    while True:
        start = time.time()

        try:
            r.ping()
        except Exception:
            time.sleep(POLL_SEC)
            continue

        now_t = time.time()

        if (now_t - last_discover) >= DISCOVER_SEC:
            last_discover = now_t

            next_discovered: Dict[str, str] = {}

            try:
                for unit in discover_rt_units():
                    sid = _unit_to_service_id(unit)
                    next_discovered[sid] = unit
            except Exception:
                next_discovered = dict(discovered_services)

            for sid, unit in unit_map.items():
                if unit and unit_exists(unit):
                    next_discovered.setdefault(sid, unit)

            removed = set(discovered_services.keys()) - set(next_discovered.keys())

            discovered_services = next_discovered

            if RT_PRUNE_MISSING:
                for sid in removed:
                    key = f"{SERVICE_PREFIX}{sid}"
                    try:
                        r.delete(key)
                        publish_state_changed(r, key)
                    except Exception:
                        pass

                    last_state.pop(sid, None)
                    last_error.pop(sid, None)

        for sid, unit in discovered_services.items():
            try:
                info = run_systemctl_show(unit)
                state = normalize_state(info)
                error = f"unit_missing: {unit}" if state == "missing" else ""

                prev_state = last_state.get(sid)
                prev_error = last_error.get(sid)

                if state != prev_state or error != prev_error:
                    write_service_state(r, sid, unit, state, error)
                    last_state[sid] = state
                    last_error[sid] = error

            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                prev_error = last_error.get(sid)

                if error != prev_error:
                    try:
                        write_service_state(r, sid, unit, "unknown", error)
                    except Exception:
                        pass

                    last_state[sid] = "unknown"
                    last_error[sid] = error

        elapsed = time.time() - start
        time.sleep(max(0.2, POLL_SEC - elapsed))


if __name__ == "__main__":
    main()