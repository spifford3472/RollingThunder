#!/usr/bin/env python3
"""
RollingThunder RF Intel tactical map model service.

Controller-side only:
- Reads summarized RF Intel Redis models.
- Does not scan Redis.
- Does not write rt:ui:bus.
- Does not emit UI intents.
- Writes rt:rfintel:map.
- Publishes rt:system:bus state.changed only when semantic model changes.

The UI remains a dumb renderer. All marker positions, labels, intensity,
status, summaries, and mode/basis decisions are owned here on rt-controller.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis


SERVICE_NAME = "rfintel_map_model"

KEY_DX_ACTIVITY = "rt:rfintel:dx_activity"
KEY_BANDS = "rt:rfintel:bands"
KEY_SOLAR = "rt:rfintel:solar"
KEY_OUT = "rt:rfintel:map"
SYSTEM_BUS = "rt:system:bus"

DEFAULT_INTERVAL_SEC = 90


# Fixed visual positions for an honest "activity overview" tactical display.
# These are NOT geographic claims. They are controller-owned display positions.
OVERVIEW_SLOTS: List[Tuple[float, float]] = [
    (58.0, 34.0),
    (36.0, 54.0),
    (72.0, 58.0),
    (25.0, 32.0),
    (50.0, 72.0),
    (82.0, 28.0),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def redis_client() -> redis.Redis:
    host = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
    port = env_int("RT_REDIS_PORT", 6379)
    db = env_int("RT_REDIS_DB", 0)

    password = (
        os.environ.get("RT_REDIS_PASSWORD")
        or os.environ.get("REDIS_PASSWORD")
        or os.environ.get("REDIS_AUTH")
        or None
    )

    return redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )


def load_json(r: redis.Redis, key: str) -> Tuple[str, Dict[str, Any]]:
    try:
        raw = r.get(key)
    except Exception:
        return "error", {}

    if not raw:
        return "missing", {}

    try:
        parsed = json.loads(raw)
    except Exception:
        return "invalid", {}

    if isinstance(parsed, dict):
        status = str(parsed.get("status") or "ok").lower()
        if status in {"", "unknown"}:
            status = "ok"
        return "ok", parsed

    return "invalid", {}


def clamp_int(value: Any, low: int = 0, high: int = 100, default: int = 0) -> int:
    try:
        n = int(round(float(value)))
    except Exception:
        n = default
    return max(low, min(high, n))


def text(value: Any, fallback: str = "") -> str:
    s = str(value if value is not None else "").strip()
    return s if s else fallback


def marker_status(intensity: int) -> str:
    if intensity >= 75:
        return "active"
    if intensity >= 45:
        return "moderate"
    if intensity >= 20:
        return "light"
    return "quiet"


def band_label(band: str) -> str:
    b = text(band).lower()
    if b.endswith("m"):
        return b
    return band


def build_marker_for_band(band: str, band_model: Dict[str, Any], index: int) -> Dict[str, Any]:
    x_pct, y_pct = OVERVIEW_SLOTS[index % len(OVERVIEW_SLOTS)]

    score = clamp_int(band_model.get("score"), default=0)
    spots = clamp_int(band_model.get("spots_15m"), low=0, high=999, default=0)
    unique_dx = clamp_int(band_model.get("unique_dx"), low=0, high=999, default=0)
    trend = text(band_model.get("trend"), "steady")
    reason = text(band_model.get("reason"))

    b = band_label(band)
    intensity = score
    status = marker_status(intensity)

    if reason:
        summary = reason
    elif spots > 0:
        summary = f"{b} has {spots} recent DX cluster spots."
    else:
        summary = f"{b} has limited recent DX cluster activity."

    role = "Primary DX" if index == 0 else f"DX Activity {index + 1}"

    return {
        "id": f"overview_{b.replace(' ', '_').replace('/', '_')}",
        "label": role,
        "x_pct": x_pct,
        "y_pct": y_pct,
        "intensity": intensity,
        "status": status,
        "summary": summary,
        "bands": [b],
        "band": b,
        "spots_15m": spots,
        "unique_dx": unique_dx,
        "trend": trend,
    }


def build_markers(dx_activity: Dict[str, Any], bands_model: Dict[str, Any]) -> List[Dict[str, Any]]:
    dx_bands = dx_activity.get("bands")
    if not isinstance(dx_bands, dict):
        dx_bands = {}

    top_bands = dx_activity.get("top_bands")
    ordered: List[str] = []

    if isinstance(top_bands, list):
        for b in top_bands:
            bs = text(b)
            if bs and bs in dx_bands and bs not in ordered:
                ordered.append(bs)

    remaining = sorted(
        [b for b in dx_bands.keys() if b not in ordered],
        key=lambda b: clamp_int(dx_bands.get(b, {}).get("score"), default=0),
        reverse=True,
    )
    ordered.extend(remaining)

    markers: List[Dict[str, Any]] = []

    for index, band in enumerate(ordered[:6]):
        model = dx_bands.get(band)
        if isinstance(model, dict):
            markers.append(build_marker_for_band(band, model, index))

    if markers:
        return markers

    # Graceful fallback from rt:rfintel:bands when dx_activity is missing.
    items = bands_model.get("items")
    if isinstance(items, list):
        sorted_items = sorted(
            [x for x in items if isinstance(x, dict)],
            key=lambda x: clamp_int(x.get("score"), default=0),
            reverse=True,
        )

        for index, item in enumerate(sorted_items[:4]):
            band = text(item.get("band"), f"band_{index + 1}")
            x_pct, y_pct = OVERVIEW_SLOTS[index % len(OVERVIEW_SLOTS)]
            score = clamp_int(item.get("score"), default=0)
            reason = text(item.get("reason") or item.get("recommendation"))

            markers.append(
                {
                    "id": f"band_{band.replace(' ', '_').replace('/', '_')}",
                    "label": "Band Advisor" if index == 0 else f"Band {index + 1}",
                    "x_pct": x_pct,
                    "y_pct": y_pct,
                    "intensity": score,
                    "status": marker_status(score),
                    "summary": reason or f"{band} advisor score {score}.",
                    "bands": [band],
                    "band": band,
                }
            )

    return markers


def compact_regions_from_markers(markers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compatibility field for the current renderer, which already displays regions/items.
    This duplicates controller-supplied marker summaries without adding UI logic.
    """
    regions: List[Dict[str, Any]] = []
    for m in markers[:4]:
        bands = m.get("bands") if isinstance(m.get("bands"), list) else []
        band_text = ", ".join(str(x) for x in bands if str(x).strip())
        label = text(m.get("label"), "DX Activity")
        if band_text:
            label = f"{label} • {band_text}"

        regions.append(
            {
                "id": text(m.get("id")),
                "label": label,
                "status": text(m.get("status"), "unknown"),
                "summary": text(m.get("summary"), "No summary available."),
                "intensity": clamp_int(m.get("intensity"), default=0),
            }
        )
    return regions


def build_model(r: redis.Redis) -> Dict[str, Any]:
    started = time.monotonic()

    dx_status, dx_activity = load_json(r, KEY_DX_ACTIVITY)
    bands_status, bands_model = load_json(r, KEY_BANDS)
    solar_status, _solar_model = load_json(r, KEY_SOLAR)

    markers = build_markers(dx_activity, bands_model)

    if dx_status == "ok":
        status = "ok"
    elif markers:
        status = "degraded"
    else:
        status = "missing"

    summary = text(dx_activity.get("summary")) if isinstance(dx_activity, dict) else ""
    if not summary and markers:
        top = markers[0]
        bands = top.get("bands") if isinstance(top.get("bands"), list) else []
        band = text(bands[0] if bands else top.get("band"), "the top band")
        summary = f"{band} has the strongest current RF activity overview marker."
    if not summary:
        summary = "Waiting for RF Intel DX activity data."

    model: Dict[str, Any] = {
        "status": status,
        "source": SERVICE_NAME,
        "updated_utc": utc_now_iso(),
        "generated_ms": 0,
        "mode": "dx_activity",
        "basis": "activity_overview",
        "background": {
            "type": "tactical_grid",
            "asset_url": "",
            "label": "RF DX Activity",
        },
        "inputs": {
            "dx_activity": dx_status,
            "bands": bands_status,
            "solar": solar_status,
        },
        "markers": markers,
        "regions": compact_regions_from_markers(markers),
        "summary": summary,
        "message": summary,
    }

    model["generated_ms"] = int(round((time.monotonic() - started) * 1000))
    return model


def strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): strip_volatile(v)
            for k, v in value.items()
            if str(k) not in {"updated_utc", "generated_ms"}
        }
    if isinstance(value, list):
        return [strip_volatile(x) for x in value]
    return value


def semantic_changed(old_raw: Optional[str], new_model: Dict[str, Any]) -> bool:
    if not old_raw:
        return True

    try:
        old_model = json.loads(old_raw)
    except Exception:
        return True

    return strip_volatile(old_model) != strip_volatile(new_model)


def publish_state_changed(r: redis.Redis) -> None:
    payload = {
        "type": "state.changed",
        "source": SERVICE_NAME,
        "timestamp_utc": utc_now_iso(),
        "keys": [KEY_OUT],
        "changed_keys": [KEY_OUT],
    }
    r.publish(SYSTEM_BUS, json.dumps(payload, separators=(",", ":"), sort_keys=True))


def write_if_changed(r: redis.Redis, model: Dict[str, Any]) -> bool:
    encoded = json.dumps(model, separators=(",", ":"), sort_keys=True)
    old_raw = r.get(KEY_OUT)

    if not semantic_changed(old_raw, model):
        return False

    r.set(KEY_OUT, encoded)
    publish_state_changed(r)
    return True


def main() -> int:
    interval = env_int("RT_RFINTEL_MAP_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)
    if interval < 30:
        interval = 30

    r = redis_client()

    print(f"[{SERVICE_NAME}] starting interval={interval}s output={KEY_OUT}", flush=True)

    while True:
        try:
            model = build_model(r)
            changed = write_if_changed(r, model)
            print(
                f"[{SERVICE_NAME}] status={model.get('status')} markers={len(model.get('markers') or [])} changed={changed}",
                flush=True,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[{SERVICE_NAME}] ERROR: {exc}", flush=True)
            traceback.print_exc()

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())