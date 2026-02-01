#!/usr/bin/env python3
"""
RollingThunder - GPS/Env State Publisher (rt-controller)

Purpose:
- Seed/publish predictable GPS-ish keys so UI panels can bind reliably.
- For now uses system UTC time and placeholder GPS/temp values.
- Later this can be upgraded to read GPSD / sensors without changing the UI.

Keys written (hashes):
- rt:gps:time  { utc_iso, source, last_update_ms }
- rt:gps:fix   { has_fix, fix_type, sats, last_update_ms }
- rt:gps:speed { mps, mph, kph, last_update_ms }
- rt:env:temp  { f, c, source, last_update_ms }
"""

from __future__ import annotations

import os
import time
from typing import Dict, Any

import redis


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

POLL_MS = int(os.environ.get("RT_GPS_PUBLISH_INTERVAL_MS", "1000"))

KEY_GPS_TIME = os.environ.get("RT_KEY_GPS_TIME", "rt:gps:time")
KEY_GPS_FIX = os.environ.get("RT_KEY_GPS_FIX", "rt:gps:fix")
KEY_GPS_SPEED = os.environ.get("RT_KEY_GPS_SPEED", "rt:gps:speed")
KEY_ENV_TEMP = os.environ.get("RT_KEY_ENV_TEMP", "rt:env:temp")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hset_dict(r: redis.Redis, key: str, fields: Dict[str, Any]) -> None:
    # Redis wants stringable scalars.
    safe = {str(k): ("" if v is None else v) for k, v in fields.items()}
    r.hset(key, mapping=safe)


def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )

    interval = max(100, POLL_MS) / 1000.0

    while True:
        ts = now_ms()

        # Time (system-derived for now)
        hset_dict(r, KEY_GPS_TIME, {
            "utc_iso": now_iso_utc(),
            "source": "system",          # later: "gps"
            "last_update_ms": ts,
        })

        # GPS fix placeholder
        hset_dict(r, KEY_GPS_FIX, {
            "has_fix": "false",
            "fix_type": 0,
            "sats": 0,
            "last_update_ms": ts,
        })

        # Speed placeholder
        hset_dict(r, KEY_GPS_SPEED, {
            "mps": 0.0,
            "mph": 0.0,
            "kph": 0.0,
            "last_update_ms": ts,
        })

        # Temp placeholder (unknown)
        # Use blanks so your UI can display --°F/--°C.
        hset_dict(r, KEY_ENV_TEMP, {
            "f": "",
            "c": "",
            "source": "unknown",
            "last_update_ms": ts,
        })

        time.sleep(interval)


if __name__ == "__main__":
    main()
