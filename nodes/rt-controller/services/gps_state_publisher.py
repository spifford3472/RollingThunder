#!/usr/bin/env python3
"""
RollingThunder - GPS State Publisher (rt-controller)

Long-term "GPS oracle" implementation:
- Persistent gpsd connection (WATCH JSON)
- Cache last TPV + SKY in memory
- Publish snapshots to Redis on a fixed cadence
- Deterministic behavior when GPS is lost (underground, unplugged, gpsd down)
- Provides time, fix, speed, and position/nav (lat/lon/alt/track/grid)

Redis hashes (authoritative):
- rt:gps:time   { utc_iso, source, last_update_ms, gps_last_seen_ms }
- rt:gps:fix    { has_fix, fix_type, sats, source, last_update_ms, gps_last_seen_ms }
- rt:gps:speed  { mps, mph, kph, last_update_ms, gps_last_seen_ms }
- rt:gps:pos    { valid, lat, lon, alt_m, alt_ft, track_deg, track_cardinal, grid4, grid6,
                  last_update_ms, gps_last_seen_ms, pos_last_good_ms }

Semantics:
- fix_type:
    0 = no fix / unknown
    1 = time-only / searching (time may be valid)
    2 = 2D fix (lat/lon)
    3 = 3D fix (lat/lon/alt)
- has_fix = (fix_type >= 2)
- Time is "GPS" as soon as fix_type >= 1 AND gpsd provides TPV.time.
- Position is valid only when fix_type >= 2 and TPV is fresh.
- Speed:
    mph is clamped: mph < 2 -> 0
    If TPV is stale, speed reports 0 (avoid stale-motion lies).
- Direction and Maidenhead:
    last known values are retained when fix is lost; never empty after first publish.

Restart safety:
- Service starts and publishes immediately (system time + no-fix).
- If gpsd drops/hangs, reconnect with bounded backoff.
"""

from __future__ import annotations

import os
import time
import threading
from typing import Any, Dict, Optional, Tuple

import redis
import gps  # gpsd python bindings (from gpsd package)


# -------------------- Config --------------------
REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

POLL_MS = int(os.environ.get("RT_GPS_PUBLISH_INTERVAL_MS", "1000"))
POLL_MS = max(200, POLL_MS)

KEY_GPS_TIME = os.environ.get("RT_KEY_GPS_TIME", "rt:gps:time")
KEY_GPS_FIX = os.environ.get("RT_KEY_GPS_FIX", "rt:gps:fix")
KEY_GPS_SPEED = os.environ.get("RT_KEY_GPS_SPEED", "rt:gps:speed")
KEY_GPS_POS = os.environ.get("RT_KEY_GPS_POS", "rt:gps:pos")

GPSD_HOST = os.environ.get("RT_GPSD_HOST", "127.0.0.1")
GPSD_PORT = os.environ.get("RT_GPSD_PORT", "2947")

# Freshness thresholds (milliseconds)
TPV_STALE_MS = int(os.environ.get("RT_GPS_TPV_STALE_MS", "3000"))     # TPV is chatty
SKY_STALE_MS = int(os.environ.get("RT_GPS_SKY_STALE_MS", "15000"))   # SKY is slower

# Internal safety: if publish loop is blocked too long, exit so systemd restarts us.
HANG_EXIT_SEC = float(os.environ.get("RT_GPS_HANG_EXIT_SEC", "30"))


# -------------------- Helpers --------------------
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


def num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def clamp_mph(mph: float) -> float:
    return 0.0 if mph < 2.0 else mph


def cardinal_from_deg(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    d = deg % 360.0
    idx = int((d + 22.5) / 45.0) % 8
    return dirs[idx]


def maidenhead(lat: float, lon: float, precision: int = 6) -> str:
    """
    Convert lat/lon to Maidenhead locator.
    precision must be 4 or 6 (EM79 or EM79xm).
    """
    if precision not in (4, 6):
        raise ValueError("precision must be 4 or 6")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon out of range")

    A = lon + 180.0
    B = lat + 90.0

    field_lon = int(A / 20)
    field_lat = int(B / 10)
    a = chr(ord("A") + field_lon)
    b = chr(ord("A") + field_lat)

    A -= field_lon * 20
    B -= field_lat * 10
    square_lon = int(A / 2)
    square_lat = int(B / 1)
    c = str(square_lon)
    d = str(square_lat)

    if precision == 4:
        return f"{a}{b}{c}{d}"

    A -= square_lon * 2
    B -= square_lat * 1
    subs_lon = int(A * 24 / 2)
    subs_lat = int(B * 24 / 1)
    e = chr(ord("a") + subs_lon)
    f = chr(ord("a") + subs_lat)

    return f"{a}{b}{c}{d}{e}{f}"


# -------------------- GPSD reader thread --------------------
class GpsCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tpv: Optional[Dict[str, Any]] = None
        self.sky: Optional[Dict[str, Any]] = None
        self.tpv_ms: int = 0
        self.sky_ms: int = 0
        self.connected: bool = False

    def update_tpv(self, d: Dict[str, Any], ts_ms: int) -> None:
        with self.lock:
            self.tpv = d
            self.tpv_ms = ts_ms

    def update_sky(self, d: Dict[str, Any], ts_ms: int) -> None:
        with self.lock:
            self.sky = d
            self.sky_ms = ts_ms

    def set_connected(self, v: bool) -> None:
        with self.lock:
            self.connected = v

    def snapshot(self) -> Tuple[Optional[Dict[str, Any]], int, Optional[Dict[str, Any]], int, bool]:
        with self.lock:
            return self.tpv, self.tpv_ms, self.sky, self.sky_ms, self.connected


def gpsd_reader(cache: GpsCache, stop: threading.Event) -> None:
    """
    Persistent gpsd session reader. Reconnects on failure with bounded backoff.
    """
    backoff = 0.5
    while not stop.is_set():
        sess = None
        try:
            cache.set_connected(False)

            # gps.gps() supports host/port
            sess = gps.gps(host=GPSD_HOST, port=GPSD_PORT)
            # enable JSON watch
            sess.stream(gps.WATCH_ENABLE | gps.WATCH_JSON)

            # Set socket timeout so we can notice stop events and reconnect cleanly.
            try:
                sess.sock.settimeout(2.0)  # type: ignore[attr-defined]
            except Exception:
                pass

            cache.set_connected(True)
            backoff = 0.5

            while not stop.is_set():
                try:
                    report = sess.next()
                except StopIteration:
                    raise OSError("gpsd stream ended")
                except Exception as e:
                    # timeout or socket errors -> reconnect
                    raise OSError(f"gpsd read error: {e}")

                if not isinstance(report, dict):
                    continue

                cls = report.get("class")
                ts = now_ms()
                if cls == "TPV":
                    cache.update_tpv(report, ts)
                elif cls == "SKY":
                    cache.update_sky(report, ts)

        except Exception as e:
            cache.set_connected(False)
            print(f"[gps_state_publisher] gpsd_reader reconnecting: {e}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
        finally:
            try:
                if sess is not None:
                    sess.close()
            except Exception:
                pass


# -------------------- Main publish loop --------------------
def main() -> None:
    interval_s = POLL_MS / 1000.0

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

    cache = GpsCache()
    stop = threading.Event()
    t = threading.Thread(target=gpsd_reader, args=(cache, stop), daemon=True)
    t.start()

    # Last-known derived state (prevents oscillation when SKY/TPV arrives at different rates)
    last_sats_used = 0
    last_track_deg = 0.0
    last_track_cardinal = "N"
    last_grid4 = ""
    last_grid6 = ""
    pos_last_good_ms = 0
    gps_last_seen_ms = 0  # last time we saw *fresh* TPV

    last_loop_progress = time.time()

    while True:
        loop_start = time.time()
        ts = now_ms()

        # Detect a true hang (should never happen, but if it does, exit so systemd restarts us)
        if (loop_start - last_loop_progress) > HANG_EXIT_SEC:
            raise SystemExit(f"gps_state_publisher hung for > {HANG_EXIT_SEC}s; exiting for restart")

        try:
            tpv, tpv_ms, sky, sky_ms, connected = cache.snapshot()

            tpv_age = (ts - tpv_ms) if tpv_ms else 10_000_000
            sky_age = (ts - sky_ms) if sky_ms else 10_000_000

            tpv_fresh = tpv is not None and tpv_ms > 0 and tpv_age <= TPV_STALE_MS
            sky_fresh = sky is not None and sky_ms > 0 and sky_age <= SKY_STALE_MS

            # Derive fix_type
            fix_type = 0
            if tpv_fresh:
                try:
                    fix_type = int(tpv.get("mode") or 0)  # type: ignore[union-attr]
                except Exception:
                    fix_type = 0

            has_fix = fix_type >= 2

            # Satellite used count
            sats_used = last_sats_used
            if sky_fresh and isinstance(sky, dict):
                # gpsd SKY typically has "uSat" (used satellites)
                if isinstance(sky.get("uSat"), (int, float)):
                    sats_used = int(sky["uSat"])
                else:
                    sats = sky.get("satellites")
                    if isinstance(sats, list):
                        sats_used = sum(
                            1 for s in sats
                            if isinstance(s, dict) and s.get("used") is True
                        )
            last_sats_used = sats_used

            # Time
            time_source = "system"
            utc_iso = now_iso_utc()
            if tpv_fresh and fix_type >= 1 and isinstance(tpv, dict):
                t_iso = tpv.get("time")
                if isinstance(t_iso, str) and t_iso.strip():
                    # gpsd usually gives ISO ending in Z; keep as-is (UI Date() parses it fine)
                    utc_iso = t_iso.strip()
                    time_source = "gps"

            if tpv_fresh:
                gps_last_seen_ms = ts

            # Speed (m/s -> mph/kph), clamp mph < 2 to 0
            speed_mps = 0.0
            if tpv_fresh and isinstance(tpv, dict):
                sm = num(tpv.get("speed"))
                if sm is not None and sm >= 0:
                    speed_mps = float(sm)

            mph = clamp_mph(speed_mps * 2.2369362920544)
            kph = speed_mps * 3.6

            # Position/nav
            valid_pos = False
            lat = None
            lon = None
            alt_m = None

            if tpv_fresh and has_fix and isinstance(tpv, dict):
                la = num(tpv.get("lat"))
                lo = num(tpv.get("lon"))
                if la is not None and lo is not None:
                    lat = la
                    lon = lo
                    valid_pos = True
                    pos_last_good_ms = ts

                    if fix_type >= 3:
                        am = num(tpv.get("alt"))
                        if am is not None:
                            alt_m = am

                    # Track/course (degrees). Keep last known if missing.
                    tr = num(tpv.get("track"))
                    if tr is not None:
                        last_track_deg = float(tr) % 360.0
                        last_track_cardinal = cardinal_from_deg(last_track_deg)

                    # Maidenhead grids (keep last known if compute fails)
                    try:
                        last_grid4 = maidenhead(lat, lon, 4)
                        last_grid6 = maidenhead(lat, lon, 6)
                    except Exception:
                        pass

            # Always publish heartbeat, even if gpsd is down.
            hset_dict(
                r,
                KEY_GPS_TIME,
                {
                    "utc_iso": utc_iso,
                    "source": time_source,
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                },
            )

            hset_dict(
                r,
                KEY_GPS_FIX,
                {
                    "has_fix": has_fix,
                    "fix_type": fix_type,
                    "sats": sats_used,
                    "source": "gpsd" if connected else "system",
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                },
            )

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

            alt_ft = (alt_m * 3.280839895013123) if isinstance(alt_m, (int, float)) else None

            hset_dict(
                r,
                KEY_GPS_POS,
                {
                    "valid": valid_pos,
                    "lat": lat if valid_pos else "",
                    "lon": lon if valid_pos else "",
                    "alt_m": alt_m if alt_m is not None else "",
                    "alt_ft": alt_ft if alt_ft is not None else "",
                    "track_deg": round(last_track_deg, 1),
                    "track_cardinal": last_track_cardinal or "N",
                    "grid4": last_grid4,
                    "grid6": last_grid6,
                    "last_update_ms": ts,
                    "gps_last_seen_ms": gps_last_seen_ms or "",
                    "pos_last_good_ms": pos_last_good_ms or "",
                },
            )

            last_loop_progress = time.time()

        except Exception as e:
            # Log and continue with backoff so we don't spin if Redis is down.
            print(f"[gps_state_publisher] ERROR: {type(e).__name__}: {e}", flush=True)
            time.sleep(0.5)

        # Maintain cadence
        elapsed = time.time() - loop_start
        sleep_s = interval_s - elapsed
        if sleep_s > 0:
            time.sleep(sleep_s)


if __name__ == "__main__":
    main()