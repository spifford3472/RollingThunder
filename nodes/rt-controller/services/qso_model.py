"""
Canonical RollingThunder QSO model for v0.3700 foundation.

This is the internal representation.
It is intentionally not ADIF-shaped.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


REQUIRED_QSO_FIELDS = {
    "qso_id": str,
    "revision": int,
    "created_utc": str,
    "call": str,
    "freq_hz": int,
    "band": str,
    "mode": str,
    "submode": str,
    "time_on_utc": str,
    "time_off_utc": str,
    "rst_sent": str,
    "rst_rcvd": str,
    "operator_callsign": str,
    "station_callsign": str,
    "my_grid": str,
    "their_grid": str,
    "my_pota_refs": list,
    "their_pota_ref": str,
    "comment": str,
    "qso_complete": str,
    "mobile_state": dict,
    "defaults_applied": dict,
    "duplicate_suspected": bool,
    "duplicate_basis": (str, type(None)),
}


def utc_now_iso_z() -> str:
    """
    Return current UTC time as ISO-8601 with Z suffix, second precision.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_qso_base() -> Dict[str, Any]:
    """
    Return a new canonical QSO object with stable defaults.
    """
    now = utc_now_iso_z()

    return {
        "qso_id": str(uuid4()),
        "revision": 1,
        "created_utc": now,
        "call": "",
        "freq_hz": 0,
        "band": "",
        "mode": "",
        "submode": "",
        "time_on_utc": now,
        "time_off_utc": now,
        "rst_sent": "",
        "rst_rcvd": "",
        "operator_callsign": "",
        "station_callsign": "",
        "my_grid": "",
        "their_grid": "",
        "my_pota_refs": [],
        "their_pota_ref": "",
        "comment": "",
        "qso_complete": "Y",
        "mobile_state": {},
        "defaults_applied": {},
        "duplicate_suspected": False,
        "duplicate_basis": None,
    }


def validate_qso_shape(qso: Dict[str, Any]) -> None:
    """
    Validate the canonical QSO shape.

    Raises:
        TypeError: if qso is not a dict
        ValueError: if required fields are missing or obviously invalid
    """
    if not isinstance(qso, dict):
        raise TypeError("QSO must be a dict")

    missing = [field for field in REQUIRED_QSO_FIELDS if field not in qso]
    if missing:
        raise ValueError(f"QSO missing required fields: {', '.join(sorted(missing))}")

    for field, expected_type in REQUIRED_QSO_FIELDS.items():
        value = qso[field]
        if not isinstance(value, expected_type):
            if isinstance(expected_type, tuple):
                expected_names = ", ".join(t.__name__ for t in expected_type)
            else:
                expected_names = expected_type.__name__
            raise ValueError(
                f"QSO field '{field}' must be of type {expected_names}; "
                f"got {type(value).__name__}"
            )

    if not qso["qso_id"].strip():
        raise ValueError("QSO field 'qso_id' must not be empty")

    if qso["revision"] < 1:
        raise ValueError("QSO field 'revision' must be >= 1")

    if qso["freq_hz"] < 0:
        raise ValueError("QSO field 'freq_hz' must be >= 0")

    if qso["qso_complete"] not in {"Y", "N"}:
        raise ValueError("QSO field 'qso_complete' must be 'Y' or 'N'")

    if not all(isinstance(x, str) for x in qso["my_pota_refs"]):
        raise ValueError("QSO field 'my_pota_refs' must contain only strings")


def clone_qso(qso: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a deep copy of a canonical QSO.
    """
    validate_qso_shape(qso)
    return deepcopy(qso)