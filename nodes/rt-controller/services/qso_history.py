#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

DEFAULT_DB_PATH = os.environ.get(
    "RT_QSO_HISTORY_DB",
    "/opt/rollingthunder/data/qso_history.sqlite3",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS qso_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  callsign TEXT NOT NULL,
  freq_hz INTEGER,
  band TEXT,
  mode TEXT,
  qso_utc TEXT NOT NULL,
  source TEXT,
  comment TEXT
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_callsign_time
ON qso_history(callsign, qso_utc DESC);
"""


def utc_now_iso_z() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_callsign(callsign: Any) -> str:
    return str(callsign or "").strip().upper()


def normalize_qso_utc(value: Any) -> str:
    text = str(value or "").strip()
    return text or utc_now_iso_z()


def coerce_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def get_default_db_path() -> str:
    return DEFAULT_DB_PATH


def ensure_qso_history_db(path: str | os.PathLike[str] | None = None) -> Path:
    db_path = Path(path or DEFAULT_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path), timeout=2.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute(SCHEMA_SQL)
        conn.execute(INDEX_SQL)
        conn.commit()

    return db_path


def insert_qso_history(
    path: str | os.PathLike[str] | None,
    callsign: Any,
    freq_hz: Any = None,
    band: Any = "",
    mode: Any = "",
    qso_utc: Any = "",
    source: Any = "",
    comment: Any = "",
) -> bool:
    call = normalize_callsign(callsign)
    if not call:
        return False

    db_path = ensure_qso_history_db(path)

    with sqlite3.connect(str(db_path), timeout=2.0) as conn:
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute(
            """
            INSERT INTO qso_history
              (callsign, freq_hz, band, mode, qso_utc, source, comment)
            VALUES
              (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call,
                coerce_int_or_none(freq_hz),
                str(band or "").strip(),
                str(mode or "").strip().upper(),
                normalize_qso_utc(qso_utc),
                str(source or "").strip(),
                str(comment or "").strip(),
            ),
        )
        conn.commit()

    return True


def get_last_qsos_for_callsign(
    path: str | os.PathLike[str] | None,
    callsign: Any,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    call = normalize_callsign(callsign)
    if not call:
        return []

    db_path = ensure_qso_history_db(path)
    safe_limit = max(1, min(int(limit or 5), 25))

    with sqlite3.connect(str(db_path), timeout=2.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT callsign, freq_hz, band, mode, qso_utc, source, comment
            FROM qso_history
            WHERE callsign = ?
            ORDER BY qso_utc DESC
            LIMIT ?
            """,
            (call, safe_limit),
        ).fetchall()

    return [dict(row) for row in rows]


def history_payload_for_callsign(
    path: str | os.PathLike[str] | None,
    callsign: Any,
    limit: int = 5,
    updated_at_ms: int | None = None,
) -> Dict[str, Any]:
    call = normalize_callsign(callsign)
    items = get_last_qsos_for_callsign(path, call, limit=limit) if call else []

    for item in items:
        freq_hz = coerce_int_or_none(item.get("freq_hz"))
        item["freq_hz"] = freq_hz
        item["freq"] = f"{freq_hz / 1000000:.3f}" if freq_hz else ""

    return {
        "source": "sqlite",
        "callsign": call,
        "items": items,
        "limit": int(limit or 5),
        "updated_at_ms": int(updated_at_ms or 0),
    }