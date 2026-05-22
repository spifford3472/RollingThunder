#!/usr/bin/env python3
"""
RollingThunder RF Intel Mobile Advisor Mode Detector

Controller-side only.

Reads:
  - rt:gps:pos

Writes:
  - rt:rfintel:mobile
  - rt:rfintel:mobile:state

Publishes:
  - state.changed to rt:system:bus when rt:rfintel:mobile semantically changes

Does not:
  - write to rt:ui:bus
  - write to rt:ui:intents
  - scan Redis
  - read UI state
  - call browser APIs
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import redis
except ImportError:
    print("ERROR: python3 redis module is required", file=sys.stderr)
    sys.exit(2)


KEY_GPS = "rt:gps:pos"
KEY_OUT = "rt:rfintel:mobile"
KEY_STATE = "rt:rfintel:mobile:state"
SYSTEM_BUS = "rt:system:bus"

SOURCE = "rfintel_mobile_advisor"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def now_ms() -> int:
    return int(time.time() * 1000)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def load_jsonish(raw: Any) -> Any:
    if raw is None:
        return None

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return {"_invalid_raw": s[:120]}

    return raw


def redis_get_json(r: redis.Redis, key: str) -> Any:
    """
    Read one known Redis key without scanning.

    Supports:
      - string JSON values
      - hashes, which are common for live RollingThunder device/state models

    This is intentionally key-specific access only. No SCAN/KEYS.
    """
    key_type = r.type(key)

    if isinstance(key_type, bytes):
        key_type = key_type.decode("utf-8", errors="replace")

    if key_type == "none":
        return None

    if key_type == "string":
        raw = r.get(key)
        return load_jsonish(raw)

    if key_type == "hash":
        raw_hash = r.hgetall(key)
        result: Dict[str, Any] = {}

        for k, v in raw_hash.items():
            if isinstance(k, bytes):
                k = k.decode("utf-8", errors="replace")
            if isinstance(v, bytes):
                v = v.decode("utf-8", errors="replace")

            parsed = load_jsonish(v)

            # Keep normal scalar hash values as strings unless they are JSON objects/arrays/bools/null/numbers.
            # This keeps the service tolerant of simple Redis hashes.
            result[str(k)] = parsed

        return result

    return {
        "_invalid_raw": f"Unsupported Redis key type for {key}: {key_type}"
    }


def redis_set_json(r: redis.Redis, key: str, value: Dict[str, Any]) -> None:
    r.set(key, json.dumps(value, sort_keys=True, separators=(",", ":")))


def as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        f = float(value)
        if not math.isfinite(f):
            return None
        return f
    except Exception:
        return None


def nested_get(obj: Dict[str, Any], paths: Tuple[Tuple[str, ...], ...]) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path:
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok:
            return cur
    return None


def extract_speed_mph(gps: Dict[str, Any]) -> Tuple[Optional[float], str]:
    direct_mph = nested_get(
        gps,
        (
            ("speed_mph",),
            ("mph",),
            ("speed", "mph"),
            ("gps", "speed_mph"),
            ("gps", "mph"),
            ("position", "speed_mph"),
        ),
    )
    mph = as_float(direct_mph)
    if mph is not None:
        return max(0.0, mph), "speed"

    kph_val = nested_get(
        gps,
        (
            ("speed_kph",),
            ("kph",),
            ("speed", "kph"),
            ("speed_kmh",),
            ("kmh",),
            ("gps", "speed_kph"),
            ("position", "speed_kph"),
        ),
    )
    kph = as_float(kph_val)
    if kph is not None:
        return max(0.0, kph * 0.621371), "speed"

    # Some GPS sources use speed in meters/second.
    # Only treat generic speed as m/s if a unit field says so.
    unit = str(
        nested_get(
            gps,
            (
                ("speed_unit",),
                ("speed_units",),
                ("speed", "unit"),
                ("gps", "speed_unit"),
            ),
        )
        or ""
    ).lower()

    generic_speed = nested_get(gps, (("speed",), ("gps", "speed"), ("position", "speed")))
    gs = as_float(generic_speed)
    if gs is not None and unit in {"m/s", "mps", "meters_per_second", "metres_per_second"}:
        return max(0.0, gs * 2.236936), "speed"

    return None, ""


def extract_lat_lon(gps: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    lat = as_float(
        nested_get(
            gps,
            (
                ("lat",),
                ("latitude",),
                ("gps", "lat"),
                ("gps", "latitude"),
                ("position", "lat"),
                ("position", "latitude"),
            ),
        )
    )
    lon = as_float(
        nested_get(
            gps,
            (
                ("lon",),
                ("lng",),
                ("longitude",),
                ("gps", "lon"),
                ("gps", "lng"),
                ("gps", "longitude"),
                ("position", "lon"),
                ("position", "lng"),
                ("position", "longitude"),
            ),
        )
    )

    if lat is None or lon is None:
        return None, None

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None, None

    return lat, lon


def extract_gps_time(gps: Dict[str, Any]) -> Optional[datetime]:
    value = nested_get(
        gps,
        (
            ("updated_utc",),
            ("timestamp_utc",),
            ("time_utc",),
            ("gps_time_utc",),
            ("timestamp",),
            ("updated_at",),
            ("time",),
            ("gps", "updated_utc"),
            ("gps", "timestamp_utc"),
            ("position", "updated_utc"),
            ("position", "timestamp_utc"),
        ),
    )

    dt = parse_iso_utc(value)
    if dt:
        return dt

    epoch_ms = as_float(
        nested_get(
            gps,
            (
                ("generated_ms",),
                ("timestamp_ms",),
                ("time_ms",),
                ("last_update_ms",),
                ("gps_last_seen_ms",),
                ("pos_last_good_ms",),
                ("gps", "generated_ms"),
                ("gps", "last_update_ms"),
                ("gps", "gps_last_seen_ms"),
                ("gps", "pos_last_good_ms"),
                ("position", "generated_ms"),
                ("position", "last_update_ms"),
                ("position", "gps_last_seen_ms"),
                ("position", "pos_last_good_ms"),
            ),
        )
    )
    if epoch_ms is not None and epoch_ms > 0:
        try:
            return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        except Exception:
            return None

    epoch_sec = as_float(
        nested_get(
            gps,
            (
                ("timestamp_sec",),
                ("epoch",),
                ("epoch_sec",),
                ("time_sec",),
                ("gps", "timestamp_sec"),
                ("position", "timestamp_sec"),
            ),
        )
    )
    if epoch_sec is not None and epoch_sec > 0:
        try:
            return datetime.fromtimestamp(epoch_sec, tz=timezone.utc)
        except Exception:
            return None

    return None


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_miles * c


def rounded_speed(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return int(round(value))


def load_state(r: redis.Redis) -> Dict[str, Any]:
    state = redis_get_json(r, KEY_STATE)
    if isinstance(state, dict):
        return state
    return {}


def save_state(r: redis.Redis, state: Dict[str, Any]) -> None:
    compact = {
        "last_lat": state.get("last_lat"),
        "last_lon": state.get("last_lon"),
        "last_seen_utc": state.get("last_seen_utc"),
        "moving_since_utc": state.get("moving_since_utc"),
        "stopped_since_utc": state.get("stopped_since_utc"),
        "last_motion_state": state.get("last_motion_state"),
        "last_mobile_mode": bool(state.get("last_mobile_mode", False)),
        "last_method": state.get("last_method"),
    }
    redis_set_json(r, KEY_STATE, compact)


def semantic_model(model: Dict[str, Any]) -> Dict[str, Any]:
    copy = dict(model)
    copy.pop("updated_utc", None)
    copy.pop("generated_ms", None)

    # Avoid churn caused only by duration wording changing.
    copy.pop("reason", None)

    return copy


def publish_state_changed(r: redis.Redis) -> None:
    ts = now_ms()
    msg = {
        "topic": "state.changed",
        "source": SOURCE,
        "ts_ms": ts,
        "payload": {
            "keys": [KEY_OUT],
            "changed_keys": [KEY_OUT],
            "ts_ms": ts,
        },
    }
    r.publish(SYSTEM_BUS, json.dumps(msg, sort_keys=True, separators=(",", ":")))


def build_model(
    gps_raw: Any,
    state: Dict[str, Any],
    *,
    enter_minutes: float,
    exit_minutes: float,
    moving_mph: float,
    stopped_mph: float,
    stale_sec: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    now = utc_now()
    now_iso = iso_utc(now)

    prior_mobile = bool(state.get("last_mobile_mode", False))
    prior_motion = str(state.get("last_motion_state") or "unknown")

    base = {
        "status": "unknown",
        "source": SOURCE,
        "mock": False,
        "updated_utc": now_iso,
        "generated_ms": now_ms(),
        "mobile_mode": prior_mobile,
        "motion_state": "unknown",
        "reason": "GPS position is not available.",
        "speed_mph": None,
        "moving_since_utc": state.get("moving_since_utc"),
        "stopped_since_utc": state.get("stopped_since_utc"),
        "input": {
            "gps": "missing",
            "method": "none",
        },
    }

    if gps_raw is None:
        state["last_motion_state"] = "unknown"
        state["last_mobile_mode"] = False
        state["moving_since_utc"] = None
        state["stopped_since_utc"] = state.get("stopped_since_utc") or now_iso
        return base, state

    if not isinstance(gps_raw, dict):
        base["status"] = "error"
        base["reason"] = "GPS position model is not an object."
        base["input"] = {"gps": "invalid", "method": "none"}
        state["last_motion_state"] = "unknown"
        state["last_mobile_mode"] = False
        state["moving_since_utc"] = None
        state["stopped_since_utc"] = state.get("stopped_since_utc") or now_iso
        return base, state

    if "_invalid_raw" in gps_raw:
        base["status"] = "error"
        base["reason"] = "GPS position model is not valid JSON."
        base["input"] = {"gps": "invalid", "method": "none"}
        state["last_motion_state"] = "unknown"
        state["last_mobile_mode"] = False
        state["moving_since_utc"] = None
        state["stopped_since_utc"] = state.get("stopped_since_utc") or now_iso
        return base, state

    gps_time = extract_gps_time(gps_raw)
    if gps_time:
        age_sec = max(0, int((now - gps_time).total_seconds()))
        if age_sec > stale_sec:
            base["status"] = "stale"
            base["reason"] = f"GPS data is stale ({age_sec} seconds old)."
            base["input"] = {"gps": "stale", "method": "none"}
            base["mobile_mode"] = False
            base["motion_state"] = "unknown"
            base["moving_since_utc"] = None
            base["stopped_since_utc"] = state.get("stopped_since_utc") or now_iso

            state["last_motion_state"] = "unknown"
            state["last_mobile_mode"] = False
            state["moving_since_utc"] = None
            state["stopped_since_utc"] = base["stopped_since_utc"]
            return base, state

    speed, method = extract_speed_mph(gps_raw)
    lat, lon = extract_lat_lon(gps_raw)

    estimated_speed = None
    if speed is None and lat is not None and lon is not None:
        last_lat = as_float(state.get("last_lat"))
        last_lon = as_float(state.get("last_lon"))
        last_seen = parse_iso_utc(state.get("last_seen_utc"))

        if last_lat is not None and last_lon is not None and last_seen is not None:
            elapsed_sec = max(1.0, (now - last_seen).total_seconds())
            miles = haversine_miles(last_lat, last_lon, lat, lon)
            estimated_speed = (miles / elapsed_sec) * 3600.0

            # Round tiny GPS jitter down.
            if miles < 0.02:
                estimated_speed = 0.0

            speed = estimated_speed
            method = "position_delta"

    if lat is not None and lon is not None:
        state["last_lat"] = round(lat, 6)
        state["last_lon"] = round(lon, 6)

    state["last_seen_utc"] = now_iso

    if speed is None:
        base["status"] = "unknown"
        base["reason"] = "GPS is present but does not include speed or usable position movement."
        base["input"] = {"gps": "ok", "method": "none"}
        base["mobile_mode"] = prior_mobile
        base["motion_state"] = prior_motion if prior_motion in {"moving", "stopped"} else "unknown"
        return base, state

    speed_int = rounded_speed(speed)

    if speed >= moving_mph:
        motion_state = "moving"
    elif speed <= stopped_mph:
        motion_state = "stopped"
    else:
        # Hysteresis zone. Preserve previous motion state if known.
        motion_state = prior_motion if prior_motion in {"moving", "stopped"} else "unknown"

    if motion_state == "moving":
        if state.get("last_motion_state") != "moving" or not state.get("moving_since_utc"):
            state["moving_since_utc"] = now_iso
        state["stopped_since_utc"] = None

    elif motion_state == "stopped":
        if state.get("last_motion_state") != "stopped" or not state.get("stopped_since_utc"):
            state["stopped_since_utc"] = now_iso
        state["moving_since_utc"] = None

    moving_since = parse_iso_utc(state.get("moving_since_utc"))
    stopped_since = parse_iso_utc(state.get("stopped_since_utc"))

    moving_minutes = 0.0
    stopped_minutes = 0.0

    if moving_since:
        moving_minutes = max(0.0, (now - moving_since).total_seconds() / 60.0)
    if stopped_since:
        stopped_minutes = max(0.0, (now - stopped_since).total_seconds() / 60.0)

    mobile_mode = prior_mobile

    if motion_state == "moving" and moving_minutes >= enter_minutes:
        mobile_mode = True
    elif motion_state == "stopped" and stopped_minutes >= exit_minutes:
        mobile_mode = False
    elif motion_state == "unknown":
        mobile_mode = prior_mobile

    if motion_state == "moving":
        status = "active" if mobile_mode else "inactive"
        if mobile_mode:
            reason = f"GPS movement detected for {int(round(moving_minutes))} minutes."
        else:
            reason = f"GPS movement detected; waiting for {int(round(enter_minutes))} minute mobile threshold."
    elif motion_state == "stopped":
        status = "inactive"
        if prior_mobile and not mobile_mode:
            reason = f"GPS stopped for {int(round(stopped_minutes))} minutes; mobile mode exited."
        elif prior_mobile:
            reason = f"GPS stopped; waiting for {int(round(exit_minutes))} minute exit threshold."
        else:
            reason = "GPS indicates the vehicle is stopped."
    else:
        status = "unknown"
        reason = "GPS speed is in the hysteresis range and prior motion is unknown."

    model = {
        "status": status,
        "source": SOURCE,
        "mock": False,
        "updated_utc": now_iso,
        "generated_ms": now_ms(),
        "mobile_mode": bool(mobile_mode),
        "motion_state": motion_state,
        "reason": reason,
        "speed_mph": speed_int,
        "moving_since_utc": state.get("moving_since_utc"),
        "stopped_since_utc": state.get("stopped_since_utc"),
        "input": {
            "gps": "ok",
            "method": method or "speed",
        },
    }

    state["last_motion_state"] = motion_state
    state["last_mobile_mode"] = bool(mobile_mode)
    state["last_method"] = method or "speed"

    return model, state


def connect_redis(args: argparse.Namespace) -> redis.Redis:
    return redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        password=args.redis_password or None,
        socket_timeout=5,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def run_once(r: redis.Redis, args: argparse.Namespace) -> bool:
    state = load_state(r)
    gps_raw = redis_get_json(r, KEY_GPS)

    model, new_state = build_model(
        gps_raw,
        state,
        enter_minutes=args.enter_minutes,
        exit_minutes=args.exit_minutes,
        moving_mph=args.moving_mph,
        stopped_mph=args.stopped_mph,
        stale_sec=args.stale_sec,
    )

    previous = redis_get_json(r, KEY_OUT)
    changed = not isinstance(previous, dict) or semantic_model(previous) != semantic_model(model)

    save_state(r, new_state)

    if changed:
        redis_set_json(r, KEY_OUT, model)
        publish_state_changed(r)

    if args.verbose:
        print(json.dumps(model, indent=2, sort_keys=True))
        print(f"changed={changed}")

    return changed


def positive_float(value: str) -> float:
    f = float(value)
    if f < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return f


def positive_int(value: str) -> int:
    i = int(float(value))
    if i < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return i


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RollingThunder RF Intel mobile mode detector")

    parser.add_argument(
        "--interval-sec",
        type=positive_float,
        default=env_float("RT_RFI_MOBILE_ADVISOR_INTERVAL_SEC", 30.0),
    )
    parser.add_argument(
        "--redis-host",
        default=os.environ.get("RT_REDIS_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=env_int("RT_REDIS_PORT", 6379),
    )
    parser.add_argument(
        "--redis-db",
        type=int,
        default=env_int("RT_REDIS_DB", 0),
    )
    parser.add_argument(
        "--redis-password",
        default=os.environ.get("RT_REDIS_PASSWORD", ""),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one pass and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print output model",
    )

    parser.add_argument(
        "--enter-minutes",
        type=positive_float,
        default=env_float("RT_RFI_MOBILE_ENTER_MINUTES", 8.0),
    )
    parser.add_argument(
        "--exit-minutes",
        type=positive_float,
        default=env_float("RT_RFI_MOBILE_EXIT_MINUTES", 3.0),
    )
    parser.add_argument(
        "--moving-mph",
        type=positive_float,
        default=env_float("RT_RFI_MOBILE_MOVING_MPH", 5.0),
    )
    parser.add_argument(
        "--stopped-mph",
        type=positive_float,
        default=env_float("RT_RFI_MOBILE_STOPPED_MPH", 2.0),
    )
    parser.add_argument(
        "--stale-sec",
        type=positive_int,
        default=env_int("RT_RFI_MOBILE_STALE_SEC", 180),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.stopped_mph > args.moving_mph:
        print("ERROR: --stopped-mph must be <= --moving-mph", file=sys.stderr)
        return 2

    r = connect_redis(args)

    try:
        r.ping()
    except Exception as exc:
        print(f"ERROR: cannot connect to Redis: {exc}", file=sys.stderr)
        return 1

    if args.once:
        run_once(r, args)
        return 0

    while True:
        try:
            run_once(r, args)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"ERROR: mobile advisor loop failed: {exc}", file=sys.stderr)

        time.sleep(max(1.0, float(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())