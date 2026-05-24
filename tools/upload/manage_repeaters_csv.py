#!/usr/bin/env python3
"""
RollingThunder repeater CSV maintenance tool.

Phase 1 scope only:
- Local rt-controller maintenance utility
- CSV import/export
- SQLite schema creation
- No Redis, no UI, no radio control, no external APIs
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_DB = "/opt/rollingthunder/data/vhf/repeaters.sqlite3"
VALID_ACTIONS = {"Add", "Update", "Remove"}

ORIGINAL_COLUMNS = [
    "Channel_Name",
    "Channel_Type",
    "Rx_Frequency",
    "Tx_Frequency",
    "Bandwidth_kHz",
    "State",
    "RX_Tone",
    "TX_Tone",
    "Latitude",
    "Longitude",
    "Special",
]

EXPORT_COLUMNS = ORIGINAL_COLUMNS + ["Action"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_float(raw: Any, field_name: str, required: bool = True) -> Tuple[Optional[float], Optional[str]]:
    text = clean(raw)
    if text == "":
        if required:
            return None, f"{field_name} is required"
        return None, None
    try:
        return float(text), None
    except ValueError:
        return None, f"{field_name} must be numeric; got {text!r}"


def skywarn_flag(row: Dict[str, Any]) -> int:
    haystack = " ".join(
        clean(row.get(name))
        for name in ("Special", "Channel_Name", "Channel_Type")
    ).lower()
    return 1 if "skywarn" in haystack else 0


def format_float(value: Any) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return clean(value)
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def connect_db(path: str) -> sqlite3.Connection:
    parent_dir = os.path.dirname(os.path.abspath(path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repeater_imports (
            id INTEGER PRIMARY KEY,
            source_file TEXT,
            started_utc TEXT,
            finished_utc TEXT,
            mode TEXT,
            dry_run INTEGER,
            rows_read INTEGER,
            rows_added INTEGER,
            rows_updated INTEGER,
            rows_removed INTEGER,
            rows_skipped INTEGER,
            warnings INTEGER,
            errors INTEGER,
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS repeaters (
            id INTEGER PRIMARY KEY,
            channel_name TEXT NOT NULL,
            channel_type TEXT,
            rx_frequency_mhz REAL NOT NULL,
            tx_frequency_mhz REAL NOT NULL,
            bandwidth_khz REAL,
            state TEXT NOT NULL,
            rx_tone TEXT,
            tx_tone TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            lat_bucket_025 INTEGER NOT NULL,
            lon_bucket_025 INTEGER NOT NULL,
            special TEXT,
            is_skywarn INTEGER NOT NULL DEFAULT 0,
            source_file TEXT,
            source_row INTEGER,
            raw_json TEXT,
            created_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_repeaters_match
            ON repeaters(channel_name, rx_frequency_mhz, tx_frequency_mhz, state);

        CREATE INDEX IF NOT EXISTS ix_repeaters_bucket_025
            ON repeaters(lat_bucket_025, lon_bucket_025);

        CREATE INDEX IF NOT EXISTS ix_repeaters_skywarn_bucket_025
            ON repeaters(is_skywarn, lat_bucket_025, lon_bucket_025);

        CREATE INDEX IF NOT EXISTS ix_repeaters_state
            ON repeaters(state);

        CREATE INDEX IF NOT EXISTS ix_repeaters_rx_frequency_mhz
            ON repeaters(rx_frequency_mhz);
        """
    )
    conn.commit()


class Summary:
    def __init__(self) -> None:
        self.rows_read = 0
        self.rows_added = 0
        self.rows_updated = 0
        self.rows_removed = 0
        self.rows_skipped = 0
        self.warnings = 0
        self.errors = 0
        self.messages: List[str] = []

    def warn(self, source_row: Optional[int], message: str) -> None:
        self.warnings += 1
        prefix = f"row {source_row}: " if source_row is not None else ""
        self.messages.append(f"WARNING: {prefix}{message}")

    def error(self, source_row: Optional[int], message: str) -> None:
        self.errors += 1
        prefix = f"row {source_row}: " if source_row is not None else ""
        self.messages.append(f"ERROR: {prefix}{message}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rows_read": self.rows_read,
            "rows_added": self.rows_added,
            "rows_updated": self.rows_updated,
            "rows_removed": self.rows_removed,
            "rows_skipped": self.rows_skipped,
            "warnings": self.warnings,
            "errors": self.errors,
            "messages": self.messages,
        }


def validate_header(fieldnames: Optional[List[str]], default_action: Optional[str], initial_load: bool) -> Tuple[bool, List[str]]:
    messages: List[str] = []
    if not fieldnames:
        return False, ["CSV file has no header row"]

    missing = [name for name in ORIGINAL_COLUMNS if name not in fieldnames]
    if missing:
        messages.append("CSV is missing required columns: " + ", ".join(missing))

    if "Action" not in fieldnames and not default_action and not initial_load:
        messages.append(
            "CSV is missing Action column. For original RollingThunder-Repeaters.csv, rerun with --default-action Add or --initial-load."
        )

    return len(messages) == 0, messages


def row_action(row: Dict[str, Any], has_action_column: bool, default_action: Optional[str], initial_load: bool) -> str:
    if has_action_column:
        return clean(row.get("Action"))
    if default_action:
        return default_action
    if initial_load:
        return "Add"
    return ""


def normalize_row(
    row: Dict[str, Any],
    source_file: str,
    source_row: int,
    action: str,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []

    channel_name = clean(row.get("Channel_Name"))
    channel_type = clean(row.get("Channel_Type"))
    state = clean(row.get("State")).upper()
    rx_tone = clean(row.get("RX_Tone"))
    tx_tone = clean(row.get("TX_Tone"))
    special = clean(row.get("Special"))

    if not channel_name:
        errors.append("Channel_Name is required")
    if not state:
        errors.append("State is required")

    rx_frequency, err = parse_float(row.get("Rx_Frequency"), "Rx_Frequency", required=True)
    if err:
        errors.append(err)

    tx_frequency, err = parse_float(row.get("Tx_Frequency"), "Tx_Frequency", required=True)
    if err:
        errors.append(err)

    bandwidth, err = parse_float(row.get("Bandwidth_kHz"), "Bandwidth_kHz", required=False)
    if err:
        errors.append(err)

    latitude, err = parse_float(row.get("Latitude"), "Latitude", required=True)
    if err:
        errors.append(err)
    elif latitude is not None and not (-90 <= latitude <= 90):
        errors.append(f"Latitude must be between -90 and 90; got {latitude}")

    longitude, err = parse_float(row.get("Longitude"), "Longitude", required=True)
    if err:
        errors.append(err)
    elif longitude is not None and not (-180 <= longitude <= 180):
        errors.append(f"Longitude must be between -180 and 180; got {longitude}")

    if action not in VALID_ACTIONS:
        errors.append(f"Action must be one of Add, Update, Remove; got {action!r}")

    if errors:
        return None, errors

    assert rx_frequency is not None
    assert tx_frequency is not None
    assert latitude is not None
    assert longitude is not None

    normalized = {
        "channel_name": channel_name,
        "channel_type": channel_type,
        "rx_frequency_mhz": rx_frequency,
        "tx_frequency_mhz": tx_frequency,
        "bandwidth_khz": bandwidth,
        "state": state,
        "rx_tone": rx_tone,
        "tx_tone": tx_tone,
        "latitude": latitude,
        "longitude": longitude,
        "lat_bucket_025": math.floor(latitude / 0.25),
        "lon_bucket_025": math.floor(longitude / 0.25),
        "special": special,
        "is_skywarn": skywarn_flag(row),
        "source_file": os.path.basename(source_file),
        "source_row": source_row,
        "raw_json": json.dumps(row, sort_keys=True, ensure_ascii=False),
    }
    return normalized, []


def find_match(conn: sqlite3.Connection, rec: Dict[str, Any]) -> Optional[int]:
    row = conn.execute(
        """
        SELECT id FROM repeaters
        WHERE channel_name = ?
          AND rx_frequency_mhz = ?
          AND tx_frequency_mhz = ?
          AND state = ?
        """,
        (
            rec["channel_name"],
            rec["rx_frequency_mhz"],
            rec["tx_frequency_mhz"],
            rec["state"],
        ),
    ).fetchone()
    return int(row["id"]) if row else None


def add_repeater(conn: sqlite3.Connection, rec: Dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO repeaters (
            channel_name, channel_type, rx_frequency_mhz, tx_frequency_mhz,
            bandwidth_khz, state, rx_tone, tx_tone, latitude, longitude,
            lat_bucket_025, lon_bucket_025, special, is_skywarn,
            source_file, source_row, raw_json, created_utc, updated_utc
        ) VALUES (
            :channel_name, :channel_type, :rx_frequency_mhz, :tx_frequency_mhz,
            :bandwidth_khz, :state, :rx_tone, :tx_tone, :latitude, :longitude,
            :lat_bucket_025, :lon_bucket_025, :special, :is_skywarn,
            :source_file, :source_row, :raw_json, :created_utc, :updated_utc
        )
        """,
        {**rec, "created_utc": now, "updated_utc": now},
    )


def update_repeater(conn: sqlite3.Connection, repeater_id: int, rec: Dict[str, Any], now: str) -> None:
    conn.execute(
        """
        UPDATE repeaters
        SET channel_type = :channel_type,
            bandwidth_khz = :bandwidth_khz,
            rx_tone = :rx_tone,
            tx_tone = :tx_tone,
            latitude = :latitude,
            longitude = :longitude,
            lat_bucket_025 = :lat_bucket_025,
            lon_bucket_025 = :lon_bucket_025,
            special = :special,
            is_skywarn = :is_skywarn,
            source_file = :source_file,
            source_row = :source_row,
            raw_json = :raw_json,
            updated_utc = :updated_utc
        WHERE id = :id
        """,
        {**rec, "updated_utc": now, "id": repeater_id},
    )


def remove_repeater(conn: sqlite3.Connection, repeater_id: int) -> None:
    conn.execute("DELETE FROM repeaters WHERE id = ?", (repeater_id,))


def log_import(
    conn: sqlite3.Connection,
    source_file: str,
    started_utc: str,
    finished_utc: str,
    mode: str,
    dry_run: bool,
    summary: Summary,
) -> None:
    conn.execute(
        """
        INSERT INTO repeater_imports (
            source_file, started_utc, finished_utc, mode, dry_run,
            rows_read, rows_added, rows_updated, rows_removed, rows_skipped,
            warnings, errors, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            os.path.basename(source_file),
            started_utc,
            finished_utc,
            mode,
            1 if dry_run else 0,
            summary.rows_read,
            summary.rows_added,
            summary.rows_updated,
            summary.rows_removed,
            summary.rows_skipped,
            summary.warnings,
            summary.errors,
            json.dumps(summary.as_dict(), sort_keys=True, ensure_ascii=False),
        ),
    )


def import_csv(
    conn: sqlite3.Connection,
    csv_path: str,
    default_action: Optional[str],
    initial_load: bool,
    dry_run: bool,
) -> Tuple[Summary, int]:
    summary = Summary()
    started_utc = utc_now()

    if not os.path.exists(csv_path):
        summary.error(None, f"CSV file does not exist: {csv_path}")
        return summary, 2

    mode = "initial-load" if initial_load else "import"
    if default_action:
        mode += f" default-action={default_action}"
    if dry_run:
        mode += " dry-run"

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ok, header_messages = validate_header(reader.fieldnames, default_action, initial_load)
        if not ok:
            for msg in header_messages:
                summary.error(None, msg)
            return summary, 2

        has_action_column = reader.fieldnames is not None and "Action" in reader.fieldnames

        if not dry_run:
            conn.execute("BEGIN")

        try:
            for line_number, row in enumerate(reader, start=2):
                summary.rows_read += 1
                action = row_action(row, has_action_column, default_action, initial_load)
                rec, errors = normalize_row(row, csv_path, line_number, action)
                if errors:
                    summary.rows_skipped += 1
                    for error in errors:
                        summary.error(line_number, error)
                    continue

                assert rec is not None
                now = utc_now()
                match_id = find_match(conn, rec)

                if action == "Add":
                    if match_id is not None:
                        summary.rows_skipped += 1
                        summary.warn(line_number, "matching repeater already exists; Add skipped")
                    else:
                        if not dry_run:
                            add_repeater(conn, rec, now)
                        summary.rows_added += 1

                elif action == "Update":
                    if match_id is None:
                        summary.rows_skipped += 1
                        summary.warn(line_number, "matching repeater was not found; Update skipped")
                    else:
                        if not dry_run:
                            update_repeater(conn, match_id, rec, now)
                        summary.rows_updated += 1

                elif action == "Remove":
                    if match_id is None:
                        summary.rows_skipped += 1
                        summary.warn(line_number, "matching repeater was not found; Remove skipped")
                    else:
                        if not dry_run:
                            remove_repeater(conn, match_id)
                        summary.rows_removed += 1

            finished_utc = utc_now()
            if not dry_run:
                log_import(conn, csv_path, started_utc, finished_utc, mode, dry_run, summary)
                conn.commit()

        except Exception:
            if not dry_run:
                conn.rollback()
            raise

    return summary, 1 if summary.errors else 0


def export_csv(conn: sqlite3.Connection, export_path: str) -> int:
    ensure_parent_dir(export_path)
    rows = conn.execute(
        """
        SELECT channel_name, channel_type, rx_frequency_mhz, tx_frequency_mhz,
               bandwidth_khz, state, rx_tone, tx_tone, latitude, longitude, special
        FROM repeaters
        ORDER BY state, rx_frequency_mhz, channel_name
        """
    ).fetchall()

    with open(export_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "Channel_Name": row["channel_name"],
                    "Channel_Type": row["channel_type"] or "",
                    "Rx_Frequency": format_float(row["rx_frequency_mhz"]),
                    "Tx_Frequency": format_float(row["tx_frequency_mhz"]),
                    "Bandwidth_kHz": format_float(row["bandwidth_khz"]),
                    "State": row["state"],
                    "RX_Tone": row["rx_tone"] or "",
                    "TX_Tone": row["tx_tone"] or "",
                    "Latitude": format_float(row["latitude"]),
                    "Longitude": format_float(row["longitude"]),
                    "Special": row["special"] or "",
                    "Action": "",
                }
            )

    print(f"Exported {len(rows)} repeaters to {export_path}")
    return 0


def print_summary(summary: Summary, dry_run: bool) -> None:
    print("Repeater CSV import summary")
    print(f"  dry_run:      {'yes' if dry_run else 'no'}")
    print(f"  rows_read:    {summary.rows_read}")
    print(f"  added:        {summary.rows_added}")
    print(f"  updated:      {summary.rows_updated}")
    print(f"  removed:      {summary.rows_removed}")
    print(f"  skipped:      {summary.rows_skipped}")
    print(f"  warnings:     {summary.warnings}")
    print(f"  errors:       {summary.errors}")
    if summary.messages:
        print("")
        print("Messages:")
        for msg in summary.messages:
            print(f"  {msg}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import/export RollingThunder repeater CSV data into local SQLite. Phase 1 maintenance tool only."
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path. Default: {DEFAULT_DB}")

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--import", dest="import_path", help="CSV file to import")
    action_group.add_argument("--export", dest="export_path", help="CSV file to export")

    parser.add_argument(
        "--default-action",
        choices=["Add"],
        help="Use this action only when the CSV has no Action column. Intended for original CSV import.",
    )
    parser.add_argument(
        "--initial-load",
        action="store_true",
        help="Treat a CSV without Action as an initial Add load. Equivalent to --default-action Add for missing Action.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize without changing repeater rows")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.default_action and args.initial_load:
        parser.error("use either --default-action Add or --initial-load, not both")

    conn = connect_db(args.db)
    try:
        create_schema(conn)
        if args.import_path:
            summary, rc = import_csv(
                conn,
                csv_path=args.import_path,
                default_action=args.default_action,
                initial_load=args.initial_load,
                dry_run=args.dry_run,
            )
            print_summary(summary, args.dry_run)
            return rc

        if args.default_action or args.initial_load or args.dry_run:
            parser.error("--default-action, --initial-load, and --dry-run are only valid with --import")

        assert args.export_path
        return export_csv(conn, args.export_path)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())