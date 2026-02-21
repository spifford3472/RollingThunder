#!/usr/bin/env python3
"""
RollingThunder - GPS State Publisher (rt-controller)

Green-path goals:
- Publish GPS-derived UTC time as soon as fix_type >= 1 (time-only), even before location lock.
- Keep UI read-only. UI binds to Redis state only.
- Restart-safe, bounded, and resilient to gpsd/device loss (clean fallback to system time).

Keys written (hashes):
- rt:gps:time  { utc_iso, source, last_update_ms, gps_last_seen_ms }
- rt:gps:fix   { has_fix, fix_type, sats, source, last_update_ms, gps_last_seen_ms }
- rt:gps:speed { mps, mph, kph, last_update_ms, gps_last_seen_ms }

Notes:
- last_update_ms is publisher heartbeat (freshness of publisher).
- gps_last_seen_ms is "last time we got real gpsd data" (freshness of GPS feed).
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Dict, Optional, Tuple

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

POLL_MS = int(os.environ.get("RT_GPS_PUBLISH_INTERVAL_MS", "1000"))

KEY_GPS_TIME = os.environ.get("RT_KEY_GPS_TIME", "rt:gps:time")
KEY_GPS_FIX = os.environ.get("RT_KEY_GPS_FIX", "rt:gps:fix")
KEY_GPS_SPEED = os.environ.get("RT_KEY_GPS_SPEED", "rt:gps:speed")

GPSD_HOST = os.environ.get("RT_GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.environ.get("RT_GPSD_PORT", "2947"))
GPSD_TIMEOUT_S = float(os.environ.get("RT_GPSD_TIMEOUT_S", "1.0"))

# If you want to hard-disable gpsd reads for testing:
GPSD_ENABLED = os.environ.get("RT_GPSD_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _scalarize(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, str)):
        return str(v)
    return str(v)


def hset_dict(r: redis.Redis, key: str, fields: Dict[str, Any]) -> None:
    safe = {str(k): _scalarize(v) for k, v in fields.items()}
    r.hset(key, mapping=safe)


def _gpsd_connect() -> socket.socket:
    s = socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=GPSD_TIMEOUT_S)
    s.settimeout(GPSD_TIMEOUT_S)
    return s


def _gpsd_watch(sock: socket.socket) -> None:
    # Ask for JSON streaming; gpsd will send \n-delimited JSON objects.
    cmd = '?WATCH={"enable":true,"json":true};\n'
    sock.sendall(cmd.encode("ascii", errors="ignore"))


def _readline(sock: socket.socket, buf: bytearray) -> Optional[bytes]:
    """
    Read a single '\n' terminated line from gpsd, bounded by timeouts.
    Returns the line bytes (without guaranteeing validity) or None on timeout.
    """
    deadline = time.time() + GPSD_TIMEOUT_S
    while time.time() < deadline:
        # Do we already have a full line?
        nl = buf.find(b"\n")
        if nl != -1:
            line = bytes(buf[:nl])
            del buf[: nl + 1]
            return line

        try:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf.extend(chunk)
        except socket.timeout:
            return None
        except OSError:
            return None
    return None


def _parse_gpsd_time_to_iso(t: Any) -> Optional[str]:
    """
    gpsd TPV.time is usually ISO-8601 like '2026-02-20T02:34:56.000Z'
    Normalize to '...Z' (keep milliseconds if present; UI parseIso can handle it).
    """
    if not isinstance(t, str) or not t:
        return None
    # Minimal sanity: must contain 'T' and end with 'Z' or have timezone.
    if "T" not in t:
        return None
    # If it has offset, Date() can parse it too, but we prefer Z.
    return t


def _gpsd_sample() -> Optional[Dict[str, Any]]:
    """
    Try to obtain a recent TPV and SKY snapshot from gpsd quickly.
    Returns dict with: fix_type, utc_iso, speed_mps, sats_used, lat, lon, alt (optional)
    or None if unavailable.
    """
    if not GPSD_ENABLED:
        return None

    sock: Optional[socket.socket] = None
    buf = bytearray()
    try:
        sock = _gpsd_connect()
        _gpsd_watch(sock)

        tpv: Optional[dict] = None
        sky: Optional[dict] = None

        # Bounded loop: read up to N lines, then give up for this tick.
        for _ in range(20):
            line = _readline(sock, buf)
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", errors="ignore"))
            except Exception:
                continue

            cls = msg.get("class")
            if cls == "TPV":
                tpv = msg
            elif cls == "SKY":
                sky = msg

            # As soon as we have TPV, we can proceed; SKY is "nice to have".
            if tpv is not None and (sky is not None or _ >= 3):
                break

        if not tpv:
            return None

        mode = tpv.get("mode")  # 0/1/2/3
        try:
            fix_type = int(mode) if mode is not None else 0
        except Exception:
            fix_type = 0

        utc_iso = _parse_gpsd_time_to_iso(tpv.get("time"))
        speed_mps = tpv.get("speed")

        # Satellites used: gpsd SKY has "uSat" (used) or we can count those with "used": true
        sats_used = 0
        if isinstance(sky, dict):
            if isinstance(sky.get("uSat"), (int, float)):
                sats_used = int(sky["uSat"])
            else:
                sats = sky.get("satellites")
                if isinstance(sats, list):
                    sats_used = sum(1 for s in sats if isinstance(s, dict) and s.get("used") is True)

        out: Dict[str, Any] = {
            "fix_type": fix_type,
            "utc_iso": utc_iso,
            "speed_mps": float(speed_mps) if isinstance(speed_mps, (int, float)) else None,
            "sats_used": sats_used,
            # Optional, cheap if present (can be used later without schema change)
            "lat": tpv.get("lat"),
            "lon": tpv.get("lon"),
            "alt": tpv.get("alt"),
        }
        return out

    except Exception:
        return None
    finally:
        try:
            if sock:
                sock.close()
        except Exception:
            pass


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

    backoff = 0.5
    gps_last_seen_ms: int = 0  # when we last got real gpsd data (time/fix/speed)

    while True:
        ts = now_ms()
        try:
            sample = _gpsd_sample()

            # Defaults: safe fallback
            fix_type = 0
            sats = 0
            has_fix = False
            clock_source = "system"
            utc_iso = now_iso_utc()

            speed_mps = 0.0

            if sample:
                ft = sample.get("fix_type")
                if isinstance(ft, int):
                    fix_type = ft

                # Time is authoritative as soon as fix_type >= 1 *and* gpsd provided time
                gps_time = sample.get("utc_iso")
                if fix_type >= 1 and isinstance(gps_time, str) and gps_time:
                    utc_iso = gps_time
                    clock_source = "gpsd"
                    gps_last_seen_ms = ts  # we saw real GPS time

                # Location fix truth is fix_type >= 2
                has_fix = fix_type >= 2

                su = sample.get("sats_used")
                if isinstance(su, int) and su >= 0:
                    sats = su

                sm = sample.get("speed_mps")
                if isinstance(sm, (int, float)) and sm >= 0:
                    speed_mps = float(sm)
                    # speed is also "real gpsd data"
                    gps_last_seen_ms = max(gps_last_seen_ms, ts)

            mph = speed_mps * 2.2369362920544
            kph = speed_mps * 3.6

            # Publish time
            hset_dict(
                r,
                KEY_GPS_TIME,
                {
                    "utc_iso": utc_iso,
                    "source": clock_source if clock_source != "gpsd" else "gps",
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                },
            )

            # Publish fix
            hset_dict(
                r,
                KEY_GPS_FIX,
                {
                    "has_fix": has_fix,
                    "fix_type": fix_type,
                    "sats": sats,
                    "source": "gpsd" if sample else "system",
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                },
            )

            # Publish speed
            hset_dict(
                r,
                KEY_GPS_SPEED,
                {
                    "mps": round(speed_mps, 3),
                    "mph": round(mph, 3),
                    "kph": round(kph, 3),
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                },
            )

            backoff = 0.5
            time.sleep(interval)

        except Exception as e:
            print(f"[gps_state_publisher] ERROR: {e}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


if __name__ == "__main__":
    main()