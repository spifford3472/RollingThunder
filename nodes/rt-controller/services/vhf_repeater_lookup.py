#!/usr/bin/env python3
"""
RollingThunder v0.55.0 Phase 2
Controller-side nearby VHF repeater lookup model.

Reads:
  - Redis hash: rt:gps:pos
  - SQLite DB: /opt/rollingthunder/data/vhf/repeaters.sqlite3

Writes:
  - Redis string JSON: rt:vhf:repeaters:nearby
  - Redis pub/sub: state.changed event on rt:system:bus

Does NOT:
  - write rt:ui:bus
  - program radios
  - scan Redis
  - mutate the repeater database
  - import CSV
  - call external APIs
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
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SOURCE = "vhf_repeater_lookup"

GPS_KEY = "rt:gps:pos"
OUTPUT_KEY = "rt:vhf:repeaters:nearby"
SYSTEM_BUS = "rt:system:bus"

APP_CONFIG_PATH = "/opt/rollingthunder/config/app.json"

DEFAULT_DB_PATH = "/opt/rollingthunder/data/vhf/repeaters.sqlite3"
DEFAULT_RADIUS_MILES = 40.0
DEFAULT_BUCKET_DEGREES = 0.25
DEFAULT_INTERVAL_SEC = 30.0
DEFAULT_FORCE_PUBLISH_SEC = 300.0


def load_app_config() -> Dict[str, Any]:
    try:
        path = Path(APP_CONFIG_PATH)
        if not path.exists():
            return {}

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else {}

    except Exception as exc:
        log.warning("could not read %s: %s", APP_CONFIG_PATH, exc)
        return {}


def config_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    if dotted in config:
        return config.get(dotted, default)

    current: Any = config
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]

    return current


def config_float(config: Dict[str, Any], dotted: str, env_name: str, default: float) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = config_get(config, dotted, default)

    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("not finite")
        return value
    except Exception:
        log.warning("invalid config %s=%r; using default %s", dotted, raw, default)
        return float(default)


def config_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = config_get(config, dotted, default)

    return str(raw) if raw is not None else default


APP_CONFIG = load_app_config()

DB_PATH = config_str(
    APP_CONFIG,
    "vhf.repeater_db_path",
    "RT_VHF_REPEATER_DB_PATH",
    DEFAULT_DB_PATH,
)

RADIUS_MILES = config_float(
    APP_CONFIG,
    "vhf.repeater_radius_miles",
    "RT_VHF_REPEATER_RADIUS_MILES",
    DEFAULT_RADIUS_MILES,
)

BUCKET_DEGREES = config_float(
    APP_CONFIG,
    "vhf.repeater_bucket_degrees",
    "RT_VHF_REPEATER_BUCKET_DEGREES",
    DEFAULT_BUCKET_DEGREES,
)

INTERVAL_SEC = config_float(
    APP_CONFIG,
    "vhf.repeater_lookup_interval_sec",
    "RT_VHF_REPEATER_LOOKUP_INTERVAL_SEC",
    DEFAULT_INTERVAL_SEC,
)

FORCE_PUBLISH_SEC = config_float(
    APP_CONFIG,
    "vhf.repeater_force_publish_sec",
    "RT_VHF_REPEATER_FORCE_PUBLISH_SEC",
    DEFAULT_FORCE_PUBLISH_SEC,
)

DEFAULT_MIN_GPS_MOVE_MILES = 0.25

CONFIG_PATHS = (
    "/opt/rollingthunder/config/app.json",
    "/opt/rollingthunder/config/config.json",
)

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
        with p.open("r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        log.warning("could not read config %s: %s", path, exc)
        return {}


def deep_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    """
    Supports both:
      {"vhf": {"repeater_radius_miles": 40}}
    and:
      {"vhf.repeater_radius_miles": 40}
    """
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


def cfg_float(config: Dict[str, Any], dotted: str, env_name: str, default: float) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    try:
        return float(raw)
    except Exception:
        log.warning("invalid config %s=%r; using %s", dotted, raw, default)
        return float(default)


def cfg_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    return str(raw) if raw is not None else default


class RedisCli:
    """
    Small Redis wrapper using redis-cli to avoid adding Python dependencies.

    Honors REDIS_AUTH_ARGS, REDIS_HOST, REDIS_PORT, REDIS_DB, and REDIS_CLI.
    Does not use SCAN or KEYS.
    """

    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args: List[str] = [self.redis_cli]

        host = os.environ.get("REDIS_HOST")
        port = os.environ.get("REDIS_PORT")
        db = os.environ.get("REDIS_DB")

        if host:
            self.base_args += ["-h", host]
        if port:
            self.base_args += ["-p", port]
        if db:
            self.base_args += ["-n", db]

        auth_args = os.environ.get("REDIS_AUTH_ARGS", "").strip()
        if auth_args:
            self.base_args += shlex.split(auth_args)

    def _run(self, args: Sequence[str], input_text: Optional[str] = None) -> str:
        cmd = self.base_args + list(args)
        proc = subprocess.run(
            cmd,
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
        f = float(value)
        if not math.isfinite(f):
            return None
        return f
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
    """
    Accept common RollingThunder-style/common GPS field names without redesigning GPS.
    """
    try:
        gps = redis.hgetall(GPS_KEY)
    except Exception as exc:
        log.warning("could not read GPS key %s: %s", GPS_KEY, exc)
        return None, None, "missing_gps"

    if not gps:
        return None, None, "missing_gps"

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
        return None, None, "missing_gps"

    return lat, lon, "ok"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r_miles * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bounding_box(lat: float, lon: float, radius_miles: float) -> Tuple[float, float, float, float]:
    lat_delta = radius_miles / 69.0
    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 0.01:
        lon_delta = 180.0
    else:
        lon_delta = radius_miles / (69.0 * cos_lat)

    min_lat = max(-90.0, lat - lat_delta)
    max_lat = min(90.0, lat + lat_delta)
    min_lon = max(-180.0, lon - lon_delta)
    max_lon = min(180.0, lon + lon_delta)
    return min_lat, max_lat, min_lon, max_lon


def bucket_index(value: float, bucket_degrees: float) -> int:
    return math.floor(value / bucket_degrees)


def bucket_degree_floor(value: float, bucket_degrees: float) -> float:
    return math.floor(value / bucket_degrees) * bucket_degrees


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalize_col(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def pick_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    exact = {c.lower(): c for c in columns}
    norm = {normalize_col(c): c for c in columns}

    for cand in candidates:
        if cand.lower() in exact:
            return exact[cand.lower()]

    for cand in candidates:
        n = normalize_col(cand)
        if n in norm:
            return norm[n]

    return None


def list_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [str(r[1]) for r in rows]


def choose_repeater_table(conn: sqlite3.Connection) -> Tuple[str, List[str]]:
    preferred_names = ("repeaters", "repeater", "vhf_repeaters", "channels")
    tables = list_tables(conn)
    if not tables:
        raise RuntimeError("empty_database")

    scored: List[Tuple[int, str, List[str]]] = []
    for table in tables:
        cols = table_columns(conn, table)
        lat_col = pick_column(cols, ("latitude", "lat", "repeater_latitude", "site_latitude"))
        lon_col = pick_column(cols, ("longitude", "lon", "lng", "repeater_longitude", "site_longitude"))
        rx_col = pick_column(cols, ("rx_frequency_mhz", "rx_freq_mhz", "frequency_mhz", "output_mhz", "rx_frequency"))
        score = 0
        if table.lower() in preferred_names:
            score += 50
        if lat_col:
            score += 20
        if lon_col:
            score += 20
        if rx_col:
            score += 10
        scored.append((score, table, cols))

    scored.sort(reverse=True, key=lambda x: x[0])
    best_score, best_table, best_cols = scored[0]
    if best_score < 40:
        raise RuntimeError(f"no_repeater_like_table tables={tables}")
    return best_table, best_cols


def row_get(row: sqlite3.Row, col: Optional[str], default: Any = None) -> Any:
    if not col:
        return default
    try:
        return row[col]
    except Exception:
        return default


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def number_or_none(value: Any) -> Optional[float]:
    return parse_float(value)


def build_item(row: sqlite3.Row, cols: Dict[str, Optional[str]], distance: float) -> Dict[str, Any]:
    rid = row_get(row, cols.get("id"))
    rid_f = parse_float(rid)
    repeater_id: Any
    if rid_f is not None and float(rid_f).is_integer():
        repeater_id = int(rid_f)
    else:
        repeater_id = clean_text(rid)

    rx = number_or_none(row_get(row, cols.get("rx_frequency_mhz")))
    tx = number_or_none(row_get(row, cols.get("tx_frequency_mhz")))

    item = {
        "repeater_id": repeater_id,
        "channel_name": clean_text(row_get(row, cols.get("channel_name"))),
        "rx_frequency_mhz": rx,
        "tx_frequency_mhz": tx,
        "rx_tone": clean_text(row_get(row, cols.get("rx_tone"))),
        "tx_tone": clean_text(row_get(row, cols.get("tx_tone"))),
        "distance_miles": round(distance, 1),
        "state": clean_text(row_get(row, cols.get("state"))),
        "special": clean_text(row_get(row, cols.get("special"))),
    }

    return item


def discover_columns(columns: Sequence[str]) -> Dict[str, Optional[str]]:
    return {
        "id": pick_column(columns, ("repeater_id", "id", "rowid", "channel_id")),
        "channel_name": pick_column(
            columns,
            (
                "channel_name",
                "name",
                "callsign",
                "call_sign",
                "repeater_name",
                "site_name",
                "description",
            ),
        ),
        "lat": pick_column(columns, ("latitude", "lat", "repeater_latitude", "site_latitude")),
        "lon": pick_column(columns, ("longitude", "lon", "lng", "repeater_longitude", "site_longitude")),
        "lat_bucket": pick_column(
            columns,
            (
                "lat_bucket_025",
                "lat_bucket",
                "latitude_bucket",
                "bucket_lat",
                "lat_bucket_index",
                "latitude_bucket_index",
            ),
        ),
        "lon_bucket": pick_column(
            columns,
            (
                "lon_bucket_025",
                "lon_bucket",
                "lng_bucket",
                "longitude_bucket",
                "bucket_lon",
                "bucket_lng",
                "lon_bucket_index",
                "longitude_bucket_index",
            ),
        ),
        "rx_frequency_mhz": pick_column(
            columns,
            (
                "rx_frequency_mhz",
                "rx_freq_mhz",
                "receive_frequency_mhz",
                "frequency_mhz",
                "output_mhz",
                "output_frequency_mhz",
                "rx_frequency",
            ),
        ),
        "tx_frequency_mhz": pick_column(
            columns,
            (
                "tx_frequency_mhz",
                "tx_freq_mhz",
                "transmit_frequency_mhz",
                "input_mhz",
                "input_frequency_mhz",
                "tx_frequency",
            ),
        ),
        "rx_tone": pick_column(
            columns,
            (
                "rx_tone",
                "receive_tone",
                "tone_rx",
                "pl_rx",
                "ctcss_rx",
                "tone",
                "pl",
                "ctcss",
            ),
        ),
        "tx_tone": pick_column(
            columns,
            (
                "tx_tone",
                "transmit_tone",
                "tone_tx",
                "pl_tx",
                "ctcss_tx",
                "tone",
                "pl",
                "ctcss",
            ),
        ),
        "state": pick_column(columns, ("state", "region", "province")),
        "special": pick_column(columns, ("special", "special_flag", "category", "tags", "notes", "skywarn")),
    }


def query_candidates(
    conn: sqlite3.Connection,
    table: str,
    cols: Dict[str, Optional[str]],
    lat: float,
    lon: float,
    radius_miles: float,
    bucket_degrees: float,
) -> List[sqlite3.Row]:
    lat_col = cols.get("lat")
    lon_col = cols.get("lon")
    lat_bucket_col = cols.get("lat_bucket")
    lon_bucket_col = cols.get("lon_bucket")

    if not lat_col or not lon_col:
        raise RuntimeError("schema_missing_lat_lon")

    min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_miles)

    where_parts = [
        f"CAST({quote_ident(lat_col)} AS REAL) BETWEEN ? AND ?",
        f"CAST({quote_ident(lon_col)} AS REAL) BETWEEN ? AND ?",
    ]
    params: List[Any] = [min_lat, max_lat, min_lon, max_lon]

    if lat_bucket_col and lon_bucket_col and bucket_degrees > 0:
        # Supports either integer bucket indexes or degree-floor bucket values.
        lat_idx_min = bucket_index(min_lat, bucket_degrees)
        lat_idx_max = bucket_index(max_lat, bucket_degrees)
        lon_idx_min = bucket_index(min_lon, bucket_degrees)
        lon_idx_max = bucket_index(max_lon, bucket_degrees)

        lat_deg_min = bucket_degree_floor(min_lat, bucket_degrees)
        lat_deg_max = bucket_degree_floor(max_lat, bucket_degrees)
        lon_deg_min = bucket_degree_floor(min_lon, bucket_degrees)
        lon_deg_max = bucket_degree_floor(max_lon, bucket_degrees)

        bucket_clause = f"""
        (
          (
            CAST({quote_ident(lat_bucket_col)} AS REAL) BETWEEN ? AND ?
            AND CAST({quote_ident(lon_bucket_col)} AS REAL) BETWEEN ? AND ?
          )
          OR
          (
            CAST({quote_ident(lat_bucket_col)} AS REAL) BETWEEN ? AND ?
            AND CAST({quote_ident(lon_bucket_col)} AS REAL) BETWEEN ? AND ?
          )
        )
        """
        where_parts.append(bucket_clause)
        params.extend(
            [
                lat_idx_min,
                lat_idx_max,
                lon_idx_min,
                lon_idx_max,
                lat_deg_min,
                lat_deg_max,
                lon_deg_min,
                lon_deg_max,
            ]
        )
    else:
        log.warning("bucket columns not found; using lat/lon bounding box fallback only")

    sql = f"""
        SELECT *
        FROM {quote_ident(table)}
        WHERE {" AND ".join(where_parts)}
    """

    return conn.execute(sql, params).fetchall()


def open_db_readonly(path: str) -> sqlite3.Connection:
    uri = "file:" + Path(path).absolute().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def unavailable_model(
    gps_status: str,
    radius_miles: float,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    model: Dict[str, Any] = {
        "status": "unavailable",
        "source": SOURCE,
        "gps_status": gps_status,
        "radius_miles": int(radius_miles) if float(radius_miles).is_integer() else radius_miles,
        "candidate_count": 0,
        "count": 0,
        "items": [],
        "updated_utc": utc_now_iso(),
    }
    if reason:
        model["reason"] = reason
    return model


def build_nearby_model(
    db_path: str,
    lat: float,
    lon: float,
    radius_miles: float,
    bucket_degrees: float,
) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return unavailable_model("ok", radius_miles, "missing_database")

    try:
        with open_db_readonly(db_path) as conn:
            table, columns = choose_repeater_table(conn)
            cols = discover_columns(columns)

            log.debug("using table=%s columns=%s", table, cols)

            rows = query_candidates(conn, table, cols, lat, lon, radius_miles, bucket_degrees)
            candidate_count = len(rows)

            items_with_distance: List[Tuple[float, Dict[str, Any]]] = []
            lat_col = cols.get("lat")
            lon_col = cols.get("lon")

            for row in rows:
                rlat = parse_float(row_get(row, lat_col))
                rlon = parse_float(row_get(row, lon_col))
                if rlat is None or rlon is None:
                    continue

                distance = haversine_miles(lat, lon, rlat, rlon)
                if distance <= radius_miles:
                    items_with_distance.append((distance, build_item(row, cols, distance)))

            items_with_distance.sort(key=lambda pair: pair[0])
            items = [item for _, item in items_with_distance]

            return {
                "status": "ok",
                "source": SOURCE,
                "gps_status": "ok",
                "radius_miles": int(radius_miles) if float(radius_miles).is_integer() else radius_miles,
                "candidate_count": candidate_count,
                "count": len(items),
                "items": items,
                "updated_utc": utc_now_iso(),
            }

    except RuntimeError as exc:
        reason = str(exc)
        log.warning("database lookup unavailable: %s", reason)
        return unavailable_model("ok", radius_miles, reason)
    except sqlite3.Error as exc:
        log.exception("sqlite error")
        return unavailable_model("ok", radius_miles, f"sqlite_error:{exc.__class__.__name__}")
    except Exception as exc:
        log.exception("lookup error")
        return unavailable_model("ok", radius_miles, f"lookup_error:{exc.__class__.__name__}")


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


def publish_model(redis: RedisCli, model: Dict[str, Any]) -> None:
    payload = json.dumps(model, separators=(",", ":"), sort_keys=True)
    redis.set(OUTPUT_KEY, payload)

    event = {
        "type": "state.changed",
        "source": SOURCE,
        "keys": [OUTPUT_KEY],
        "changed_keys": [OUTPUT_KEY],
        "updated_utc": model.get("updated_utc", utc_now_iso()),
    }
    redis.publish_json(SYSTEM_BUS, event)


def main() -> int:
    config = load_config()

    db_path = cfg_str(config, "vhf.repeater_db_path", "RT_VHF_REPEATER_DB_PATH", DEFAULT_DB_PATH)
    radius_miles = cfg_float(
        config,
        "vhf.repeater_radius_miles",
        "RT_VHF_REPEATER_RADIUS_MILES",
        DEFAULT_RADIUS_MILES,
    )
    bucket_degrees = cfg_float(
        config,
        "vhf.repeater_bucket_degrees",
        "RT_VHF_REPEATER_BUCKET_DEGREES",
        DEFAULT_BUCKET_DEGREES,
    )
    interval_sec = cfg_float(config, "vhf.repeater_lookup_interval_sec", "RT_VHF_LOOKUP_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)
    force_publish_sec = cfg_float(
        config,
        "vhf.repeater_force_publish_sec",
        "RT_VHF_FORCE_PUBLISH_SEC",
        DEFAULT_FORCE_PUBLISH_SEC,
    )
    min_gps_move_miles = cfg_float(
        config,
        "vhf.repeater_min_gps_move_miles",
        "RT_VHF_MIN_GPS_MOVE_MILES",
        DEFAULT_MIN_GPS_MOVE_MILES,
    )

    if radius_miles <= 0:
        log.warning("invalid radius %.3f; using default %.3f", radius_miles, DEFAULT_RADIUS_MILES)
        radius_miles = DEFAULT_RADIUS_MILES

    if bucket_degrees <= 0:
        log.warning("invalid bucket %.3f; using default %.3f", bucket_degrees, DEFAULT_BUCKET_DEGREES)
        bucket_degrees = DEFAULT_BUCKET_DEGREES

    if interval_sec < 5:
        interval_sec = 5

    redis = RedisCli()

    log.info(
        "starting db=%s radius_miles=%s bucket_degrees=%s interval_sec=%s",
        db_path,
        radius_miles,
        bucket_degrees,
        interval_sec,
    )

    last_publish_monotonic = 0.0
    last_gps: Optional[Tuple[float, float]] = None

    while True:
        try:
            lat, lon, gps_status = read_gps(redis)

            if gps_status != "ok" or lat is None or lon is None:
                model = unavailable_model("missing_gps", radius_miles)
            else:
                should_recompute = True

                if last_gps is not None:
                    moved = haversine_miles(last_gps[0], last_gps[1], lat, lon)
                    # Still recompute on the normal interval, but this lets logs show GPS significance.
                    if moved >= min_gps_move_miles:
                        log.info("GPS moved %.2f miles; recomputing nearby repeaters", moved)

                model = build_nearby_model(db_path, lat, lon, radius_miles, bucket_degrees)
                last_gps = (lat, lon)

            previous = redis.get(OUTPUT_KEY)
            now_mono = time.monotonic()
            force_publish = (now_mono - last_publish_monotonic) >= force_publish_sec

            if force_publish or changed_meaningfully(previous, model):
                publish_model(redis, model)
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

        except Exception:
            log.exception("service loop error; will continue")

        time.sleep(interval_sec)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
