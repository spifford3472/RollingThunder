#!/usr/bin/env python3
"""
RollingThunder - Env Temp Publisher (rt-controller)

Publishes ambient/board temperature into Redis:
  rt:env:temp (hash): c, f, source, last_update_ms

Input sources (best-effort):
1) 1-wire DS18B20: /sys/bus/w1/devices/28-*/w1_slave
2) CPU/SoC temp:  /sys/class/thermal/thermal_zone0/temp
"""

from __future__ import annotations

import glob
import os
import time
from typing import Optional, Tuple

import redis


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

KEY_TEMP = os.environ.get("RT_KEY_ENV_TEMP", "rt:env:temp")

INTERVAL_SEC = float(os.environ.get("RT_ENV_TEMP_INTERVAL_SEC", "5.0"))


def now_ms() -> int:
    return int(time.time() * 1000)


def read_ds18b20_c() -> Optional[float]:
    # Typical DS18B20 path: /sys/bus/w1/devices/28-xxxx/w1_slave
    paths = glob.glob("/sys/bus/w1/devices/28-*/w1_slave")
    if not paths:
        return None

    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            if len(lines) < 2:
                continue
            # Must pass CRC: "... YES"
            if not lines[0].strip().endswith("YES"):
                continue
            # Parse: "t=23125" => 23.125C
            idx = lines[1].find("t=")
            if idx < 0:
                continue
            t_milli = int(lines[1][idx + 2 :].strip())
            return t_milli / 1000.0
        except Exception:
            continue

    return None


def read_thermal_zone0_c() -> float | None:
    path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_s = f.read().strip()
        raw = int(raw_s)
        # Pi reports millidegrees C (e.g. 20927 => 20.927C)
        c = raw / 1000.0 if raw > 1000 else float(raw)
        # sanity bounds for cabin-ish temps; adjust if you want
        if c < -50.0 or c > 150.0:
            return None
        return c
    except Exception:
        return None



def read_temp_c() -> Tuple[Optional[float], str]:
    c = read_ds18b20_c()
    if isinstance(c, float):
        return c, "w1"

    c = read_thermal_zone0_c()
    if isinstance(c, float):
        return c, "thermal_zone0"

    return None, "unknown"


def c_to_f(c: float) -> float:
    return (c * 9.0 / 5.0) + 32.0


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

    # Fail fast if Redis isn’t reachable
    r.ping()

    KEY_TEMP = "rt:env:temp"

    while True:
        ts = now_ms()
        c = read_thermal_zone0_c()

        if c is not None:
            f = (c * 9.0 / 5.0) + 32.0
            r.hset(KEY_TEMP, mapping={
                "c": round(c, 1),
                "f": round(f, 1),
                "source": "thermal_zone0",
                "stale": "0",
                "last_update_ms": ts,
            })
        else:
            # do NOT blank c/f; just mark stale and update timestamp
            r.hset(KEY_TEMP, mapping={
                "source": "unknown",
                "stale": "1",
                "last_update_ms": ts,
            })

        time.sleep(1)

        # Store as hash for ui/state/batch "hash" encoding
        #r.hset(KEY_TEMP, mapping={k: str(v) for k, v in payload.items()})

        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
