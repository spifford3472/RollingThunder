from __future__ import annotations

import os
from typing import Any, Callable, Mapping

import redis


QRZ_CACHE_PREFIX = os.environ.get("RT_QRZ_CACHE_PREFIX", "rt:hf:qrz:cache:")
QRZ_CACHE_TTL_SEC = int(os.environ.get("RT_QRZ_CACHE_TTL_SEC", str(24 * 60 * 60)))


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def normalize_callsign(call: str | None) -> str:
    if call is None:
        return ""
    return str(call).strip().upper()


def qrz_cache_key(call: str | None) -> str:
    normalized = normalize_callsign(call)
    if not normalized:
        return ""
    return f"{QRZ_CACHE_PREFIX}{normalized}"


def _address_summary(payload: Mapping[str, Any]) -> str:
    addr1 = _to_str(payload.get("addr1"))
    addr2 = _to_str(payload.get("addr2"))
    state = _to_str(payload.get("state"))
    zip_code = _to_str(payload.get("zip"))
    country = _to_str(payload.get("country"))

    city_state = ", ".join([x for x in [addr2, state] if x])
    if zip_code:
        city_state = f"{city_state} {zip_code}".strip()

    parts = [addr1, city_state, country]
    return " • ".join([x for x in parts if x])


def normalize_qrz_result(payload: Mapping[str, Any] | None) -> dict[str, str]:
    payload = payload or {}

    out = {
        "callsign": normalize_callsign(_to_str(payload.get("callsign"))),
        "name": _to_str(payload.get("name")),
        "fname": _to_str(payload.get("fname")),
        "lname": _to_str(payload.get("lname")),
        "addr1": _to_str(payload.get("addr1")),
        "addr2": _to_str(payload.get("addr2")),
        "state": _to_str(payload.get("state")),
        "zip": _to_str(payload.get("zip")),
        "country": _to_str(payload.get("country")),
        "grid": _to_str(payload.get("grid")),
        "lat": _to_str(payload.get("lat")),
        "lon": _to_str(payload.get("lon")),
        "image": _to_str(payload.get("image")),
        "class": _to_str(payload.get("class")),
        "codes": _to_str(payload.get("codes")),
        "qslmgr": _to_str(payload.get("qslmgr")),
        "email": _to_str(payload.get("email")),
        "url": _to_str(payload.get("url")),
        "status": _to_str(payload.get("status")) or "ok",
        "message": _to_str(payload.get("message")),
    }

    out["address"] = _to_str(payload.get("address")) or _address_summary(out)
    return out


def get_cached_qrz(r: redis.Redis, call: str | None) -> dict[str, str] | None:
    key = qrz_cache_key(call)
    if not key:
        return None

    raw = r.hgetall(key)
    if not raw:
        return None

    decoded = {_to_str(k): _to_str(v) for k, v in raw.items()}
    return normalize_qrz_result(decoded)


def set_cached_qrz(
    r: redis.Redis,
    call: str | None,
    value: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    key = qrz_cache_key(call)
    if not key:
        return None

    normalized = normalize_qrz_result(value)
    if not normalized.get("callsign"):
        normalized["callsign"] = normalize_callsign(call)

    r.hset(key, mapping=normalized)
    r.expire(key, QRZ_CACHE_TTL_SEC)
    return normalized


def lookup_qrz_with_cache(
    r: redis.Redis,
    call: str | None,
    fetcher: Callable[[str], Mapping[str, Any] | None],
) -> dict[str, str] | None:
    normalized_call = normalize_callsign(call)
    if not normalized_call:
        return None

    cached = get_cached_qrz(r, normalized_call)
    if cached is not None:
        return cached

    upstream = fetcher(normalized_call)
    if not upstream:
        return None

    normalized = normalize_qrz_result(upstream)
    if not normalized.get("callsign"):
        normalized["callsign"] = normalized_call

    if not any(
        normalized.get(k)
        for k in (
            "name",
            "addr1",
            "addr2",
            "state",
            "country",
            "grid",
            "lat",
            "lon",
            "image",
            "class",
        )
    ):
        return None

    set_cached_qrz(r, normalized_call, normalized)
    return normalized