#!/usr/bin/env python3
"""
RollingThunder Phase 8.2 CSV repeater import tool.

Purpose:
- Treat the CSV file as the source of truth.
- Build a generated SQLite cache for fast runtime lookup.
- Safe/offline tool: no Redis writes, no radio control, no UI bus writes.

Expected CSV columns:
  Name, Frequency, Dup, Offset, Mode, TONE, Repeater Tone, TSQL Frequency,
  DTCS Code, DTCS Polarity, Latitude, Longitude, Skywarn, ARES, Type

Recommended usage:
  python3 tools/vhf/import_repeaters_csv_to_sqlite.py \
    --csv /opt/rollingthunder/data/vhf/REPEATER_IMPORT.csv \
    --sqlite /opt/rollingthunder/data/vhf/repeaters_cache.sqlite3
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

DEFAULT_CSV_PATH = "/opt/rollingthunder/data/vhf/REPEATER_IMPORT.csv"
DEFAULT_SQLITE_PATH = "/opt/rollingthunder/data/vhf/repeaters_cache.sqlite3"

EXPECTED_COLUMNS = [
    "Name",
    "Frequency",
    "Dup",
    "Offset",
    "Mode",
    "TONE",
    "Repeater Tone",
    "TSQL Frequency",
    "DTCS Code",
    "DTCS Polarity",
    "Latitude",
    "Longitude",
    "Skywarn",
    "ARES",
    "Type",
]


def clean_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def parse_float(value: Any) -> Optional[float]:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("Hz", "").replace("HZ", "").replace("hz", "").strip()
    try:
        parsed = float(text)
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


def parse_boolish(value: Any) -> int:
    text = clean_text(value).lower()
    if text in {"1", "true", "yes", "y", "on", "enabled", "skywarn", "ares"}:
        return 1
    if text in {"0", "false", "no", "n", "off", "disabled", ""}:
        return 0
    try:
        return 1 if float(text) != 0.0 else 0
    except Exception:
        return 0


def normalize_mode(value: Any) -> str:
    text = clean_text(value).upper()
    return text or "FM"


def normalize_service_type(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return "Repeater"
    lower = text.lower()
    if lower == "air":
        return "Air"
    if lower == "news":
        return "News"
    if lower == "repeater":
        return "Repeater"
    return text


def normalize_duplex_and_offset(dup_value: Any, offset_value: Any) -> Tuple[str, float]:
    dup = clean_text(dup_value).upper().replace(" ", "")
    raw_offset = parse_float(offset_value)
    offset_abs = abs(raw_offset or 0.0)

    if dup in {"DUP-", "-", "MINUS"}:
        return "minus", round(-offset_abs, 6)

    if dup in {"DUP+", "+", "PLUS"}:
        return "plus", round(offset_abs, 6)

    if dup in {"OFF", "NONE", "SIMPLEX", ""}:
        return "simplex", 0.0

    # Conservative fallback: preserve unknown duplex shape by using offset sign.
    if raw_offset is not None and raw_offset < 0:
        return "minus", round(raw_offset, 6)
    if raw_offset is not None and raw_offset > 0:
        return "plus", round(raw_offset, 6)
    return "simplex", 0.0


def normalize_tone_mode(tone_value: Any) -> str:
    text = clean_text(tone_value).upper().replace(" ", "")
    if text in {"", "OFF", "NONE", "NO", "0"}:
        return "off"
    if text in {"TONE", "ENC", "ENCODE", "CTCSS"}:
        return "encode"
    if text in {"TSQL", "TONE SQL", "TONESQL", "TONE-SQL", "TONE_SQUELCH"}:
        return "tone_squelch"
    if text in {"TONE(T)/TSQL(R)", "TONE(T)TSQL(R)", "TONE/TSQL"}:
        return "encode_and_tsql"
    if text in {"DTCS", "DCS", "DTCS-R", "DCS-R"}:
        return "dtcs"
    return text.lower() or "off"


def normalize_repeater_tone_hz(value: Any) -> Optional[float]:
    parsed = parse_float(value)
    if parsed is None or parsed <= 0:
        return None
    return round(parsed, 1)


def validate_headers(fieldnames: Optional[Iterable[str]]) -> list[str]:
    found = list(fieldnames or [])
    found_set = set(found)
    missing = [name for name in EXPECTED_COLUMNS if name not in found_set]
    return missing


def connect_and_initialize(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")

    conn.executescript(
        """
        DROP TABLE IF EXISTS repeaters_cache;

        CREATE TABLE repeaters_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_row INTEGER NOT NULL,

            name TEXT NOT NULL,
            frequency_mhz REAL NOT NULL,
            duplex TEXT NOT NULL,
            offset_mhz REAL NOT NULL,
            mode TEXT NOT NULL,

            tone_mode TEXT NOT NULL,
            tone_raw TEXT NOT NULL,
            repeater_tone_hz REAL,
            tsql_frequency_hz REAL,
            dtcs_code TEXT NOT NULL,
            dtcs_polarity TEXT NOT NULL,

            latitude REAL NOT NULL,
            longitude REAL NOT NULL,

            skywarn INTEGER NOT NULL DEFAULT 0,
            ares INTEGER NOT NULL DEFAULT 0,
            service_type TEXT NOT NULL,

            scan_enabled INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        );

        CREATE INDEX idx_repeaters_cache_lat_lon
            ON repeaters_cache(latitude, longitude);

        CREATE INDEX idx_repeaters_cache_service_mode
            ON repeaters_cache(service_type, mode);

        CREATE INDEX idx_repeaters_cache_scan
            ON repeaters_cache(scan_enabled, latitude, longitude);

        CREATE INDEX idx_repeaters_cache_skywarn
            ON repeaters_cache(skywarn);

        CREATE INDEX idx_repeaters_cache_ares
            ON repeaters_cache(ares);
        """
    )
    return conn


def normalize_row(row_number: int, row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    name = clean_text(row.get("Name"))
    frequency = parse_float(row.get("Frequency"))
    latitude = parse_float(row.get("Latitude"))
    longitude = parse_float(row.get("Longitude"))

    if not name:
        return None, "missing_name"
    if frequency is None or frequency <= 0:
        return None, "invalid_frequency"
    if latitude is None or not (-90.0 <= latitude <= 90.0):
        return None, "invalid_latitude"
    if longitude is None or not (-180.0 <= longitude <= 180.0):
        return None, "invalid_longitude"

    duplex, offset_mhz = normalize_duplex_and_offset(row.get("Dup"), row.get("Offset"))
    mode = normalize_mode(row.get("Mode"))
    service_type = normalize_service_type(row.get("Type"))
    tone_raw = clean_text(row.get("TONE"))
    tone_mode = normalize_tone_mode(tone_raw)
    repeater_tone_hz = normalize_repeater_tone_hz(row.get("Repeater Tone"))
    tsql_frequency_hz = normalize_repeater_tone_hz(row.get("TSQL Frequency"))
    skywarn = parse_boolish(row.get("Skywarn"))
    ares = parse_boolish(row.get("ARES"))

    scan_enabled = 1 if service_type == "Repeater" and mode == "FM" else 0

    normalized = {
        "source_row": row_number,
        "name": name,
        "frequency_mhz": round(float(frequency), 6),
        "duplex": duplex,
        "offset_mhz": round(float(offset_mhz), 6),
        "mode": mode,
        "tone_mode": tone_mode,
        "tone_raw": tone_raw,
        "repeater_tone_hz": repeater_tone_hz,
        "tsql_frequency_hz": tsql_frequency_hz,
        "dtcs_code": clean_text(row.get("DTCS Code")),
        "dtcs_polarity": clean_text(row.get("DTCS Polarity")),
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "skywarn": skywarn,
        "ares": ares,
        "service_type": service_type,
        "scan_enabled": scan_enabled,
        "raw_json": json.dumps(row, sort_keys=True, separators=(",", ":")),
    }
    return normalized, None


def insert_row(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO repeaters_cache (
            source_row,
            name,
            frequency_mhz,
            duplex,
            offset_mhz,
            mode,
            tone_mode,
            tone_raw,
            repeater_tone_hz,
            tsql_frequency_hz,
            dtcs_code,
            dtcs_polarity,
            latitude,
            longitude,
            skywarn,
            ares,
            service_type,
            scan_enabled,
            raw_json
        )
        VALUES (
            :source_row,
            :name,
            :frequency_mhz,
            :duplex,
            :offset_mhz,
            :mode,
            :tone_mode,
            :tone_raw,
            :repeater_tone_hz,
            :tsql_frequency_hz,
            :dtcs_code,
            :dtcs_polarity,
            :latitude,
            :longitude,
            :skywarn,
            :ares,
            :service_type,
            :scan_enabled,
            :raw_json
        )
        """,
        item,
    )


def import_csv(csv_path: Path, sqlite_path: Path) -> Dict[str, Any]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=sqlite_path.name + ".",
        suffix=".tmp",
        dir=str(sqlite_path.parent if sqlite_path.parent.exists() else Path(".")),
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)

    stats: Dict[str, Any] = {
        "csv_path": str(csv_path),
        "sqlite_path": str(sqlite_path),
        "rows_read": 0,
        "rows_loaded": 0,
        "rows_skipped": 0,
        "skip_reasons": {},
        "by_type": {},
        "by_mode": {},
        "skywarn_count": 0,
        "ares_count": 0,
        "scan_enabled_count": 0,
    }

    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = validate_headers(reader.fieldnames)
            if missing:
                raise RuntimeError("CSV missing expected columns: " + ", ".join(missing))

            conn = connect_and_initialize(temp_path)
            try:
                with conn:
                    for row_number, row in enumerate(reader, start=2):
                        stats["rows_read"] += 1
                        item, error = normalize_row(row_number, row)
                        if error or item is None:
                            stats["rows_skipped"] += 1
                            stats["skip_reasons"][error or "unknown"] = stats["skip_reasons"].get(error or "unknown", 0) + 1
                            continue

                        insert_row(conn, item)
                        stats["rows_loaded"] += 1
                        stats["by_type"][item["service_type"]] = stats["by_type"].get(item["service_type"], 0) + 1
                        stats["by_mode"][item["mode"]] = stats["by_mode"].get(item["mode"], 0) + 1
                        stats["skywarn_count"] += int(item["skywarn"])
                        stats["ares_count"] += int(item["ares"])
                        stats["scan_enabled_count"] += int(item["scan_enabled"])

                conn.execute("PRAGMA user_version = 1")
                conn.execute("VACUUM")
            finally:
                conn.close()

        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(sqlite_path)
        return stats

    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build RollingThunder generated SQLite repeater cache from CSV."
    )
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH, help=f"CSV source path. Default: {DEFAULT_CSV_PATH}")
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE_PATH, help=f"SQLite cache path. Default: {DEFAULT_SQLITE_PATH}")
    parser.add_argument("--summary-json", default="", help="Optional path to write import summary JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    sqlite_path = Path(args.sqlite)

    try:
        stats = import_csv(csv_path, sqlite_path)
    except Exception as exc:
        print(f"ERROR: import failed: {exc}", file=sys.stderr)
        return 1

    summary = json.dumps(stats, indent=2, sort_keys=True)
    print(summary)

    if args.summary_json:
        Path(args.summary_json).write_text(summary + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
