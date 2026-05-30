#!/usr/bin/env python3
"""
RollingThunder Phase 8.2 VHF repeater cache lookup service.

Reads:
  - Redis GPS position: rt:gps:pos
  - Generated SQLite cache built from CSV:
      /opt/rollingthunder/data/vhf/repeaters_cache.sqlite3

Writes:
  - Redis string JSON: rt:vhf:repeaters:nearby
  - state.changed event on rt:system:bus

Safety:
  - Does not read the CSV at runtime.
  - Does not mutate the SQLite cache.
  - Does not command radios.
  - Does not write rt:ui:bus.
  - Does not implement map support.
  - AIR and NEWS remain non-scan categories in this phase.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import shlex
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SOURCE = "vhf_repeater_cache_lookup"

GPS_KEY = "rt:gps:pos"
OUTPUT_KEY = "rt:vhf:repeaters:nearby"
SYSTEM_BUS = "rt:system:bus"

CONFIG_PATHS = (
    "/opt/rollingthunder/config/app.json",
    "/opt/rollingthunder/config/config.json",
)

DEFAULT_SQLITE_CACHE_PATH = "/opt/rollingthunder/data/vhf/repeaters_cache.sqlite3"
DEFAULT_CSV_SOURCE_PATH = "/opt/rollingthunder/data/vhf/REPEATER_IMPORT.csv"
DEFAULT_RADIUS_MILES = 25.0
DEFAULT_INTERVAL_SEC = 30.0
DEFAULT_FORCE_PUBLISH_SEC = 300.0
DEFAULT_MIN_GPS_MOVE_MILES = 0.25
DEFAULT_RESULT_LIMIT = 100

LOG_LEVEL = os.environ.get("RT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(SOURCE)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_file(path: str) -> Dict[str, Any]:
    try:
        p = Path(path)
        if not p.exists():
            return {}
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        log.warning("could not read config %s: %s", path, exc)
        return {}


def deep_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    if dotted in config:
        return config.get(dotted, default)

    cur: Any = config
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def load_config() -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    for path in CONFIG_PATHS:
        config.update(load_json_file(path))
    return config


def cfg_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    text = str(raw if raw is not None else default).strip()
    return text or default


def cfg_float(config: Dict[str, Any], dotted: str, env_name: str, default: float, minimum: Optional[float] = None) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("not finite")
        if minimum is not None and value < minimum:
            raise ValueError(f"below minimum {minimum}")
        return value
    except Exception:
        log.warning("invalid config %s=%r; using %s", dotted, raw, default)
        return float(default)


def cfg_int(config: Dict[str, Any], dotted: str, env_name: str, default: int, minimum: Optional[int] = None) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    try:
        value = int(raw)
        if minimum is not None and value < minimum:
            raise ValueError(f"below minimum {minimum}")
        return value
    except Exception:
        log.warning("invalid config %s=%r; using %s", dotted, raw, default)
        return int(default)


class RedisCli:
    """
    Small Redis wrapper using redis-cli to match existing controller-service style.

    Honors:
      REDIS_CLI
      REDIS_AUTH_ARGS
      REDIS_HOST / RT_REDIS_HOST
      REDIS_PORT / RT_REDIS_PORT
      REDIS_DB / RT_REDIS_DB
    """

    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args: List[str] = [self.redis_cli]

        host = os.environ.get("RT_REDIS_HOST") or os.environ.get("REDIS_HOST")
        port = os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT")
        db = os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB")

        if host:
            self.base_args += ["-h", str(host)]
        if port:
            self.base_args += ["-p", str(port)]
        if db:
            self.base_args += ["-n", str(db)]

        auth_args = os.environ.get("REDIS_AUTH_ARGS", "").strip()
        if auth_args:
            self.base_args += shlex.split(auth_args)

    def _run(self, args: Sequence[str], input_text: Optional[str] = None) -> str:
        proc = subprocess.run(
            self.base_args + list(args),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"redis-cli failed rc={proc.returncode}: {proc.stderr.strip()}")
        return proc.stdout

    def hgetall(self, key: str) -> Dict[str, str]:
        out = self._run(["--raw", "HGETALL", key])
        lines = out.splitlines()
        result: Dict[str, str] = {}
        for i in range(0, len(lines) - 1, 2):
            result[lines[i]] = lines[i + 1]
        return result

    def get(self, key: str) -> Optional[str]:
        out = self._run(["--raw", "GET", key])
        if out == "":
            return None
        return out.rstrip("\n")

    def set(self, key: str, value: str) -> None:
        self._run(["SET", key, value])

    def publish_json(self, channel: str, payload: Dict[str, Any]) -> None:
        self._run(["PUBLISH", channel, json.dumps(payload, separators=(",", ":"), sort_keys=True)])


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


def first_present_float(data: Dict[str, Any], names: Iterable[str]) -> Optional[float]:
    lower = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            val = parse_float(data.get(name))
            if val is not None:
                return val
        lname = name.lower()
        if lname in lower:
            val = parse_float(lower.get(lname))
            if val is not None:
                return val
    return None


def read_gps(redis: RedisCli) -> Tuple[Optional[float], Optional[float], str]:
    try:
        gps = redis.hgetall(GPS_KEY)
    except Exception as exc:
        log.warning("could not read GPS key %s: %s", GPS_KEY, exc)
        return None, None, "missing_gps"

    if not gps:
        raw = redis.get(GPS_KEY)
        if raw:
            try:
                parsed = json.loads(raw)
                gps = parsed if isinstance(parsed, dict) else {}
            except Exception:
                gps = {}

    if not gps:
        return None, None, "missing_gps"

    status = str(gps.get("status") or gps.get("gps_status") or gps.get("fix_status") or "").strip().lower()
    if status and status not in {"ok", "valid", "ready", "fix", "fixed", "3d", "2d"}:
        return None, None, "invalid_gps"

    lat = first_present_float(
        gps,
        (
            "lat",
            "latitude",
            "gps_lat",
            "gps_latitude",
            "fix_lat",
            "position_lat",
        ),
    )
    lon = first_present_float(
        gps,
        (
            "lon",
            "lng",
            "longitude",
            "gps_lon",
            "gps_lng",
            "gps_longitude",
            "fix_lon",
            "position_lon",
        ),
    )

    if lat is None or lon is None:
        return None, None, "missing_gps"

    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return None, None, "invalid_gps"

    return lat, lon, "ok"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * radius_miles * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = math.degrees(math.atan2(y, x))
    return int(round((bearing + 360.0) % 360.0))


def bounding_box(lat: float, lon: float, radius_miles: float) -> Tuple[float, float, float, float]:
    lat_delta = radius_miles / 69.0
    cos_lat = math.cos(math.radians(lat))
    lon_delta = 180.0 if abs(cos_lat) < 0.01 else radius_miles / (69.0 * cos_lat)

    min_lat = max(-90.0, lat - lat_delta)
    max_lat = min(90.0, lat + lat_delta)
    min_lon = max(-180.0, lon - lon_delta)
    max_lon = min(180.0, lon + lon_delta)
    return min_lat, max_lat, min_lon, max_lon


def unavailable_model(
    *,
    csv_source_file: str,
    cache_file: str,
    gps_status: str,
    radius_miles: float,
    reason: str,
) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "source": "csv",
        "source_file": csv_source_file,
        "cache_file": cache_file,
        "gps_status": gps_status,
        "reason": reason,
        "center_lat": None,
        "center_lon": None,
        "radius_miles": int(radius_miles) if float(radius_miles).is_integer() else radius_miles,
        "selected_index": 0,
        "candidate_count": 0,
        "count": 0,
        "repeaters": [],
        "items": [],
        "updated_utc": utc_now_iso(),
    }


def open_cache_readonly(path: str) -> sqlite3.Connection:
    uri = "file:" + Path(path).absolute().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def query_candidates(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    radius_miles: float,
    limit: int,
) -> List[sqlite3.Row]:
    min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_miles)
    return conn.execute(
        """
        SELECT *
        FROM repeaters_cache
        WHERE scan_enabled = 1
          AND latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
        LIMIT ?
        """,
        (min_lat, max_lat, min_lon, max_lon, int(max(limit * 4, limit))),
    ).fetchall()


def normalize_repeater_row(row: sqlite3.Row, center_lat: float, center_lon: float) -> Dict[str, Any]:
    latitude = float(row["latitude"])
    longitude = float(row["longitude"])
    distance = haversine_miles(center_lat, center_lon, latitude, longitude)

    return {
        "id": str(row["id"]),
        "name": str(row["name"] or ""),
        "callsign": str(row["name"] or ""),
        "frequency_mhz": round(float(row["frequency_mhz"]), 6),
        "offset_mhz": round(float(row["offset_mhz"]), 6),
        "duplex": str(row["duplex"] or "simplex"),
        "tone_hz": row["repeater_tone_hz"],
        "tone_mode": str(row["tone_mode"] or "off"),
        "mode": str(row["mode"] or "FM"),
        "distance_miles": round(distance, 1),
        "bearing_degrees": bearing_degrees(center_lat, center_lon, latitude, longitude),
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "skywarn": bool(row["skywarn"]),
        "ares": bool(row["ares"]),
        "selected": False,
        "active": False,
        "source_row": int(row["source_row"]),
        "type": str(row["service_type"] or "Repeater"),
    }


def build_nearby_model(
    *,
    csv_source_file: str,
    cache_file: str,
    lat: float,
    lon: float,
    radius_miles: float,
    limit: int,
) -> Dict[str, Any]:
    if not Path(cache_file).exists():
        return unavailable_model(
            csv_source_file=csv_source_file,
            cache_file=cache_file,
            gps_status="ok",
            radius_miles=radius_miles,
            reason="missing_repeater_cache",
        )

    try:
        with open_cache_readonly(cache_file) as conn:
            rows = query_candidates(conn, lat, lon, radius_miles, limit)
            candidate_count = len(rows)

            repeaters: List[Dict[str, Any]] = []
            for row in rows:
                item = normalize_repeater_row(row, lat, lon)
                if item["distance_miles"] <= radius_miles:
                    repeaters.append(item)

            repeaters.sort(key=lambda item: (item["distance_miles"], item["frequency_mhz"], item["name"]))
            repeaters = repeaters[:limit]

            if repeaters:
                repeaters[0]["selected"] = True

            radius_out: float | int = int(radius_miles) if float(radius_miles).is_integer() else radius_miles
            return {
                "status": "ok",
                "source": "csv",
                "source_file": csv_source_file,
                "cache_file": cache_file,
                "gps_status": "ok",
                "center_lat": round(float(lat), 6),
                "center_lon": round(float(lon), 6),
                "radius_miles": radius_out,
                "selected_index": 0,
                "candidate_count": candidate_count,
                "count": len(repeaters),
                "repeaters": repeaters,
                # Temporary compatibility for the existing renderer/scan-manager helpers.
                "items": repeaters,
                "updated_utc": utc_now_iso(),
            }

    except sqlite3.Error as exc:
        log.exception("sqlite cache error")
        return unavailable_model(
            csv_source_file=csv_source_file,
            cache_file=cache_file,
            gps_status="ok",
            radius_miles=radius_miles,
            reason=f"sqlite_error:{exc.__class__.__name__}",
        )
    except Exception as exc:
        log.exception("lookup error")
        return unavailable_model(
            csv_source_file=csv_source_file,
            cache_file=cache_file,
            gps_status="ok",
            radius_miles=radius_miles,
            reason=f"lookup_error:{exc.__class__.__name__}",
        )


def stable_model_for_compare(model: Dict[str, Any]) -> Dict[str, Any]:
    clone = dict(model)
    clone.pop("updated_utc", None)
    return clone


def changed_meaningfully(previous_json: Optional[str], new_model: Dict[str, Any]) -> bool:
    if previous_json is None:
        return True
    try:
        previous = json.loads(previous_json)
    except Exception:
        return True
    return stable_model_for_compare(previous) != stable_model_for_compare(new_model)


def publish_model(redis: RedisCli, model: Dict[str, Any], reason: str) -> None:
    payload = json.dumps(model, separators=(",", ":"), sort_keys=True)
    redis.set(OUTPUT_KEY, payload)

    event = {
        "type": "state.changed",
        "topic": "state.changed",
        "source": SOURCE,
        "keys": [OUTPUT_KEY],
        "changed_keys": [OUTPUT_KEY],
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": model.get("updated_utc", utc_now_iso()),
    }
    redis.publish_json(SYSTEM_BUS, event)


def main() -> int:
    config = load_config()

    cache_file = cfg_str(
        config,
        "vhf.repeater_cache_path",
        "RT_VHF_REPEATER_CACHE_PATH",
        DEFAULT_SQLITE_CACHE_PATH,
    )
    csv_source_file = cfg_str(
        config,
        "vhf.repeater_csv_path",
        "RT_VHF_REPEATER_CSV_PATH",
        DEFAULT_CSV_SOURCE_PATH,
    )
    radius_miles = cfg_float(
        config,
        "vhf.repeater_radius_miles",
        "RT_VHF_REPEATER_RADIUS_MILES",
        DEFAULT_RADIUS_MILES,
        minimum=0.1,
    )
    interval_sec = cfg_float(
        config,
        "vhf.repeater_lookup_interval_sec",
        "RT_VHF_LOOKUP_INTERVAL_SEC",
        DEFAULT_INTERVAL_SEC,
        minimum=5.0,
    )
    force_publish_sec = cfg_float(
        config,
        "vhf.repeater_force_publish_sec",
        "RT_VHF_FORCE_PUBLISH_SEC",
        DEFAULT_FORCE_PUBLISH_SEC,
        minimum=30.0,
    )
    min_gps_move_miles = cfg_float(
        config,
        "vhf.repeater_min_gps_move_miles",
        "RT_VHF_MIN_GPS_MOVE_MILES",
        DEFAULT_MIN_GPS_MOVE_MILES,
        minimum=0.0,
    )
    result_limit = cfg_int(
        config,
        "vhf.repeater_result_limit",
        "RT_VHF_REPEATER_RESULT_LIMIT",
        DEFAULT_RESULT_LIMIT,
        minimum=1,
    )

    redis = RedisCli()

    log.info(
        "starting cache=%s csv_source=%s radius_miles=%s interval_sec=%s result_limit=%s",
        cache_file,
        csv_source_file,
        radius_miles,
        interval_sec,
        result_limit,
    )

    last_publish_monotonic = 0.0
    last_gps: Optional[Tuple[float, float]] = None

    while True:
        try:
            lat, lon, gps_status = read_gps(redis)

            if gps_status != "ok" or lat is None or lon is None:
                model = unavailable_model(
                    csv_source_file=csv_source_file,
                    cache_file=cache_file,
                    gps_status=gps_status,
                    radius_miles=radius_miles,
                    reason=gps_status,
                )
            else:
                if last_gps is not None:
                    moved = haversine_miles(last_gps[0], last_gps[1], lat, lon)
                    if moved >= min_gps_move_miles:
                        log.info("GPS moved %.2f miles; recomputing nearby repeaters", moved)

                model = build_nearby_model(
                    csv_source_file=csv_source_file,
                    cache_file=cache_file,
                    lat=lat,
                    lon=lon,
                    radius_miles=radius_miles,
                    limit=result_limit,
                )
                last_gps = (lat, lon)

            previous = redis.get(OUTPUT_KEY)
            now_mono = time.monotonic()
            force_publish = (now_mono - last_publish_monotonic) >= force_publish_sec

            if force_publish or changed_meaningfully(previous, model):
                publish_model(
                    redis,
                    model,
                    "vhf_repeater_cache_changed" if not force_publish else "vhf_repeater_cache_heartbeat",
                )
                last_publish_monotonic = now_mono
                log.info(
                    "published %s status=%s gps=%s candidates=%s count=%s",
                    OUTPUT_KEY,
                    model.get("status"),
                    model.get("gps_status"),
                    model.get("candidate_count"),
                    model.get("count"),
                )
            else:
                log.debug("no meaningful change; skipped Redis write")

        except KeyboardInterrupt:
            log.info("stopping")
            return 0
        except Exception:
            log.exception("service loop error; will continue")

        time.sleep(interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
