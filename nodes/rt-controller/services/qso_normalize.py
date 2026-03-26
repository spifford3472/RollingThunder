"""
Normalization logic for RollingThunder canonical QSO drafts.

This module intentionally does not:
- write files
- read Redis
- publish events
- implement business rules
- know about ADIF

It only turns a log intent + current live state into a stable canonical QSO dict.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from qso_model import new_qso_base, validate_qso_shape

import json


# Conservative common amateur band edges in Hz.
# Enough for the foundation layer without dragging in a giant dependency octopus.
BAND_EDGES_HZ = [
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_500, 5_406_500),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("2m", 144_000_000, 148_000_000),
    ("70cm", 420_000_000, 450_000_000),
]


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _trim(value: Any) -> str:
    return _norm_str(value).strip()


def _upper_trim(value: Any) -> str:
    return _trim(value).upper()


def _normalize_callsign(value: Any) -> str:
    """
    Uppercase and trim the callsign.
    Leaves slash forms intact, e.g. W1AW/4.
    """
    return _upper_trim(value)


def _normalize_comment(value: Any) -> str:
    """
    Trim surrounding whitespace and collapse internal whitespace runs.
    """
    raw = _trim(value)
    if not raw:
        return ""
    return " ".join(raw.split())


def _normalize_mode(value: Any) -> str:
    return _upper_trim(value)


def _normalize_submode(value: Any) -> str:
    return _upper_trim(value)


def _normalize_freq_hz(value: Any) -> int:
    if value in (None, ""):
        return 0

    if isinstance(value, bool):
        raise ValueError("freq_hz cannot be boolean")

    try:
        freq = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid freq_hz value: {value!r}") from exc

    if freq < 0:
        raise ValueError("freq_hz must be >= 0")

    return freq


def _derive_band(freq_hz: int) -> str:
    if freq_hz <= 0:
        return ""

    for band_name, low_hz, high_hz in BAND_EDGES_HZ:
        if low_hz <= freq_hz <= high_hz:
            return band_name

    return ""


def _normalize_pota_refs(value: Any) -> list[str]:
    if value is None:
        return []

    items: Iterable[Any]

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []

        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
            except Exception:
                items = [value]
            else:
                if isinstance(parsed, list):
                    items = parsed
                else:
                    items = [value]
        else:
            items = [value]

    elif isinstance(value, list):
        items = value
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()

    for item in items:
        ref = _upper_trim(item)
        if not ref:
            continue
        if ref not in seen:
            out.append(ref)
            seen.add(ref)

    return out

def _as_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0

    s = str(value).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off", ""}:
        return False

    return bool(value)

def _normalize_mobile_state(motion_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    motion_state = motion_state or {}
    recent_motion = _as_boolish(motion_state.get("recent_motion", False))

    motion_free_raw = motion_state.get("motion_free_sec", 0)
    try:
        motion_free_sec = int(motion_free_raw)
    except (TypeError, ValueError):
        motion_free_sec = 0

    if motion_free_sec < 0:
        motion_free_sec = 0

    return {
        "recent_motion": recent_motion,
        "motion_free_sec": motion_free_sec,
    }


def _normalize_my_grid(
    gps_pos: Optional[Dict[str, Any]],
    operator_state: Optional[Dict[str, Any]],
) -> str:
    """
    Choose my_grid using the current architecture:

    1. Prefer GPS-derived grid6 from rt:gps:pos
    2. Fall back to GPS-derived grid4 from rt:gps:pos
    3. Fall back to operator_state.my_grid for compatibility
    4. Otherwise blank

    This function does not read Redis; callers pass in plain dicts.
    """
    gps_pos = gps_pos or {}
    operator_state = operator_state or {}

    grid6 = _upper_trim(gps_pos.get("grid6", ""))
    if grid6:
        return grid6

    grid4 = _upper_trim(gps_pos.get("grid4", ""))
    if grid4:
        return grid4

    return _upper_trim(operator_state.get("my_grid", ""))


def normalize_qso_intent(
    intent_params: Dict[str, Any] | None,
    radio_state: Dict[str, Any] | None,
    operator_state: Dict[str, Any] | None,
    motion_state: Dict[str, Any] | None,
    gps_pos: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Normalize a radio.log_qso intent plus live state into a canonical QSO draft.

    Inputs are plain dictionaries. This function does not access Redis.
    """
    intent_params = intent_params or {}
    radio_state = radio_state or {}
    operator_state = operator_state or {}
    motion_state = motion_state or {}
    gps_pos = gps_pos or {}

    qso = new_qso_base()

    intent_freq_hz = _normalize_freq_hz(intent_params.get("freq_hz", 0))
    radio_freq_hz = _normalize_freq_hz(radio_state.get("freq_hz", 0))
    freq_hz = intent_freq_hz or radio_freq_hz

    intent_band = _upper_trim(intent_params.get("band", ""))
    band = intent_band or _derive_band(freq_hz)

    intent_mode = _normalize_mode(intent_params.get("mode", ""))
    intent_submode = _normalize_submode(intent_params.get("submode", ""))

    radio_mode = _normalize_mode(radio_state.get("mode", ""))
    radio_submode = _normalize_submode(radio_state.get("submode", ""))

    qso["freq_hz"] = freq_hz
    qso["band"] = band
    qso["mode"] = intent_mode or radio_mode
    qso["submode"] = intent_submode or radio_submode
    qso["call"] = _normalize_callsign(intent_params.get("call", ""))

    qso["operator_callsign"] = _normalize_callsign(operator_state.get("operator_callsign", ""))
    qso["station_callsign"] = _normalize_callsign(operator_state.get("station_callsign", ""))
    qso["my_grid"] = _normalize_my_grid(gps_pos, operator_state)
    qso["their_grid"] = _upper_trim(intent_params.get("their_grid", ""))
    qso["my_pota_refs"] = _normalize_pota_refs(intent_params.get("my_pota_refs", operator_state.get("my_pota_refs", [])))
    qso["their_pota_ref"] = _upper_trim(intent_params.get("their_pota_ref", ""))

    qso["rst_sent"] = _trim(intent_params.get("rst_sent", ""))
    qso["rst_rcvd"] = _trim(intent_params.get("rst_rcvd", ""))

    qso["qso_complete"] = _upper_trim(intent_params.get("qso_complete", "Y")) or "Y"
    if qso["qso_complete"] not in {"Y", "N"}:
        qso["qso_complete"] = "Y"

    qso["mobile_state"] = _normalize_mobile_state(motion_state)

    defaults_applied: dict[str, Any] = {}
    if not intent_params.get("their_grid"):
        defaults_applied["their_grid"] = "empty"
    if not intent_params.get("their_pota_ref"):
        defaults_applied["their_pota_ref"] = "empty"
    if "qso_complete" not in intent_params:
        defaults_applied["qso_complete"] = "default:Y"
    if "submode" not in radio_state or not _trim(radio_state.get("submode", "")):
        defaults_applied["submode"] = "empty"
    if not qso["my_grid"]:
        defaults_applied["my_grid"] = "empty"

    qso["defaults_applied"] = defaults_applied

    validate_qso_shape(qso)
    return qso


if __name__ == "__main__":
    intent_params = {
        "call": "k8eye",
        "comment": "  POTA contact  ",
    }

    radio_state = {
        "freq_hz": 14_250_000,
        "mode": "ssb",
        "submode": "usb",
    }

    operator_state = {
        "operator_callsign": "KI5VNB",
        "station_callsign": "KI5VNB",
        "my_grid": "EM79",
        "my_pota_refs": ["US-1940", "US-7850"],
    }

    motion_state = {
        "recent_motion": True,
        "motion_free_sec": 42,
    }

    gps_pos = {
        "valid": "true",
        "lat": "39.524545",
        "lon": "-84.062443333",
        "grid4": "EM79",
        "grid6": "EM79xm",
    }

    qso = normalize_qso_intent(
        intent_params,
        radio_state,
        operator_state,
        motion_state,
        gps_pos,
    )

    import json
    print(json.dumps(qso, indent=2, sort_keys=True))