"""
RollingThunder canonical QSO storage for v0.3700.

Purpose:
    Persist canonical QSO records and provide simple recent lookup helpers.

This module:
    - uses logging.log_dir from rt_config
    - creates directory/files if missing
    - appends canonical JSONL safely
    - supports recent-QSO lookup
    - supports simple ADIF text append helper
    - does not know about Redis, UI, or daemon orchestration
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from . import rt_config


CanonicalQSO = Dict[str, Any]

QSO_JSONL_FILENAME = "rollingthunder.qso.jsonl"
ADIF_FILENAME = "rollingthunder.adi"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_log_dir() -> Path:
    """
    Resolve the configured logging directory via rt_config.

    Expected existing config contract:
        logging.log_dir from app.json
    Default:
        /opt/rollingthunder/data/logs
    """
    # Assumes existing helper contract from Step 1-4 foundation.
    # If your rt_config helper uses a different function name, adjust only this line.
    cfg = rt_config.get_app_config()
    log_dir = (
        cfg.get("logging", {}).get("log_dir")
        or "/opt/rollingthunder/data/logs"
    )
    return Path(log_dir)


def ensure_log_dir() -> Path:
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_qso_jsonl_path() -> Path:
    return ensure_log_dir() / QSO_JSONL_FILENAME


def get_adif_path() -> Path:
    return ensure_log_dir() / ADIF_FILENAME


def _touch_if_missing(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _adif_header_text() -> str:
    version = rt_config.get_program_version()
    created_utc = _utc_now_iso()
    lines = [
        f"Created by RollingThunder v{version} on {created_utc}",
        "<PROGRAMID:14>RollingThunder",
        f"<PROGRAMVERSION:{len(version)}>{version}",
        "<EOH>",
        "",
    ]
    return "\n".join(lines)


def ensure_adif_header() -> Path:
    """
    Ensure the ADIF file exists and contains a header exactly once.
    """
    path = get_adif_path()
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_adif_header_text())
            fh.flush()
    return path


def append_canonical_qso(qso: Mapping[str, Any]) -> Path:
    """
    Append one canonical QSO record as a single JSON object line.
    """
    path = get_qso_jsonl_path()
    _touch_if_missing(path)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(qso), ensure_ascii=False, sort_keys=True))
        fh.write("\n")
        fh.flush()

    return path


def iter_recent_qsos(limit: Optional[int] = 100) -> List[CanonicalQSO]:
    """
    Return recent canonical QSO records, newest first.

    Practical v0.3700 implementation:
        read JSONL, parse valid lines, reverse, then slice.
    """
    path = get_qso_jsonl_path()
    if not path.exists():
        return []

    records: List[CanonicalQSO] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                # Ignore malformed trailing junk instead of exploding the tractor.
                continue

    records.reverse()  # newest first

    if limit is None or limit <= 0:
        return records

    return records[:limit]


def _normalized_compare_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _is_probable_duplicate(candidate: Mapping[str, Any], prior: Mapping[str, Any]) -> bool:
    return (
        _normalized_compare_value(candidate.get("call"))
        == _normalized_compare_value(prior.get("call"))
        and _normalized_compare_value(candidate.get("band"))
        == _normalized_compare_value(prior.get("band"))
        and _normalized_compare_value(candidate.get("mode"))
        == _normalized_compare_value(prior.get("mode"))
    )


def find_probable_duplicates(
    qso: Mapping[str, Any],
    limit: int = 100,
) -> List[CanonicalQSO]:
    """
    Return recent canonical records that match the v0.3700 duplicate basis:
        same call + same band + same mode
    """
    matches: List[CanonicalQSO] = []
    for prior in iter_recent_qsos(limit=limit):
        if _is_probable_duplicate(qso, prior):
            matches.append(prior)
    return matches


def append_adif_text(text: str) -> Path:
    """
    Append raw ADIF text to the ADIF file after ensuring the header exists.
    This is a primitive for later integration; it does not render ADIF here.
    """
    path = ensure_adif_header()

    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.flush()

    return path


if __name__ == "__main__":
    import json
    import tempfile

    # Demo monkeypatching note:
    # This block patches rt_config access only for local smoke testing,
    # without changing production function signatures.

    tmp_root = Path(tempfile.mkdtemp(prefix="rt-qso-storage-"))
    demo_log_dir = tmp_root / "logs"

    original_get_app_config = rt_config.get_app_config
    original_get_program_version = rt_config.get_program_version

    try:
        rt_config.get_app_config = lambda: {"logging": {"log_dir": str(demo_log_dir)}}  # type: ignore[assignment]
        rt_config.get_program_version = lambda: "0.3700-demo"  # type: ignore[assignment]

        print(f"Demo log dir: {demo_log_dir}")

        qso1 = {
            "qso_id": "demo-001",
            "call": "K1ABC",
            "band": "20m",
            "mode": "SSB",
            "rst_sent": "59",
            "rst_rcvd": "59",
            "qso_complete": "Y",
        }
        qso2 = {
            "qso_id": "demo-002",
            "call": "W8XYZ",
            "band": "40m",
            "mode": "SSB",
            "rst_sent": "57",
            "rst_rcvd": "44",
            "qso_complete": "Y",
        }

        append_canonical_qso(qso1)
        append_canonical_qso(qso2)
        append_adif_text("<CALL:5>K1ABC<EOR>")
        append_adif_text("<CALL:5>W8XYZ<EOR>")

        print("\nRecent QSOs:")
        print(json.dumps(iter_recent_qsos(limit=10), indent=2, sort_keys=True))

        print("\nProbable duplicates for K1ABC 20m SSB:")
        matches = find_probable_duplicates(
            {"call": "K1ABC", "band": "20m", "mode": "SSB"},
            limit=10,
        )
        print(json.dumps(matches, indent=2, sort_keys=True))

        print("\nJSONL path:", get_qso_jsonl_path())
        print("ADIF path:", get_adif_path())
        print("ADIF contents:")
        print(get_adif_path().read_text(encoding="utf-8"))

    finally:
        rt_config.get_app_config = original_get_app_config  # type: ignore[assignment]
        rt_config.get_program_version = original_get_program_version  # type: ignore[assignment]