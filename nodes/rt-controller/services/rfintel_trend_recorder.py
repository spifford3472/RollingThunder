#!/usr/bin/env python3
"""
RollingThunder RF Intel lightweight trend recorder.

Controller-side only.

Inputs:
  - rt:rfintel:solar        JSON string
  - rt:rfintel:bands        JSON string
  - rt:weather:current      Redis hash

Outputs:
  - SQLite archive: /opt/rollingthunder/data/rfintel/rfintel_trends.sqlite3
  - rt:rfintel:trend:status JSON
  - rt:rfintel:trend:current JSON
  - rt:rfintel:trend:recent JSON
  - rt:system:bus state.changed when trend Redis models semantically change

Architecture:
  - Controller owns all state.
  - UI is renderer-only.
  - This service never writes to rt:ui:bus.
  - This service never emits UI intents.
  - Redis holds only current/recent/status trend projections, not the full archive.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

SYSTEM_BUS = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")

KEY_SOLAR = os.environ.get("RT_KEY_RFINTEL_SOLAR", "rt:rfintel:solar")
KEY_BANDS = os.environ.get("RT_KEY_RFINTEL_BANDS", "rt:rfintel:bands")
KEY_WEATHER = os.environ.get("RT_KEY_WEATHER_CURRENT", "rt:weather:current")

KEY_TREND_STATUS = os.environ.get("RT_KEY_RFINTEL_TREND_STATUS", "rt:rfintel:trend:status")
KEY_TREND_CURRENT = os.environ.get("RT_KEY_RFINTEL_TREND_CURRENT", "rt:rfintel:trend:current")
KEY_TREND_RECENT = os.environ.get("RT_KEY_RFINTEL_TREND_RECENT", "rt:rfintel:trend:recent")

DB_PATH = Path(os.environ.get(
    "RT_RFINTEL_TREND_DB",
    "/opt/rollingthunder/data/rfintel/rfintel_trends.sqlite3",
))

BUCKET_MINUTES = int(os.environ.get("RT_RFINTEL_TREND_BUCKET_MINUTES", "15"))
RETENTION_DAYS = int(os.environ.get("RT_RFINTEL_TREND_RETENTION_DAYS", "1095"))
INTERVAL_SEC = int(os.environ.get("RT_RFINTEL_TREND_INTERVAL_SEC", "60"))
RECENT_BUCKETS = int(os.environ.get("RT_RFINTEL_TREND_RECENT_BUCKETS", "96"))

MAX_TEXT = int(os.environ.get("RT_RFINTEL_TREND_MAX_TEXT", "96"))

SERVICE_NAME = "rfintel_trend_recorder"

LOG_LEVEL = os.environ.get("RT_RFINTEL_TREND_LOG_LEVEL", "INFO").upper()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_isoish_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def bucket_start_for(dt: datetime, bucket_minutes: int = BUCKET_MINUTES) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = (dt.minute // bucket_minutes) * bucket_minutes
    return dt.replace(minute=minute)


def bucket_parts(start: datetime) -> Dict[str, Any]:
    start = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    end = start + timedelta(minutes=BUCKET_MINUTES)
    return {
        "bucket_start_utc": iso_z(start),
        "bucket_end_utc": iso_z(end),
        "bucket_minutes": BUCKET_MINUTES,
        "bucket_date_utc": start.strftime("%Y-%m-%d"),
        "bucket_time_utc": start.strftime("%H:%M:%S"),
        "bucket_hour_utc": int(start.strftime("%H")),
        "bucket_dow_utc": int(start.weekday()),  # Monday=0, Sunday=6
    }


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def cap_text(value: Any, limit: int = MAX_TEXT) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) > limit:
        return s[:limit]
    return s


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def redis_client() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def read_json_key(r: redis.Redis, key: str) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        raw = r.get(key)
        if not raw:
            return None, "missing"
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None, "not_object"
        return obj, "ok"
    except Exception as e:
        return None, f"error:{type(e).__name__}"


def read_hash_key(r: redis.Redis, key: str) -> Tuple[Dict[str, str], str]:
    try:
        obj = r.hgetall(key)
        if not obj:
            return {}, "missing"
        return dict(obj), "ok"
    except Exception as e:
        return {}, f"error:{type(e).__name__}"


def publish_state_changed(r: redis.Redis, keys: List[str]) -> None:
    if not keys:
        return
    event = {
        "topic": "state.changed",
        "type": "state.changed",
        "payload": {
            "keys": keys,
            "changed_keys": keys,
            "ts_ms": now_ms(),
        },
        "ts_ms": now_ms(),
        "source": SERVICE_NAME,
    }
    r.publish(SYSTEM_BUS, stable_json(event))


def set_json_if_changed(r: redis.Redis, key: str, obj: Any, changed: List[str]) -> None:
    new_raw = stable_json(obj)
    try:
        old_raw = r.get(key)
    except Exception:
        old_raw = None

    if old_raw != new_raw:
        r.set(key, new_raw)
        changed.append(key)


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS rfintel_solar_15m (
  bucket_start_utc TEXT PRIMARY KEY,
  bucket_end_utc TEXT NOT NULL,
  bucket_minutes INTEGER NOT NULL,
  bucket_date_utc TEXT NOT NULL,
  bucket_time_utc TEXT NOT NULL,
  bucket_hour_utc INTEGER NOT NULL,
  bucket_dow_utc INTEGER NOT NULL,
  k_index REAL,
  a_index REAL,
  sfi REAL,
  sunspots INTEGER,
  xray TEXT,
  condition TEXT,
  swpc_scales TEXT,
  source_status TEXT,
  source_updated_utc TEXT,
  created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfintel_band_15m (
  bucket_start_utc TEXT NOT NULL,
  bucket_end_utc TEXT NOT NULL,
  bucket_minutes INTEGER NOT NULL,
  bucket_date_utc TEXT NOT NULL,
  bucket_time_utc TEXT NOT NULL,
  bucket_hour_utc INTEGER NOT NULL,
  bucket_dow_utc INTEGER NOT NULL,
  band TEXT NOT NULL,
  score INTEGER,
  confidence TEXT,
  status TEXT,
  mode TEXT,
  spot_count INTEGER,
  source_status TEXT,
  source_updated_utc TEXT,
  created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL,
  PRIMARY KEY(bucket_start_utc, band)
);

CREATE TABLE IF NOT EXISTS rfintel_weather_15m (
  bucket_start_utc TEXT PRIMARY KEY,
  bucket_end_utc TEXT NOT NULL,
  bucket_minutes INTEGER NOT NULL,
  bucket_date_utc TEXT NOT NULL,
  bucket_time_utc TEXT NOT NULL,
  bucket_hour_utc INTEGER NOT NULL,
  bucket_dow_utc INTEGER NOT NULL,
  temperature_f REAL,
  temperature_c REAL,
  wind_speed TEXT,
  wind_direction TEXT,
  condition TEXT,
  source TEXT,
  stale INTEGER,
  reason TEXT,
  source_updated_ms INTEGER,
  grid_id TEXT,
  grid_x TEXT,
  grid_y TEXT,
  created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfintel_trend_meta (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rfintel_band_15m_band_time
ON rfintel_band_15m(band, bucket_start_utc DESC);

CREATE INDEX IF NOT EXISTS idx_rfintel_weather_15m_time
ON rfintel_weather_15m(bucket_start_utc DESC);
"""


def ensure_db_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def move_bad_db_aside(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    bad_path = path.with_suffix(path.suffix + f".corrupt.{stamp}")
    shutil.move(str(path), str(bad_path))
    logger.error("Moved unreadable/corrupt trend DB aside: %s", bad_path)
    return bad_path


def connect_db(recover_corrupt: bool = True) -> sqlite3.Connection:
    ensure_db_dir()
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        return conn
    except sqlite3.DatabaseError:
        logger.exception("SQLite database error while opening/initializing %s", DB_PATH)
        if recover_corrupt:
            try:
                move_bad_db_aside(DB_PATH)
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA_SQL)
                conn.commit()
                return conn
            except Exception:
                logger.exception("SQLite recovery failed")
                raise
        raise


# ---------------------------------------------------------------------------
# Recorders
# ---------------------------------------------------------------------------

def upsert_solar(conn: sqlite3.Connection, bucket: Dict[str, Any], solar: Optional[Dict[str, Any]]) -> bool:
    if not solar:
        return False

    now = iso_z(utc_now())

    k_index = to_float(solar.get("k_index"))
    if k_index is None:
        k_index = to_float(solar.get("kp"))

    sfi = to_float(solar.get("sfi"))
    if sfi is None:
        sfi = to_float(solar.get("solar_flux"))

    row = {
        **bucket,
        "k_index": k_index,
        "a_index": to_float(solar.get("a_index")),
        "sfi": sfi,
        "sunspots": to_int(solar.get("sunspot_number") if "sunspot_number" in solar else solar.get("sunspots")),
        "xray": cap_text(solar.get("xray_status") if "xray_status" in solar else solar.get("xray")),
        "condition": cap_text(solar.get("condition") or solar.get("solar_status")),
        "swpc_scales": cap_text(solar.get("swpc_scales"), 32),
        "source_status": cap_text(solar.get("status"), 32),
        "source_updated_utc": cap_text(solar.get("updated_utc"), 32),
        "created_utc": now,
        "updated_utc": now,
    }

    conn.execute(
        """
        INSERT INTO rfintel_solar_15m (
          bucket_start_utc, bucket_end_utc, bucket_minutes,
          bucket_date_utc, bucket_time_utc, bucket_hour_utc, bucket_dow_utc,
          k_index, a_index, sfi, sunspots, xray, condition, swpc_scales,
          source_status, source_updated_utc, created_utc, updated_utc
        )
        VALUES (
          :bucket_start_utc, :bucket_end_utc, :bucket_minutes,
          :bucket_date_utc, :bucket_time_utc, :bucket_hour_utc, :bucket_dow_utc,
          :k_index, :a_index, :sfi, :sunspots, :xray, :condition, :swpc_scales,
          :source_status, :source_updated_utc, :created_utc, :updated_utc
        )
        ON CONFLICT(bucket_start_utc) DO UPDATE SET
          bucket_end_utc=excluded.bucket_end_utc,
          bucket_minutes=excluded.bucket_minutes,
          bucket_date_utc=excluded.bucket_date_utc,
          bucket_time_utc=excluded.bucket_time_utc,
          bucket_hour_utc=excluded.bucket_hour_utc,
          bucket_dow_utc=excluded.bucket_dow_utc,
          k_index=excluded.k_index,
          a_index=excluded.a_index,
          sfi=excluded.sfi,
          sunspots=excluded.sunspots,
          xray=excluded.xray,
          condition=excluded.condition,
          swpc_scales=excluded.swpc_scales,
          source_status=excluded.source_status,
          source_updated_utc=excluded.source_updated_utc,
          updated_utc=excluded.updated_utc
        """,
        row,
    )
    return True


def upsert_bands(conn: sqlite3.Connection, bucket: Dict[str, Any], bands: Optional[Dict[str, Any]]) -> int:
    if not bands:
        return 0

    items = bands.get("items")
    if not isinstance(items, list):
        return 0

    now = iso_z(utc_now())
    count = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        band = cap_text(item.get("band"), 16)
        if not band:
            continue

        row = {
            **bucket,
            "band": band,
            "score": to_int(item.get("score")),
            "confidence": cap_text(item.get("confidence"), 32),
            "status": cap_text(item.get("status"), 32),
            "mode": cap_text(item.get("mode"), 16),
            # Only use explicit per-band spot_count if it exists.
            # Do not parse prose from reason/recommendation.
            "spot_count": to_int(item.get("spot_count")),
            "source_status": cap_text(bands.get("status"), 32),
            "source_updated_utc": cap_text(bands.get("updated_utc"), 32),
            "created_utc": now,
            "updated_utc": now,
        }

        conn.execute(
            """
            INSERT INTO rfintel_band_15m (
              bucket_start_utc, bucket_end_utc, bucket_minutes,
              bucket_date_utc, bucket_time_utc, bucket_hour_utc, bucket_dow_utc,
              band, score, confidence, status, mode, spot_count,
              source_status, source_updated_utc, created_utc, updated_utc
            )
            VALUES (
              :bucket_start_utc, :bucket_end_utc, :bucket_minutes,
              :bucket_date_utc, :bucket_time_utc, :bucket_hour_utc, :bucket_dow_utc,
              :band, :score, :confidence, :status, :mode, :spot_count,
              :source_status, :source_updated_utc, :created_utc, :updated_utc
            )
            ON CONFLICT(bucket_start_utc, band) DO UPDATE SET
              bucket_end_utc=excluded.bucket_end_utc,
              bucket_minutes=excluded.bucket_minutes,
              bucket_date_utc=excluded.bucket_date_utc,
              bucket_time_utc=excluded.bucket_time_utc,
              bucket_hour_utc=excluded.bucket_hour_utc,
              bucket_dow_utc=excluded.bucket_dow_utc,
              score=excluded.score,
              confidence=excluded.confidence,
              status=excluded.status,
              mode=excluded.mode,
              spot_count=excluded.spot_count,
              source_status=excluded.source_status,
              source_updated_utc=excluded.source_updated_utc,
              updated_utc=excluded.updated_utc
            """,
            row,
        )
        count += 1

    return count


def upsert_weather(conn: sqlite3.Connection, bucket: Dict[str, Any], weather: Dict[str, str]) -> bool:
    if not weather:
        return False

    now = iso_z(utc_now())

    row = {
        **bucket,
        "temperature_f": to_float(weather.get("f")),
        "temperature_c": to_float(weather.get("c")),
        "wind_speed": cap_text(weather.get("wind_speed"), 32),
        "wind_direction": cap_text(weather.get("wind_direction"), 16),
        "condition": cap_text(weather.get("short_forecast")),
        "source": cap_text(weather.get("source"), 32),
        "stale": to_int(weather.get("stale")),
        "reason": cap_text(weather.get("reason")),
        "source_updated_ms": to_int(weather.get("last_update_ms")),
        "grid_id": cap_text(weather.get("grid_id"), 16),
        "grid_x": cap_text(weather.get("grid_x"), 16),
        "grid_y": cap_text(weather.get("grid_y"), 16),
        "created_utc": now,
        "updated_utc": now,
    }

    conn.execute(
        """
        INSERT INTO rfintel_weather_15m (
          bucket_start_utc, bucket_end_utc, bucket_minutes,
          bucket_date_utc, bucket_time_utc, bucket_hour_utc, bucket_dow_utc,
          temperature_f, temperature_c, wind_speed, wind_direction, condition,
          source, stale, reason, source_updated_ms, grid_id, grid_x, grid_y,
          created_utc, updated_utc
        )
        VALUES (
          :bucket_start_utc, :bucket_end_utc, :bucket_minutes,
          :bucket_date_utc, :bucket_time_utc, :bucket_hour_utc, :bucket_dow_utc,
          :temperature_f, :temperature_c, :wind_speed, :wind_direction, :condition,
          :source, :stale, :reason, :source_updated_ms, :grid_id, :grid_x, :grid_y,
          :created_utc, :updated_utc
        )
        ON CONFLICT(bucket_start_utc) DO UPDATE SET
          bucket_end_utc=excluded.bucket_end_utc,
          bucket_minutes=excluded.bucket_minutes,
          bucket_date_utc=excluded.bucket_date_utc,
          bucket_time_utc=excluded.bucket_time_utc,
          bucket_hour_utc=excluded.bucket_hour_utc,
          bucket_dow_utc=excluded.bucket_dow_utc,
          temperature_f=excluded.temperature_f,
          temperature_c=excluded.temperature_c,
          wind_speed=excluded.wind_speed,
          wind_direction=excluded.wind_direction,
          condition=excluded.condition,
          source=excluded.source,
          stale=excluded.stale,
          reason=excluded.reason,
          source_updated_ms=excluded.source_updated_ms,
          grid_id=excluded.grid_id,
          grid_x=excluded.grid_x,
          grid_y=excluded.grid_y,
          updated_utc=excluded.updated_utc
        """,
        row,
    )
    return True


# ---------------------------------------------------------------------------
# Retention, status, current, recent
# ---------------------------------------------------------------------------

def prune_older_than(conn: sqlite3.Connection, cutoff_utc: str) -> Dict[str, int]:
    results: Dict[str, int] = {}
    for table in ("rfintel_solar_15m", "rfintel_band_15m", "rfintel_weather_15m"):
        cur = conn.execute(f"DELETE FROM {table} WHERE bucket_start_utc < ?", (cutoff_utc,))
        results[table] = int(cur.rowcount if cur.rowcount is not None else 0)
    conn.execute(
        """
        INSERT INTO rfintel_trend_meta(key, value, updated_utc)
        VALUES('last_prune_utc', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_utc=excluded.updated_utc
        """,
        (iso_z(utc_now()), iso_z(utc_now())),
    )
    conn.commit()
    return results


def prune_retention(conn: sqlite3.Connection) -> Dict[str, int]:
    cutoff = utc_now() - timedelta(days=RETENTION_DAYS)
    return prune_older_than(conn, iso_z(bucket_start_for(cutoff)))


def record_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    out: Dict[str, int] = {}
    mapping = {
        "solar_15m": "rfintel_solar_15m",
        "band_15m": "rfintel_band_15m",
        "weather_15m": "rfintel_weather_15m",
    }
    for name, table in mapping.items():
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[name] = int(row["n"] if row else 0)
        except Exception:
            out[name] = 0
    out["contacts_15m"] = 0
    return out


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM rfintel_trend_meta WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return str(row["value"])


def top_band_for_bucket(conn: sqlite3.Connection, bucket_start_utc: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT band, score, confidence, status, mode, spot_count
        FROM rfintel_band_15m
        WHERE bucket_start_utc=?
        ORDER BY score DESC, band ASC
        LIMIT 1
        """,
        (bucket_start_utc,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def current_model(
    conn: sqlite3.Connection,
    bucket: Dict[str, Any],
    solar_status: str,
    bands_status: str,
    weather_status: str,
) -> Dict[str, Any]:
    bucket_start = bucket["bucket_start_utc"]

    solar_row = conn.execute(
        """
        SELECT k_index, a_index, sfi, sunspots, xray, condition, swpc_scales,
               source_status, source_updated_utc
        FROM rfintel_solar_15m
        WHERE bucket_start_utc=?
        """,
        (bucket_start,),
    ).fetchone()

    band_rows = conn.execute(
        """
        SELECT band, score, confidence, status, mode, spot_count
        FROM rfintel_band_15m
        WHERE bucket_start_utc=?
        ORDER BY score DESC, band ASC
        """,
        (bucket_start,),
    ).fetchall()

    wx_row = conn.execute(
        """
        SELECT temperature_f, temperature_c, wind_speed, wind_direction, condition,
               source, stale, reason, source_updated_ms, grid_id, grid_x, grid_y
        FROM rfintel_weather_15m
        WHERE bucket_start_utc=?
        """,
        (bucket_start,),
    ).fetchone()

    return {
        "bucket_start_utc": bucket["bucket_start_utc"],
        "bucket_end_utc": bucket["bucket_end_utc"],
        "bucket_minutes": BUCKET_MINUTES,
        "sources": {
            "solar": solar_status,
            "bands": bands_status,
            "weather": weather_status,
            "contacts": "not_enabled",
        },
        "solar": dict(solar_row) if solar_row else None,
        "bands": [dict(r) for r in band_rows],
        "contacts": {
            "enabled": False,
            "bucket_count": 0,
        },
        "weather": dict(wx_row) if wx_row else None,
        "updated_utc": iso_z(utc_now()),
    }


def recent_model(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT bucket_start_utc, bucket_end_utc, k_index, a_index, sfi, sunspots,
               xray, condition
        FROM rfintel_solar_15m
        ORDER BY bucket_start_utc DESC
        LIMIT ?
        """,
        (RECENT_BUCKETS,),
    ).fetchall()

    items: List[Dict[str, Any]] = []
    for row in reversed(rows):
        d = dict(row)
        tb = top_band_for_bucket(conn, d["bucket_start_utc"])
        d["top_band"] = tb.get("band") if tb else None
        d["top_score"] = tb.get("score") if tb else None
        d["contact_count"] = 0
        items.append(d)

    return {
        "bucket_minutes": BUCKET_MINUTES,
        "window_buckets": RECENT_BUCKETS,
        "window_hours": round((RECENT_BUCKETS * BUCKET_MINUTES) / 60.0, 2),
        "items": items,
        "updated_utc": iso_z(utc_now()),
    }


def status_model(
    conn: sqlite3.Connection,
    status: str,
    solar_status: str,
    bands_status: str,
    weather_status: str,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "db_path": str(DB_PATH),
        "bucket_minutes": BUCKET_MINUTES,
        "retention_days": RETENTION_DAYS,
        "recent_buckets": RECENT_BUCKETS,
        "last_sample_utc": get_meta(conn, "last_sample_utc"),
        "last_prune_utc": get_meta(conn, "last_prune_utc"),
        "sources": {
            "solar": solar_status,
            "bands": bands_status,
            "weather": weather_status,
            "contacts": "not_enabled",
        },
        "records": record_counts(conn),
        "error": error,
        "updated_utc": iso_z(utc_now()),
    }


def write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = iso_z(utc_now())
    conn.execute(
        """
        INSERT INTO rfintel_trend_meta(key, value, updated_utc)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_utc=excluded.updated_utc
        """,
        (key, value, now),
    )


# ---------------------------------------------------------------------------
# Main sample cycle
# ---------------------------------------------------------------------------

def sample_once(r: redis.Redis, conn: sqlite3.Connection, do_prune: bool = True) -> Dict[str, Any]:
    now = utc_now()
    bucket = bucket_parts(bucket_start_for(now))

    solar, solar_status = read_json_key(r, KEY_SOLAR)
    bands, bands_status = read_json_key(r, KEY_BANDS)
    weather, weather_status = read_hash_key(r, KEY_WEATHER)

    wrote_solar = upsert_solar(conn, bucket, solar)
    wrote_bands = upsert_bands(conn, bucket, bands)
    wrote_weather = upsert_weather(conn, bucket, weather)

    write_meta(conn, "last_sample_utc", iso_z(now))

    prune_result: Dict[str, int] = {}
    if do_prune:
        prune_result = prune_retention(conn)

    conn.commit()

    current = current_model(conn, bucket, solar_status, bands_status, weather_status)
    recent = recent_model(conn)
    status = status_model(conn, "ok", solar_status, bands_status, weather_status)

    changed: List[str] = []
    set_json_if_changed(r, KEY_TREND_CURRENT, current, changed)
    set_json_if_changed(r, KEY_TREND_RECENT, recent, changed)
    set_json_if_changed(r, KEY_TREND_STATUS, status, changed)

    if changed:
        publish_state_changed(r, changed)

    summary = {
        "bucket_start_utc": bucket["bucket_start_utc"],
        "wrote": {
            "solar": wrote_solar,
            "bands": wrote_bands,
            "band_rows": wrote_bands,
            "weather": wrote_weather,
            "contacts": False,
        },
        "sources": {
            "solar": solar_status,
            "bands": bands_status,
            "weather": weather_status,
            "contacts": "not_enabled",
        },
        "prune": prune_result,
        "changed_keys": changed,
    }
    return summary


def publish_degraded_status(r: redis.Redis, error: str) -> None:
    obj = {
        "status": "degraded",
        "db_path": str(DB_PATH),
        "bucket_minutes": BUCKET_MINUTES,
        "retention_days": RETENTION_DAYS,
        "sources": {
            "solar": "unknown",
            "bands": "unknown",
            "weather": "unknown",
            "contacts": "not_enabled",
        },
        "records": {
            "solar_15m": 0,
            "band_15m": 0,
            "weather_15m": 0,
            "contacts_15m": 0,
        },
        "error": cap_text(error, 240),
        "updated_utc": iso_z(utc_now()),
    }
    changed: List[str] = []
    set_json_if_changed(r, KEY_TREND_STATUS, obj, changed)
    if changed:
        publish_state_changed(r, changed)


# ---------------------------------------------------------------------------
# Export/admin helper commands
# ---------------------------------------------------------------------------

def export_json(conn: sqlite3.Connection, out_file: Path) -> None:
    data: Dict[str, Any] = {}
    for table in ("rfintel_solar_15m", "rfintel_band_15m", "rfintel_weather_15m", "rfintel_trend_meta"):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        data[table] = [dict(r) for r in rows]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def export_csv(conn: sqlite3.Connection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in ("rfintel_solar_15m", "rfintel_band_15m", "rfintel_weather_15m", "rfintel_trend_meta"):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        path = out_dir / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            if not rows:
                f.write("")
                continue
            writer = csv.DictWriter(f, fieldnames=list(dict(rows[0]).keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))


def clear_all_trends(conn: sqlite3.Connection) -> None:
    for table in ("rfintel_solar_15m", "rfintel_band_15m", "rfintel_weather_15m", "rfintel_trend_meta"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RollingThunder RF Intel lightweight trend recorder")
    p.add_argument("--once", action="store_true", help="Take one sample and exit.")
    p.add_argument("--status", action="store_true", help="Print trend recorder status JSON and exit.")
    p.add_argument("--export-json", metavar="FILE", help="Export trend DB to one JSON file.")
    p.add_argument("--export-csv", metavar="DIR", help="Export trend DB tables to CSV files in directory.")
    p.add_argument("--prune-before", metavar="UTC_DATE", help="Prune records before UTC date/time, e.g. 2026-01-01 or 2026-01-01T00:00:00Z.")
    p.add_argument("--clear-all-trends", action="store_true", help="Clear all trend tables. Requires --confirm-clear-all-trends.")
    p.add_argument("--confirm-clear-all-trends", action="store_true", help="Confirmation guard for --clear-all-trends.")
    return p.parse_args(argv)


def normalize_prune_cutoff(value: str) -> str:
    s = str(value).strip()
    if not s:
        raise ValueError("empty prune cutoff")
    if len(s) == 10:
        s = s + "T00:00:00Z"
    dt = parse_isoish_utc(s)
    if not dt:
        raise ValueError(f"invalid UTC date/time: {value}")
    return iso_z(dt)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    r = redis_client()
    r.ping()

    try:
        conn = connect_db(recover_corrupt=True)
    except Exception as e:
        publish_degraded_status(r, f"db_open_failed:{type(e).__name__}")
        raise

    with conn:
        if args.status:
            obj = status_model(conn, "ok", "not_sampled", "not_sampled", "not_sampled")
            print(json.dumps(obj, indent=2, sort_keys=True))
            return 0

        if args.export_json:
            export_json(conn, Path(args.export_json))
            print(f"Exported JSON to {args.export_json}")
            return 0

        if args.export_csv:
            export_csv(conn, Path(args.export_csv))
            print(f"Exported CSV files to {args.export_csv}")
            return 0

        if args.prune_before:
            cutoff = normalize_prune_cutoff(args.prune_before)
            result = prune_older_than(conn, cutoff)
            print(json.dumps({"pruned_before": cutoff, "deleted": result}, indent=2, sort_keys=True))
            return 0

        if args.clear_all_trends:
            if not args.confirm_clear_all_trends:
                print("Refusing to clear trends without --confirm-clear-all-trends", file=sys.stderr)
                return 2
            clear_all_trends(conn)
            print("Cleared all RF Intel trend tables.")
            return 0

        if args.once:
            summary = sample_once(r, conn, do_prune=True)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        logger.info("Starting %s interval=%ss db=%s", SERVICE_NAME, INTERVAL_SEC, DB_PATH)

        while True:
            try:
                summary = sample_once(r, conn, do_prune=True)
                logger.info(
                    "sample bucket=%s sources=%s changed=%s",
                    summary.get("bucket_start_utc"),
                    summary.get("sources"),
                    summary.get("changed_keys"),
                )
            except Exception as e:
                logger.exception("sample failed")
                try:
                    publish_degraded_status(r, f"sample_failed:{type(e).__name__}")
                except Exception:
                    logger.exception("failed to publish degraded status")
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn = connect_db(recover_corrupt=True)
                except Exception:
                    logger.exception("failed to reopen DB after sample failure")

            time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))