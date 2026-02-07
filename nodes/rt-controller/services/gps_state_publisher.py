#!/usr/bin/env python3
"""
RollingThunder - GPS State Publisher (rt-controller)

Purpose:
- Publish predictable GPS keys so UI panels can bind reliably.
- Currently publishes system UTC time and placeholder GPS values.
- Later can be upgraded to GPSD / sensors without changing UI.

Keys written (hashes):
- rt:gps:time  { utc_iso, source, last_update_ms }
- rt:gps:fix   { has_fix, fix_type, sats, last_update_ms }
- rt:gps:speed { mps, mph, kph, last_update_ms }

IMPORTANT:
- This publisher intentionally does NOT write any env/temperature keys.
  Temperature should be owned by a dedicated env temp publisher (or NOAA later).
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict

import redis


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

POLL_MS = int(os.environ.get("RT_GPS_PUBLISH_INTERVAL_MS", "1000"))

KEY_GPS_TIME = os.environ.get("RT_KEY_GPS_TIME", "rt:gps:time")
KEY_GPS_FIX = os.environ.get("RT_KEY_GPS_FIX", "rt:gps:fix")
KEY_GPS_SPEED = os.environ.get("RT_KEY_GPS_SPEED", "rt:gps:speed")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso_utc() -> str:
    # Always UTC ISO-8601 with Z
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _scalarize(v: Any) -> str:
    """
    Redis hashes want bytes/str/int/float. We normalize to strings.
    Keep it boring and predictable for UI decoding.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, str)):
        return str(v)
    # Last resort: stringify
    return str(v)


def hset_dict(r: redis.Redis, key: str, fields: Dict[str, Any]) -> None:
    safe = {str(k): _scalarize(v) for k, v in fields.items()}
    r.hset(key, mapping=safe)


def main() -> None:
    interval = max(100, POLL_MS) / 1000.0

    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=1.5,
        socket_timeout=1.5,
        retry_on_timeout=True,
    )

    # If Redis is briefly down, don't spin at 100% CPU.
    backoff = 0.5

    while True:
        ts = now_ms()
        try:
            # Time (system-derived for now)
            hset_dict(
                r,
                KEY_GPS_TIME,
                {
                    "utc_iso": now_iso_utc(),
                    "source": "system",  # later: "gps"
                    "last_update_ms": ts,
                },
            )

            # GPS fix placeholder
            hset_dict(
                r,
                KEY_GPS_FIX,
                {
                    "has_fix": False,
                    "fix_type": 0,
                    "sats": 0,
                    "last_update_ms": ts,
                },
            )

            # Speed placeholder
            hset_dict(
                r,
                KEY_GPS_SPEED,
                {
                    "mps": 0.0,
                    "mph": 0.0,
                    "kph": 0.0,
                    "last_update_ms": ts,
                },
            )

            backoff = 0.5
            time.sleep(interval)

        except Exception as e:
            # Log to stderr so journald captures it.
            print(f"[gps_state_publisher] ERROR: {e}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


if __name__ == "__main__":
    main()
