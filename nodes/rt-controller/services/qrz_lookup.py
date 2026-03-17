from __future__ import annotations

from typing import Any, Callable, Mapping

import redis


QRZ_CACHE_PREFIX = "rt:qrz:"
QRZ_CACHE_TTL_SEC = 30 * 24 * 60 * 60


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


def normalize_qrz_result(payload: Mapping[str, Any] | None) -> dict[str, str]:
    payload = payload or {}
    return {
        "name": _to_str(payload.get("name")),
        "state": _to_str(payload.get("state")),
        "country": _to_str(payload.get("country")),
    }


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
    if not any(normalized.values()):
        return None

    set_cached_qrz(r, normalized_call, normalized)
    return normalized