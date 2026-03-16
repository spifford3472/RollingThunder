from __future__ import annotations

from typing import Any, Callable, Mapping

import redis


QRZ_CACHE_PREFIX = "rt:qrz:"
QRZ_CACHE_TTL_SEC = 30 * 24 * 60 * 60


def _to_str(value: Any) -> str:
    """Convert Redis/upstream values to a clean string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def normalize_callsign(call: str | None) -> str:
    """Normalize a callsign for cache lookup/use."""
    if call is None:
        return ""
    return str(call).strip().upper()


def qrz_cache_key(call: str | None) -> str:
    """
    Build the Redis cache key for a callsign.

    Returns empty string for blank/invalid input so callers can stay simple
    and explicitly handle blank calls without exceptions.
    """
    normalized = normalize_callsign(call)
    if not normalized:
        return ""
    return f"{QRZ_CACHE_PREFIX}{normalized}"


def normalize_qrz_result(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """
    Normalize an upstream QRZ payload into the minimal RollingThunder cache shape.

    Only keeps:
      - name
      - state
      - country

    Missing or null values become empty strings.
    """
    payload = payload or {}
    return {
        "name": _to_str(payload.get("name")),
        "state": _to_str(payload.get("state")),
        "country": _to_str(payload.get("country")),
    }


def get_cached_qrz(r: redis.Redis, call: str | None) -> dict[str, str] | None:
    """
    Read a cached QRZ lookup from Redis.

    Returns:
      - normalized 3-field dict on hit
      - None on blank callsign or cache miss
    """
    key = qrz_cache_key(call)
    if not key:
        return None

    raw = r.hgetall(key)
    if not raw:
        return None

    # hgetall may return bytes->bytes or str->str depending on Redis client config
    decoded = {_to_str(k): _to_str(v) for k, v in raw.items()}
    return normalize_qrz_result(decoded)


def set_cached_qrz(
    r: redis.Redis,
    call: str | None,
    value: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """
    Write a normalized QRZ lookup into Redis with TTL.

    Returns:
      - normalized stored dict on success
      - None on blank callsign
    """
    key = qrz_cache_key(call)
    if not key:
        return None

    normalized = normalize_qrz_result(value)

    # Store only the minimal normalized shape
    r.hset(key, mapping=normalized)
    r.expire(key, QRZ_CACHE_TTL_SEC)

    return normalized


def lookup_qrz_with_cache(
    r: redis.Redis,
    call: str | None,
    fetcher: Callable[[str], Mapping[str, Any] | None],
) -> dict[str, str] | None:
    """
    Cache-aware QRZ lookup.

    Behavior:
      - blank callsign -> None
      - cache hit -> return cached value, do not call fetcher
      - cache miss -> call fetcher(normalized_call) once
      - unusable upstream result -> return None, do not cache garbage
      - successful upstream result -> normalize, cache, return
    """
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

    # Simple guard against poisoning cache with completely empty data
    if not any(normalized.values()):
        return None

    set_cached_qrz(r, normalized_call, normalized)
    return normalized