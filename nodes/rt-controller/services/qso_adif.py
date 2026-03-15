from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


PROGRAM_ID = "RollingThunder"


def render_adif_header(program_version: str) -> str:
    """
    Render a simple ADIF header.

    The header is intentionally minimal and stable for v0.3700.
    """
    version = _major_minor_version(program_version)
    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"# ADIF export created by {PROGRAM_ID} at {created_utc}",
        _render_adif_field("PROGRAMID", PROGRAM_ID),
        _render_adif_field("PROGRAMVERSION", version),
        "<EOH>",
        "",
    ]
    return "\n".join(lines)


def render_adif_record(fields: Mapping[str, Any]) -> str:
    """
    Render one ADIF record from a deterministic mapping of field names to values.

    Blank / null values are skipped.
    Field order is preserved if the mapping is ordered; otherwise Python dict
    insertion order is used.
    """
    parts: list[str] = []

    for key, value in fields.items():
        cleaned = _clean_value(value)
        if cleaned is None:
            continue
        parts.append(_render_adif_field(key, cleaned))

    parts.append("<EOR>")
    return "".join(parts)


def canonical_qso_to_adif_records(qso: Mapping[str, Any]) -> list[str]:
    """
    Convert one canonical QSO dict into one or more ADIF record strings.

    Export-time explosion rules:
    - no my_pota_refs   -> one record
    - one my_pota_ref   -> one record
    - many my_pota_refs -> one record per my_pota_ref
    """
    my_refs = _normalize_my_pota_refs(qso.get("my_pota_refs"))

    if not my_refs:
        fields = canonical_qso_to_adif_fields(qso, my_pota_ref=None)
        return [render_adif_record(fields)]

    records: list[str] = []
    for my_ref in my_refs:
        fields = canonical_qso_to_adif_fields(qso, my_pota_ref=my_ref)
        records.append(render_adif_record(fields))
    return records


def canonical_qso_to_adif_fields(
    qso: Mapping[str, Any],
    my_pota_ref: str | None = None,
) -> OrderedDict[str, str]:
    """
    Map one canonical QSO dict to a deterministic ADIF field mapping.

    If my_pota_ref is supplied, emit MY_SIG/POTA fields for that single park.
    """
    fields: OrderedDict[str, str] = OrderedDict()

    mode, submode = _normalize_mode_submode(qso.get("mode"), qso.get("submode"))
    freq_mhz = _format_freq_mhz(qso.get("freq_hz"))

    time_on_date, time_on = _iso_utc_to_adif_date_time(qso.get("time_on_utc"))
    time_off_date, time_off = _iso_utc_to_adif_date_time(qso.get("time_off_utc"))

    # Core practical fields
    fields["CALL"] = _clean_str(qso.get("call"))
    fields["FREQ"] = freq_mhz
    fields["BAND"] = _clean_str(qso.get("band"))
    fields["MODE"] = mode
    fields["SUBMODE"] = submode

    fields["QSO_DATE"] = time_on_date
    fields["TIME_ON"] = time_on
    fields["QSO_DATE_OFF"] = time_off_date
    fields["TIME_OFF"] = time_off

    fields["RST_SENT"] = _clean_str(qso.get("rst_sent"))
    fields["RST_RCVD"] = _clean_str(qso.get("rst_rcvd"))

    fields["OPERATOR"] = _clean_str(qso.get("operator_callsign"))
    fields["STATION_CALLSIGN"] = _clean_str(qso.get("station_callsign"))
    fields["MY_GRIDSQUARE"] = _clean_str(qso.get("my_grid"))
    fields["GRIDSQUARE"] = _clean_str(qso.get("their_grid"))

    # POTA export fields
    if my_pota_ref:
        fields["MY_SIG"] = "POTA"
        fields["MY_SIG_INFO"] = my_pota_ref

    their_pota_ref = _clean_str(qso.get("their_pota_ref"))
    if their_pota_ref:
        fields["SIG"] = "POTA"
        fields["SIG_INFO"] = their_pota_ref

    fields["COMMENT"] = _clean_str(qso.get("comment"))

    return fields


def _normalize_mode_submode(mode: Any, submode: Any) -> tuple[str | None, str | None]:
    """
    Normalize mode/submode for ADIF export.

    v0.3700 rule:
    - MODE=SSB
    - SUBMODE=USB or LSB when present/appropriate

    This function stays intentionally conservative and does not try to solve
    all ADIF mode semantics yet.
    """
    mode_s = _clean_str(mode)
    submode_s = _clean_str(submode)

    if mode_s:
        mode_s = mode_s.upper()
    if submode_s:
        submode_s = submode_s.upper()

    if mode_s == "SSB":
        if submode_s in {"USB", "LSB"}:
            return "SSB", submode_s
        return "SSB", None

    return mode_s, submode_s


def _format_freq_mhz(freq_hz: Any) -> str | None:
    """
    Convert integer Hz to ADIF FREQ in MHz as a stable decimal string.

    Example:
        14250000 -> "14.250000"
    """
    if freq_hz is None:
        return None

    try:
        hz = int(freq_hz)
    except (TypeError, ValueError):
        return None

    if hz <= 0:
        return None

    mhz = hz / 1_000_000.0
    return f"{mhz:.6f}"


def _iso_utc_to_adif_date_time(value: Any) -> tuple[str | None, str | None]:
    """
    Convert an ISO UTC-ish timestamp into ADIF date/time fields.

    Returns:
        (YYYYMMDD, HHMMSS)

    Invalid or missing values return (None, None).

    Accepted examples:
    - 2026-03-15T15:04:05Z
    - 2026-03-15T15:04:05+00:00
    - 2026-03-15T15:04:05
    """
    s = _clean_str(value)
    if not s:
        return None, None

    try:
        dt = _parse_iso_datetime(s)
    except ValueError:
        return None, None

    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y%m%d"), dt_utc.strftime("%H%M%S")


def _parse_iso_datetime(value: str) -> datetime:
    """
    Parse ISO datetime with a small amount of UTC friendliness.
    """
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    dt = datetime.fromisoformat(normalized)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def _normalize_my_pota_refs(value: Any) -> list[str]:
    """
    Normalize my_pota_refs into a clean list of distinct non-blank strings,
    preserving original order.
    """
    if value is None:
        return []

    items: Iterable[Any]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in items:
        cleaned = _clean_str(item)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)

    return result


def _major_minor_version(program_version: Any) -> str:
    """
    Reduce a version string to major.minor for ADIF header export.

    Examples:
    - "0.3700" -> "0.3700"   (kept as-is if already simple)
    - "1.2.3"  -> "1.2"
    - "1"      -> "1"
    """
    s = _clean_str(program_version)
    if not s:
        return "0.0"

    parts = s.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return s


def _clean_str(value: Any) -> str | None:
    """
    Clean a value into a stripped string. Blank strings become None.
    """
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None
    return s


def _clean_value(value: Any) -> str | None:
    """
    Convert values to ADIF-safe text. Blank/null values are omitted.
    """
    cleaned = _clean_str(value)
    if cleaned is None:
        return None
    return cleaned


def _render_adif_field(name: str, value: str) -> str:
    """
    Render one ADIF field as <FIELD:length>value
    """
    field_name = str(name).strip().upper()
    field_value = str(value)
    return f"<{field_name}:{len(field_value)}>{field_value}"