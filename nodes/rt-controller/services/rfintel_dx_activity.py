#!/usr/bin/env python3
"""
RollingThunder - RF Intel DX Activity Summary

Controller-side only.

Reads:
- rt:hf:bands
- rt:hf:spots:<band>
- rt:hf:spots:selected as fallback
- rt:rfintel:dx_activity as previous model for conservative trend classification

Writes:
- rt:rfintel:dx_activity

Publishes:
- rt:system:bus state.changed only when rt:rfintel:dx_activity semantically changes

Rules:
- UI remains renderer-only.
- Browser does not poll DX Spider.
- Browser does not scan Redis.
- Browser does not dedupe/filter/score spots.
- Service does not write to rt:ui:bus.
- Projector remains the only writer to rt:ui:bus.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import redis


SERVICE_NAME = "rfintel_dx_activity"
SERVICE_VERSION = "0.1.0"

DEFAULT_BAND_ORDER = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"]

KEY_HF_BANDS = "rt:hf:bands"
KEY_HF_SPOTS_SELECTED = "rt:hf:spots:selected"
KEY_DX_ACTIVITY = "rt:rfintel:dx_activity"

CALLSIGN_RE = re.compile(r"^[A-Z0-9][A-Z0-9/]{2,15}$")

# Broad amateur HF band guards. The HF DX poller already does SSB segment filtering;
# these ranges are a second conservative sanity check, not the primary band plan.
BAND_RANGES_HZ: Dict[str, Tuple[int, int]] = {
    "160m": (1_800_000, 2_000_000),
    "80m": (3_500_000, 4_000_000),
    "60m": (5_330_000, 5_407_000),
    "40m": (7_000_000, 7_300_000),
    "30m": (10_100_000, 10_150_000),
    "20m": (14_000_000, 14_350_000),
    "17m": (18_068_000, 18_168_000),
    "15m": (21_000_000, 21_450_000),
    "12m": (24_890_000, 24_990_000),
    "10m": (28_000_000, 29_700_000),
    "6m": (50_000_000, 54_000_000),
}


RUNNING = True


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def stable_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


def redis_client() -> redis.Redis:
    explicit_url = env_str("RT_REDIS_URL", "")
    if explicit_url:
        redis_url = explicit_url
    else:
        host = env_str("RT_REDIS_HOST", "127.0.0.1")
        port = env_int("RT_REDIS_PORT", 6379)
        db = env_int("RT_REDIS_DB", 0)
        password = env_str("RT_REDIS_PASSWORD", "")

        if password:
            redis_url = f"redis://:{password}@{host}:{port}/{db}"
        else:
            redis_url = f"redis://{host}:{port}/{db}"

    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
        health_check_interval=15,
    )


def key_prefix() -> str:
    return env_str("RT_KEY_PREFIX", "rt")


def prefixed(prefix: str, suffix: str) -> str:
    if suffix.startswith(prefix + ":"):
        return suffix
    if suffix.startswith("rt:") and prefix == "rt":
        return suffix
    if suffix.startswith("rt:") and prefix != "rt":
        return prefix + suffix[2:]
    return f"{prefix}:{suffix}"


def read_json_key(r: redis.Redis, key: str, default: Any) -> Tuple[Any, str]:
    try:
        raw = r.get(key)
    except Exception:
        return default, "error"

    if not raw:
        return default, "missing"

    try:
        obj = json.loads(raw)
    except Exception:
        return default, "invalid_json"

    return obj, "ok"


def parse_iso_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def clean_callsign(value: Any) -> str:
    call = str(value or "").strip().upper()
    call = re.sub(r"[^A-Z0-9/]", "", call)
    return call


def valid_callsign(call: str) -> bool:
    if not call:
        return False
    if not CALLSIGN_RE.match(call):
        return False
    if call in {"NOCALL", "NO CALL", "TEST", "UNKNOWN"}:
        return False
    return True


def clean_band(value: Any) -> str:
    band = str(value or "").strip().lower()
    if not band:
        return ""
    return band


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def freq_bucket_hz(freq_hz: int) -> int:
    # Match the HF poller's duplicate identity: same station on same band,
    # rounded to the nearest 1 kHz.
    return int(round(freq_hz / 1000.0) * 1000)


def freq_matches_band(freq_hz: int, band: str) -> bool:
    rng = BAND_RANGES_HZ.get(band)
    if not rng:
        return bool(freq_hz > 0)
    low, high = rng
    return low <= freq_hz <= high


def spot_sources(spot: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()

    source = str(spot.get("source") or "").strip()
    if source:
        out.add(source)

    sources = spot.get("sources")
    if isinstance(sources, list):
        for src in sources:
            text = str(src or "").strip()
            if text:
                out.add(text)

    return out


def iter_band_names(hf_bands: Dict[str, Any]) -> List[str]:
    bands: List[str] = []
    seen: Set[str] = set()

    candidates = hf_bands.get("items")
    if not isinstance(candidates, list):
        candidates = hf_bands.get("bands")

    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            band = clean_band(item.get("band") or item.get("id"))
            if band and band not in seen:
                seen.add(band)
                bands.append(band)

    bands.sort(
        key=lambda b: (
            DEFAULT_BAND_ORDER.index(b)
            if b in DEFAULT_BAND_ORDER
            else 999,
            b,
        )
    )

    return bands


def model_spots(model: Any) -> List[Dict[str, Any]]:
    if not isinstance(model, dict):
        return []

    items = model.get("items")
    if not isinstance(items, list):
        items = model.get("spots")

    if not isinstance(items, list):
        return []

    return [item for item in items if isinstance(item, dict)]


def collect_hf_spots(r: redis.Redis, prefix: str) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    hf_bands_key = prefixed(prefix, KEY_HF_BANDS)
    selected_key = prefixed(prefix, KEY_HF_SPOTS_SELECTED)

    hf_bands, bands_status = read_json_key(r, hf_bands_key, {})
    if not isinstance(hf_bands, dict):
        hf_bands = {}

    band_names = iter_band_names(hf_bands)
    all_spots: List[Dict[str, Any]] = []

    per_band_status = "missing"

    for band in band_names:
        key = prefixed(prefix, f"rt:hf:spots:{band}")
        band_model, status = read_json_key(r, key, {})
        if status == "ok":
            per_band_status = "ok"
        elif per_band_status != "ok" and status == "invalid_json":
            per_band_status = "invalid_json"

        for spot in model_spots(band_model):
            row = dict(spot)
            row.setdefault("band", band)
            all_spots.append(row)

    selected_model, selected_status = read_json_key(r, selected_key, {})
    selected_spots = model_spots(selected_model)

    # Fallback if per-band models are absent or empty.
    if not all_spots and selected_spots:
        for spot in selected_spots:
            all_spots.append(dict(spot))

    input_status = {
        "hf_bands": bands_status,
        "hf_spots": "ok" if all_spots else selected_status,
        "hf_spots_per_band": per_band_status,
    }

    return all_spots, input_status


def normalize_valid_spot(spot: Dict[str, Any], now: datetime, max_age_minutes: int) -> Optional[Dict[str, Any]]:
    call = clean_callsign(spot.get("callsign") or spot.get("call"))
    if not valid_callsign(call):
        return None

    band = clean_band(spot.get("band"))
    if not band:
        return None

    freq_hz = to_int(spot.get("freq_hz") or spot.get("sort_hz"), 0)
    if freq_hz <= 0:
        # Accept MHz string fallback only if needed.
        try:
            freq_mhz = float(str(spot.get("freq") or "").strip())
            freq_hz = int(round(freq_mhz * 1_000_000))
        except Exception:
            freq_hz = 0

    if freq_hz <= 0:
        return None

    if not freq_matches_band(freq_hz, band):
        return None

    spotted_dt = parse_iso_utc(spot.get("spotted_utc") or spot.get("spot_ts_utc") or spot.get("updated_utc"))
    if spotted_dt is None:
        # Current HF poller normally provides spotted_utc. If missing, treat as
        # current so the service degrades gracefully instead of discarding all data.
        spotted_dt = now

    age_sec = max(0, int((now - spotted_dt).total_seconds()))
    if age_sec > max_age_minutes * 60:
        return None

    spotter = clean_callsign(spot.get("spotter"))
    if spotter and not valid_callsign(spotter):
        spotter = ""

    return {
        "id": str(spot.get("id") or ""),
        "callsign": call,
        "band": band,
        "freq_hz": freq_hz,
        "freq_bucket_hz": freq_bucket_hz(freq_hz),
        "spotter": spotter,
        "source": str(spot.get("source") or "").strip(),
        "sources": sorted(spot_sources(spot)),
        "spotted_utc": spotted_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_sec": age_sec,
    }


def dedupe_for_activity(spots: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}

    for spot in spots:
        key = f"{spot['band']}:{spot['callsign']}:{spot['freq_bucket_hz']}"
        existing = best.get(key)

        if existing is None:
            row = dict(spot)
            row["_spotters"] = set([spot["spotter"]]) if spot.get("spotter") else set()
            row["_sources"] = set(spot.get("sources") or [])
            best[key] = row
            continue

        if spot.get("age_sec", 999999) < existing.get("age_sec", 999999):
            existing.update(
                {
                    "id": spot.get("id", existing.get("id", "")),
                    "freq_hz": spot.get("freq_hz", existing.get("freq_hz", 0)),
                    "spotted_utc": spot.get("spotted_utc", existing.get("spotted_utc", "")),
                    "age_sec": spot.get("age_sec", existing.get("age_sec", 999999)),
                }
            )

        if spot.get("spotter"):
            existing.setdefault("_spotters", set()).add(spot["spotter"])

        existing.setdefault("_sources", set()).update(spot.get("sources") or [])

    out = list(best.values())

    for row in out:
        row["spotters"] = sorted(row.pop("_spotters", set()))
        row["sources"] = sorted(row.pop("_sources", set()))

    return out


def freshness_score(age_sec_values: List[int]) -> int:
    """
    V1 deliberately avoids countdown-based score drift.

    The current HF DX Spider poller records spotted_utc as the RollingThunder
    poll time, not the original DX cluster spot time. If we score continuously
    from that timestamp, rt:rfintel:dx_activity can churn even when the actual
    spot set is unchanged.

    Freshness is still enforced by the max-age/window filter before scoring.
    """
    return 0


def classify_strength(score: int) -> str:
    if score >= 75:
        return "strong"
    if score >= 50:
        return "moderate"
    if score >= 25:
        return "light"
    return "limited"


def previous_band(previous: Dict[str, Any], band: str) -> Dict[str, Any]:
    if not isinstance(previous, dict):
        return {}
    bands = previous.get("bands")
    if not isinstance(bands, dict):
        return {}
    item = bands.get(band)
    return item if isinstance(item, dict) else {}


def classify_trend(previous: Dict[str, Any], band: str, score: int, deduped_count: int) -> str:
    prev = previous_band(previous, band)
    if not prev:
        return "unknown"

    prev_score = to_int(prev.get("score"), score)
    prev_count = to_int(prev.get("deduped_spots_15m"), deduped_count)

    score_delta = score - prev_score
    count_delta = deduped_count - prev_count

    if score_delta >= 10 or count_delta >= 5:
        return "rising"
    if score_delta <= -10 or count_delta <= -5:
        return "falling"
    return "steady"


def build_reason(band: str, score: int, deduped_count: int, unique_dx: int, unique_spotters: int, unique_sources: int) -> str:
    strength = classify_strength(score)

    if deduped_count <= 0:
        return f"{band} has no recent DX cluster activity in the current window."

    diversity = []
    if unique_dx >= 10:
        diversity.append("broad DX callsign diversity")
    elif unique_dx >= 4:
        diversity.append("moderate DX callsign diversity")

    if unique_spotters >= 5:
        diversity.append("several unique spotters")

    if unique_sources >= 2:
        diversity.append("multiple cluster sources")

    if diversity:
        return f"{band} has {strength} recent DX cluster activity with {', '.join(diversity)}."

    return f"{band} has {strength} recent DX cluster activity."


def score_band(spots_15m: int, deduped_count: int, unique_dx: int, unique_spotters: int, unique_sources: int, age_values: List[int]) -> int:
    density_component = min(35, deduped_count * 5)
    raw_activity_component = min(15, spots_15m * 2)
    dx_component = min(25, unique_dx * 3)
    spotter_component = min(15, unique_spotters * 3)
    source_component = min(5, unique_sources * 3)
    fresh_component = freshness_score(age_values)

    return max(
        0,
        min(
            100,
            density_component
            + raw_activity_component
            + dx_component
            + spotter_component
            + source_component
            + fresh_component,
        ),
    )


def build_activity_model(
    raw_spots: List[Dict[str, Any]],
    input_status: Dict[str, str],
    previous: Dict[str, Any],
    window_minutes: int,
    max_age_minutes: int,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    generated_ms = utc_now_ms()

    normalized: List[Dict[str, Any]] = []
    rejected = 0

    for raw in raw_spots:
        spot = normalize_valid_spot(raw, now, max_age_minutes)
        if spot is None:
            rejected += 1
            continue
        normalized.append(spot)

    by_band_raw: Dict[str, List[Dict[str, Any]]] = {}
    for spot in normalized:
        by_band_raw.setdefault(spot["band"], []).append(spot)

    bands_out: Dict[str, Dict[str, Any]] = {}

    for band in sorted(
        by_band_raw.keys(),
        key=lambda b: (
            DEFAULT_BAND_ORDER.index(b)
            if b in DEFAULT_BAND_ORDER
            else 999,
            b,
        ),
    ):
        raw_band_spots = by_band_raw[band]
        deduped = dedupe_for_activity(raw_band_spots)

        unique_dx = {str(s.get("callsign") or "") for s in deduped if s.get("callsign")}

        unique_spotters: Set[str] = set()
        unique_sources: Set[str] = set()
        age_values: List[int] = []

        for spot in deduped:
            age_values.append(to_int(spot.get("age_sec"), 0))

            for spotter in spot.get("spotters") or []:
                if spotter:
                    unique_spotters.add(str(spotter))

            for src in spot.get("sources") or []:
                if src:
                    unique_sources.add(str(src))

        score = score_band(
            spots_15m=len(raw_band_spots),
            deduped_count=len(deduped),
            unique_dx=len(unique_dx),
            unique_spotters=len(unique_spotters),
            unique_sources=len(unique_sources),
            age_values=age_values,
        )

        trend = classify_trend(previous, band, score, len(deduped))

        bands_out[band] = {
            "spots_15m": len(raw_band_spots),
            "deduped_spots_15m": len(deduped),
            "unique_dx": len(unique_dx),
            "unique_spotters": len(unique_spotters),
            "unique_sources": len(unique_sources),
            "score": score,
            "trend": trend,
            "reason": build_reason(
                band,
                score,
                len(deduped),
                len(unique_dx),
                len(unique_spotters),
                len(unique_sources),
            ),
        }

    ranked_bands = sorted(
        bands_out.keys(),
        key=lambda b: (
            -to_int(bands_out[b].get("score"), 0),
            DEFAULT_BAND_ORDER.index(b) if b in DEFAULT_BAND_ORDER else 999,
            b,
        ),
    )

    top_bands = ranked_bands[:3]

    if top_bands:
        summary = f"{top_bands[0]} has the strongest current DX cluster activity."
        status = "ok"
    elif input_status.get("hf_spots") == "ok":
        summary = "No recent usable DX cluster activity is available for RF Intel."
        status = "empty"
    else:
        summary = "HF DX cluster activity input is not currently available."
        status = "missing"

    return {
        "status": status,
        "source": SERVICE_NAME,
        "updated_utc": utc_now_iso(),
        "generated_ms": generated_ms,
        "window_minutes": window_minutes,
        "inputs": input_status,
        "bands": bands_out,
        "top_bands": top_bands,
        "summary": summary,
        "diagnostics": {
            "raw_spots_seen": len(raw_spots),
            "valid_recent_spots": len(normalized),
            "rejected_spots": rejected,
            "max_age_minutes": max_age_minutes,
        },
    }


def semantic_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove volatile clock-only fields so the service avoids Redis churn.
    """
    if not isinstance(model, dict):
        return {}

    out = dict(model)
    out.pop("updated_utc", None)
    out.pop("generated_ms", None)

    diagnostics = out.get("diagnostics")
    if isinstance(diagnostics, dict):
        diag = dict(diagnostics)
        # Diagnostics are useful, but raw seen/rejected counts changing due to
        # harmless malformed lines should not constantly churn the RF Intel model
        # if the actual band summaries did not change.
        out["diagnostics"] = {
            "max_age_minutes": diag.get("max_age_minutes"),
            "valid_recent_spots": diag.get("valid_recent_spots"),
        }

    return out


def publish_state_changed(r: redis.Redis, prefix: str, changed_key: str, model: Dict[str, Any]) -> None:
    body = {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "event": "state.changed",
        "ts": utc_now_ms(),
        "keys": [changed_key],
        "changed_keys": [changed_key],
        "status": model.get("status", "unknown"),
    }

    try:
        r.publish(f"{prefix}:system:bus", compact_json(body))
    except Exception:
        pass


def maybe_write_model(r: redis.Redis, prefix: str, model: Dict[str, Any]) -> bool:
    key = prefixed(prefix, KEY_DX_ACTIVITY)
    previous, previous_status = read_json_key(r, key, {})

    if previous_status == "ok" and isinstance(previous, dict):
        if stable_json(semantic_model(previous)) == stable_json(semantic_model(model)):
            return False

    r.set(key, compact_json(model))
    publish_state_changed(r, prefix, key, model)
    return True


def run_once(r: redis.Redis, prefix: str, window_minutes: int, max_age_minutes: int) -> bool:
    raw_spots, input_status = collect_hf_spots(r, prefix)

    previous, _status = read_json_key(r, prefixed(prefix, KEY_DX_ACTIVITY), {})
    if not isinstance(previous, dict):
        previous = {}

    model = build_activity_model(
        raw_spots=raw_spots,
        input_status=input_status,
        previous=previous,
        window_minutes=window_minutes,
        max_age_minutes=max_age_minutes,
    )

    return maybe_write_model(r, prefix, model)


def handle_signal(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    prefix = key_prefix()
    interval_sec = env_int("RT_RFI_DX_ACTIVITY_INTERVAL_SEC", 60)
    window_minutes = env_int("RT_RFI_DX_ACTIVITY_WINDOW_MIN", 15)
    max_age_minutes = env_int("RT_RFI_DX_ACTIVITY_MAX_AGE_MIN", 30)
    oneshot = env_str("RT_RFI_DX_ACTIVITY_ONESHOT", "0") == "1"

    r = redis_client()
    r.ping()

    print(
        compact_json(
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "event": "started",
                "interval_sec": interval_sec,
                "window_minutes": window_minutes,
                "max_age_minutes": max_age_minutes,
                "ts": utc_now_ms(),
            }
        ),
        flush=True,
    )

    while RUNNING:
        try:
            changed = run_once(r, prefix, window_minutes, max_age_minutes)
            print(
                compact_json(
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "event": "cycle",
                        "changed": changed,
                        "ts": utc_now_ms(),
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                compact_json(
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "event": "error",
                        "error": str(exc),
                        "ts": utc_now_ms(),
                    }
                ),
                flush=True,
            )

        if oneshot:
            break

        for _ in range(max(1, interval_sec)):
            if not RUNNING:
                break
            time.sleep(1)

    print(
        compact_json(
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "event": "stopped",
                "ts": utc_now_ms(),
            }
        ),
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())