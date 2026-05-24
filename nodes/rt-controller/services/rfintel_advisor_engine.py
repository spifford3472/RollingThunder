#!/usr/bin/env python3
"""
RollingThunder RF Intel Advisor Engine v1

Controller-side advisory message generator.

Reads only:
  - rt:rfintel:solar
  - rt:rfintel:bands
  - rt:rfintel:mobile
  - rt:radio:state
  - rt:hf:spots:selected

Writes only:
  - rt:rfintel:advisor

Publishes only:
  - state.changed to rt:system:bus when rt:rfintel:advisor semantically changes

Does not:
  - write to rt:ui:bus
  - emit intents
  - tune the radio
  - create pending actions
  - scan Redis
  - call browser/UI APIs
"""

import argparse
import copy
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis
except ImportError:
    print("ERROR: python3 redis module is required. Try: sudo apt install python3-redis", file=sys.stderr)
    sys.exit(2)


KEY_SOLAR = "rt:rfintel:solar"
KEY_BANDS = "rt:rfintel:bands"
KEY_MOBILE = "rt:rfintel:mobile"
KEY_RADIO = "rt:radio:state"
KEY_HF_SPOTS = "rt:hf:spots:selected"
KEY_OUT = "rt:rfintel:advisor"

SYSTEM_BUS = "rt:system:bus"

SOURCE = "rfintel_advisor_engine"

DEFAULT_INTERVAL_SEC = 30
DEFAULT_ITEM_TTL_SEC = 900
DEFAULT_MAX_ITEMS = 5

RUNNING = True


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on", "online", "ok", "active")


def clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def band_pretty(band: Any) -> str:
    s = clean_str(band)
    if not s:
        return "HF"
    if s.endswith("m") and s[:-1].isdigit():
        return f"{s[:-1]} meters"
    return s


def freq_pretty(spot: Dict[str, Any]) -> str:
    freq = clean_str(spot.get("freq"))
    if freq:
        return f"{freq} MHz"

    freq_hz = to_float(spot.get("freq_hz"))
    if freq_hz and freq_hz > 0:
        return f"{freq_hz / 1_000_000:.3f} MHz"

    return ""


def decode_bytes(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def decode_hash(raw: Dict[Any, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        out[str(decode_bytes(k))] = decode_bytes(v)
    return out


def parse_json_string(raw: Any) -> Tuple[Dict[str, Any], str]:
    if raw is None:
        return {}, "missing"

    raw = decode_bytes(raw)
    if raw is None:
        return {}, "missing"

    s = str(raw).strip()
    if not s:
        return {}, "missing"

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj, "ok"
        return {}, "invalid"
    except Exception:
        return {}, "invalid"


def read_key(redis_client: redis.Redis, key: str) -> Tuple[Dict[str, Any], str, str]:
    """
    Read one known key without scanning Redis.

    Returns:
      (object, input_status, redis_type)
    """
    try:
        rtype = decode_bytes(redis_client.type(key))
    except Exception:
        return {}, "error", "unknown"

    if rtype == "none":
        return {}, "missing", "none"

    if rtype == "hash":
        try:
            data = decode_hash(redis_client.hgetall(key))
            return data, "ok" if data else "missing", "hash"
        except Exception:
            return {}, "error", "hash"

    if rtype == "string":
        try:
            raw = redis_client.get(key)
            data, status = parse_json_string(raw)
            return data, status, "string"
        except Exception:
            return {}, "error", "string"

    return {}, "unsupported", str(rtype)


def advisory_item(
    *,
    item_id: str,
    label: str,
    category: str,
    severity: str,
    priority: int,
    text: str,
    reason: str,
    timestamp_utc: str,
    expires_utc: str,
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "timestamp_utc": timestamp_utc,
        "category": category,
        "severity": severity,
        "priority": int(priority),
        "text": text,
        "reason": reason,
        "expires_utc": expires_utc,
    }


def stable_id(value: Any) -> str:
    s = clean_str(value).lower()
    safe = []
    for ch in s:
        if ch.isalnum():
            safe.append(ch)
        elif ch in ("-", "_"):
            safe.append(ch)
        elif ch in (" ", ".", "/", ":"):
            safe.append("_")
    return "".join(safe).strip("_") or "unknown"


def add_unique(items: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    existing_ids = {clean_str(x.get("id")) for x in items}
    if clean_str(item.get("id")) not in existing_ids:
        items.append(item)


def trend_priority(band_item: Dict[str, Any]) -> int:
    band = clean_str(band_item.get("band"))
    trend = band_item.get("trend") if isinstance(band_item.get("trend"), dict) else {}
    direction = clean_str(trend.get("direction"))
    score = to_int(band_item.get("score"), 0)
    delta = abs(to_int(trend.get("score_delta"), 0))

    if direction == "opening":
        return 72
    if direction == "fading" and score >= 55:
        return 68
    if direction == "rising" and band in ("40m", "80m"):
        return 64
    if direction == "rising":
        return 62
    if direction == "falling" and score >= 70:
        return 60

    return 50 + min(delta, 20)


def trend_text_for_band(band_item: Dict[str, Any]) -> str:
    band = clean_str(band_item.get("band"), "HF")
    trend = band_item.get("trend") if isinstance(band_item.get("trend"), dict) else {}
    direction = clean_str(trend.get("direction"))

    if direction == "opening":
        return f"{band_pretty(band)} may be opening; recent band score improved sharply."

    if direction == "fading":
        return f"{band_pretty(band)} remains useful, but the trend is fading."

    if direction == "rising":
        if band in ("40m", "80m"):
            return f"{band_pretty(band)} activity is rising and may be a good domestic fallback."
        return f"{band_pretty(band)} activity is improving."

    if direction == "falling":
        return f"{band_pretty(band)} activity is weakening."

    return f"{band_pretty(band)} trend changed."


def meaningful_trend_band_items(bands: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = bands.get("items")
    if not isinstance(raw_items, list):
        return []

    candidates: List[Dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        trend = item.get("trend")
        if not isinstance(trend, dict):
            continue

        direction = clean_str(trend.get("direction"))
        delta = abs(to_int(trend.get("score_delta"), 0))
        band = clean_str(item.get("band"))
        score = to_int(item.get("score"), 0)

        if direction not in ("opening", "rising", "fading", "falling"):
            continue

        # Conservative anti-spam gate:
        # - opening/fading are meaningful by definition from the band advisor
        # - rising/falling must have a meaningful score move
        # - falling advisories are only useful if the band had been useful
        if direction in ("rising", "falling") and delta < 15:
            continue

        if direction == "falling" and score < 60:
            continue

        if direction == "rising" and band not in ("10m", "12m", "15m", "17m", "40m", "80m"):
            continue

        candidates.append(item)

    return sorted(candidates, key=trend_priority, reverse=True)


def add_trend_advisories(
    items: List[Dict[str, Any]],
    bands: Dict[str, Any],
    timestamp_utc: str,
    expires_utc: str,
    max_to_add: int = 2,
) -> None:
    count = 0

    for band_item in meaningful_trend_band_items(bands):
        if count >= max_to_add:
            break

        band = clean_str(band_item.get("band"), "HF")
        trend = band_item.get("trend") if isinstance(band_item.get("trend"), dict) else {}
        direction = clean_str(trend.get("direction"), "changed")
        reason = clean_str(trend.get("reason"), "Recent trend history changed meaningfully.")

        add_unique(
            items,
            advisory_item(
                item_id=f"adv_trend_{stable_id(band)}_{stable_id(direction)}",
                label=band,
                category="propagation",
                severity="info",
                priority=trend_priority(band_item),
                text=trend_text_for_band(band_item),
                reason=reason,
                timestamp_utc=timestamp_utc,
                expires_utc=expires_utc,
            ),
        )
        count += 1

def best_band_item(bands: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_items = bands.get("items")
    if not isinstance(raw_items, list):
        return None

    candidates = [x for x in raw_items if isinstance(x, dict)]
    if not candidates:
        return None

    # Trust controller-provided order first, but if scores are present, keep it robust.
    return sorted(
        candidates,
        key=lambda x: to_int(x.get("score"), -1),
        reverse=True,
    )[0]


def selected_hf_spot(hf_spots: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    selected_id = clean_str(hf_spots.get("selected_id"))
    items = hf_spots.get("items")
    if not isinstance(items, list):
        items = hf_spots.get("spots")

    if not isinstance(items, list) or not items:
        return None

    dict_items = [x for x in items if isinstance(x, dict)]

    if selected_id:
        for item in dict_items:
            if clean_str(item.get("id")) == selected_id:
                return item

    return dict_items[0] if dict_items else None


def solar_disturbance(solar: Dict[str, Any]) -> Tuple[bool, str, str, int]:
    """
    Returns:
      (is_disturbed, severity, reason, priority)
    """
    kp = to_float(solar.get("kp"), None)
    if kp is None:
        kp = to_float(solar.get("k_index"), None)

    status = clean_str(solar.get("solar_status") or solar.get("condition") or solar.get("status"))
    scales = clean_str(solar.get("swpc_scales"))
    xray = clean_str(solar.get("xray_status"))

    status_lower = status.lower()
    scales_upper = scales.upper()

    if kp is not None:
        if kp >= 7:
            return True, "critical", f"Kp is {kp:.1f}, indicating severe geomagnetic disturbance.", 95
        if kp >= 5:
            return True, "warning", f"Kp is {kp:.1f}, indicating disturbed geomagnetic conditions.", 90
        if kp >= 4:
            return True, "watch", f"Kp is {kp:.1f}, so HF paths may be less reliable.", 70

    disturbed_words = ("storm", "disturbed", "unsettled", "active", "minor", "moderate", "severe")
    if any(word in status_lower for word in disturbed_words):
        return True, "watch", f"Solar status is reported as {status}.", 70

    if scales_upper and scales_upper != "G0 S0 R0":
        return True, "watch", f"SWPC scale status is {scales}.", 70

    if xray and not xray.upper().startswith("R0"):
        return True, "watch", f"X-ray radio blackout status is {xray}.", 70

    return False, "info", "Solar model does not indicate disturbed conditions.", 0


def radio_is_offline(radio_state: Dict[str, Any], radio_input_status: str) -> Tuple[bool, str]:
    if radio_input_status != "ok":
        return False, "Radio state is unavailable."

    if not radio_state:
        return False, "Radio state is unavailable."

    online_value = radio_state.get("online")
    if online_value is None:
        status_value = clean_str(radio_state.get("status")).lower()
        if status_value in ("offline", "down", "error", "failed"):
            return True, f"Radio status is {status_value}."
        return False, "Radio state does not include an offline indication."

    if not to_bool(online_value):
        reason = clean_str(radio_state.get("reason") or radio_state.get("detail"), "offline")
        return True, reason

    return False, "Radio is online."


def build_items(
    *,
    solar: Dict[str, Any],
    bands: Dict[str, Any],
    mobile: Dict[str, Any],
    radio: Dict[str, Any],
    hf_spots: Dict[str, Any],
    input_status: Dict[str, str],
    max_items: int,
    item_ttl_sec: int,
) -> List[Dict[str, Any]]:
    ts = iso_utc(now_utc())
    expires = iso_utc(now_utc() + timedelta(seconds=item_ttl_sec))
    items: List[Dict[str, Any]] = []

    # 1. Radio offline warning. High priority because it changes whether advice is actionable.
    offline, offline_reason = radio_is_offline(radio, input_status.get("radio", "missing"))
    if offline:
        add_unique(
            items,
            advisory_item(
                item_id="adv_radio_offline",
                label="Radio",
                category="radio",
                severity="warning",
                priority=95,
                text="Radio appears offline, so tuning recommendations are informational only.",
                reason=offline_reason,
                timestamp_utc=ts,
                expires_utc=expires,
            ),
        )

    # 2. Solar warning/watch.
    if input_status.get("solar") == "ok":
        disturbed, severity, reason, priority = solar_disturbance(solar)
        if disturbed:
            add_unique(
                items,
                advisory_item(
                    item_id=f"adv_solar_{severity}",
                    label="Solar",
                    category="propagation",
                    severity=severity,
                    priority=priority,
                    text="Solar conditions look unsettled; expect weaker HF paths.",
                    reason=reason,
                    timestamp_utc=ts,
                    expires_utc=expires,
                ),
            )

    # 3. Best band based on already-existing band advisor.
    if input_status.get("bands") == "ok":
        top = best_band_item(bands)
        if top:
            band = clean_str(top.get("band"), "HF")
            status = clean_str(top.get("status"), "recommended")
            score = to_int(top.get("score"), 0)
            confidence = clean_str(top.get("confidence"))
            reason = clean_str(top.get("reason") or top.get("recommendation"))

            if score >= 70 or status in ("recommended", "usable"):
                text = f"{band_pretty(band)} looks like the best HF choice right now."
                detail_parts = []
                if score:
                    detail_parts.append(f"{band} has the highest current band advisor score ({score}).")
                else:
                    detail_parts.append(f"{band} is the top current band advisor recommendation.")
                if confidence:
                    detail_parts.append(f"Confidence is {confidence}.")
                if reason:
                    detail_parts.append(reason)

                add_unique(
                    items,
                    advisory_item(
                        item_id=f"adv_band_{stable_id(band)}_{stable_id(status)}",
                        label=band,
                        category="propagation",
                        severity="info",
                        priority=80,
                        text=text,
                        reason=" ".join(detail_parts),
                        timestamp_utc=ts,
                        expires_utc=expires,
                    ),
                )

    # 4. Trend-aware propagation nudges.
    if input_status.get("bands") == "ok":
        add_trend_advisories(
            items=items,
            bands=bands,
            timestamp_utc=ts,
            expires_utc=expires,
            max_to_add=2,
        )

    # 5. Mobile reminder.
    if input_status.get("mobile") == "ok" and to_bool(mobile.get("mobile_mode")):
        speed = to_int(mobile.get("speed_mph"), 0)
        reason = clean_str(mobile.get("reason") or mobile.get("motion_state"))
        reason_parts = []
        if speed > 0:
            reason_parts.append(f"Reported speed is {speed} mph.")
        if reason:
            reason_parts.append(reason)

        add_unique(
            items,
            advisory_item(
                item_id="adv_mobile_mode_active",
                label="Mobile",
                category="operating",
                severity="info",
                priority=65,
                text="Mobile advisor mode is active. Prefer quick, low-distraction guidance.",
                reason=" ".join(reason_parts) or "Mobile mode is true in rt:rfintel:mobile.",
                timestamp_utc=ts,
                expires_utc=expires,
            ),
        )

    # 6. Optional selected HF spot hint.
    if input_status.get("hf_spots") == "ok":
        spot = selected_hf_spot(hf_spots)
        if spot:
            call = clean_str(spot.get("callsign") or spot.get("call"))
            band = clean_str(spot.get("band") or hf_spots.get("selected_band") or hf_spots.get("band"))
            mode = clean_str(spot.get("mode"))
            freq = freq_pretty(spot)

            if call and (freq or band):
                where = " ".join(x for x in [band, freq, mode] if x)
                add_unique(
                    items,
                    advisory_item(
                        item_id=f"adv_selected_spot_{stable_id(call)}_{stable_id(band)}",
                        label="HF Spot",
                        category="spot",
                        severity="info",
                        priority=45,
                        text=f"Selected HF spot {call} is on {where}.",
                        reason="Selected spot information came from rt:hf:spots:selected.",
                        timestamp_utc=ts,
                        expires_utc=expires,
                    ),
                )

    # 7. Fallback if data is missing or no strong item exists.
    if not items:
        missing = [k for k, v in input_status.items() if v != "ok"]
        if missing:
            reason = "Missing or unavailable inputs: " + ", ".join(missing) + "."
            status = "unknown"
        else:
            reason = "Inputs are present, but no rule produced a stronger advisory."
            status = "inactive"

        add_unique(
            items,
            advisory_item(
                item_id=f"adv_waiting_{status}",
                label="Advisor",
                category="system",
                severity="info",
                priority=20,
                text="Waiting for enough RF Intel data to generate advisor guidance.",
                reason=reason,
                timestamp_utc=ts,
                expires_utc=expires,
            ),
        )

    items = sorted(items, key=lambda x: to_int(x.get("priority"), 0), reverse=True)
    return items[:max_items]


def overall_status(items: List[Dict[str, Any]], input_status: Dict[str, str]) -> str:
    if any(v == "error" for v in input_status.values()):
        return "error"
    if not items:
        return "unknown"

    if all(v != "ok" for v in input_status.values()):
        return "unknown"

    if len(items) == 1 and clean_str(items[0].get("id")).startswith("adv_waiting_"):
        return "unknown"

    return "active"


def level_from_items(items: List[Dict[str, Any]]) -> str:
    order = {"critical": 4, "warning": 3, "watch": 2, "info": 1}
    best = "info"
    for item in items:
        sev = clean_str(item.get("severity"), "info")
        if order.get(sev, 0) > order.get(best, 0):
            best = sev
    return best


def build_model(
    *,
    solar: Dict[str, Any],
    bands: Dict[str, Any],
    mobile: Dict[str, Any],
    radio: Dict[str, Any],
    hf_spots: Dict[str, Any],
    input_status: Dict[str, str],
    max_items: int,
    item_ttl_sec: int,
) -> Dict[str, Any]:
    items = build_items(
        solar=solar,
        bands=bands,
        mobile=mobile,
        radio=radio,
        hf_spots=hf_spots,
        input_status=input_status,
        max_items=max_items,
        item_ttl_sec=item_ttl_sec,
    )

    top = items[0] if items else {}
    status = overall_status(items, input_status)
    level = level_from_items(items)
    mobile_mode = "mobile" if to_bool(mobile.get("mobile_mode")) else "stationary"

    return {
        "status": status,
        "source": SOURCE,
        "mock": False,
        "updated_utc": iso_utc(now_utc()),
        "generated_ms": now_ms(),
        "advisor_text": clean_str(top.get("text"), "Waiting for enough RF Intel data to generate advisor guidance."),
        "level": level,
        "priority": to_int(top.get("priority"), 0),
        "mobile_mode": mobile_mode,
        "items": items,
        "input": copy.deepcopy(input_status),
    }


def semantic_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip churn fields before comparing.

    Intentionally ignores:
      - updated_utc
      - generated_ms
      - item timestamp_utc
      - item expires_utc
    """
    if not isinstance(model, dict):
        return {}

    out = {
        "status": model.get("status"),
        "advisor_text": model.get("advisor_text"),
        "level": model.get("level"),
        "priority": model.get("priority"),
        "mobile_mode": model.get("mobile_mode"),
        "input": model.get("input") if isinstance(model.get("input"), dict) else {},
        "items": [],
    }

    for item in model.get("items", []) if isinstance(model.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        out["items"].append(
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "category": item.get("category"),
                "severity": item.get("severity"),
                "priority": item.get("priority"),
                "text": item.get("text"),
                "reason": item.get("reason"),
            }
        )

    return out


def model_changed(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    return semantic_model(previous) != semantic_model(current)


def publish_state_changed(redis_client: redis.Redis, key: str, model: Dict[str, Any]) -> None:
    ts = now_ms()
    event = {
        "topic": "state.changed",
        "payload": {
            "keys": [key],
            "changed_keys": [key],
            "deleted_keys": [],
            "ts_ms": ts,
            "status": model.get("status", "unknown"),
        },
        "ts_ms": ts,
        "source": SOURCE,
    }
    redis_client.publish(SYSTEM_BUS, json.dumps(event, sort_keys=True, separators=(",", ":")))


def load_previous_model(redis_client: redis.Redis) -> Dict[str, Any]:
    data, status, _rtype = read_key(redis_client, KEY_OUT)
    if status == "ok":
        return data
    return {}

def has_expired_items(model: Dict[str, Any]) -> bool:
    """
    Returns true if the stored advisor model contains expired advisor items.

    This lets us ignore timestamp churn most of the time, while still refreshing
    the Redis model when item expiration would otherwise leave stale advice visible.
    """
    if not isinstance(model, dict):
        return False

    items = model.get("items")
    if not isinstance(items, list):
        return False

    now = now_utc()

    for item in items:
        if not isinstance(item, dict):
            continue

        expires_raw = clean_str(item.get("expires_utc"))
        if not expires_raw:
            continue

        try:
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires <= now:
                return True
        except Exception:
            # Bad expiration field means the stored model is not trustworthy.
            return True

    return False

def write_if_changed(redis_client: redis.Redis, model: Dict[str, Any], verbose: bool = False) -> bool:
    previous = load_previous_model(redis_client)

    expired = has_expired_items(previous)
    changed = model_changed(previous, model)

    if not changed and not expired:
        if verbose:
            print("No semantic advisor change; Redis write skipped.")
        return False

    payload = json.dumps(model, sort_keys=True, separators=(",", ":"))
    redis_client.set(KEY_OUT, payload)
    publish_state_changed(redis_client, KEY_OUT, model)

    if verbose:
        if expired and not changed:
            print(f"Refreshed {KEY_OUT} because stored advisor items had expired.")
        else:
            print(f"Wrote {KEY_OUT} and published state.changed to {SYSTEM_BUS}.")

    return True


def collect_inputs(redis_client: redis.Redis) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    solar, solar_status, _solar_type = read_key(redis_client, KEY_SOLAR)
    bands, bands_status, _bands_type = read_key(redis_client, KEY_BANDS)
    mobile, mobile_status, _mobile_type = read_key(redis_client, KEY_MOBILE)
    radio, radio_status, _radio_type = read_key(redis_client, KEY_RADIO)
    hf_spots, hf_status, _hf_type = read_key(redis_client, KEY_HF_SPOTS)

    input_status = {
        "solar": solar_status,
        "bands": bands_status,
        "mobile": mobile_status,
        "radio": radio_status,
        "hf_spots": hf_status,
    }

    return solar, bands, mobile, radio, hf_spots, input_status


def run_once(redis_client: redis.Redis, max_items: int, item_ttl_sec: int, verbose: bool = False) -> bool:
    solar, bands, mobile, radio, hf_spots, input_status = collect_inputs(redis_client)

    model = build_model(
        solar=solar,
        bands=bands,
        mobile=mobile,
        radio=radio,
        hf_spots=hf_spots,
        input_status=input_status,
        max_items=max_items,
        item_ttl_sec=item_ttl_sec,
    )

    changed = write_if_changed(redis_client, model, verbose=verbose)

    if verbose:
        print(json.dumps(model, indent=2, sort_keys=True))
        print(f"changed={changed}")

    return changed


def env_int(name: str, default: int) -> int:
    return to_int(os.environ.get(name), default)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RollingThunder RF Intel Advisor Engine v1")

    parser.add_argument(
        "--interval-sec",
        type=int,
        default=env_int("RT_RFI_ADVISOR_ENGINE_INTERVAL_SEC", DEFAULT_INTERVAL_SEC),
        help="Loop interval in seconds.",
    )
    parser.add_argument(
        "--redis-host",
        default=os.environ.get("RT_REDIS_HOST", "127.0.0.1"),
        help="Redis host.",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=env_int("RT_REDIS_PORT", 6379),
        help="Redis port.",
    )
    parser.add_argument(
        "--redis-db",
        type=int,
        default=env_int("RT_REDIS_DB", 0),
        help="Redis DB.",
    )
    parser.add_argument(
        "--redis-password",
        default=os.environ.get("RT_REDIS_PASSWORD", None),
        help="Redis password. Prefer RT_REDIS_PASSWORD from /etc/rollingthunder/redis.env.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print generated model and write behavior.",
    )
    parser.add_argument(
        "--item-ttl-sec",
        type=int,
        default=env_int("RT_RFI_ADVISOR_ITEM_TTL_SEC", DEFAULT_ITEM_TTL_SEC),
        help="Advisor item TTL in seconds.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=env_int("RT_RFI_ADVISOR_MAX_ITEMS", DEFAULT_MAX_ITEMS),
        help="Maximum advisor items to emit.",
    )

    return parser.parse_args()


def handle_signal(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def connect_redis(args: argparse.Namespace) -> redis.Redis:
    return redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        password=args.redis_password,
        socket_timeout=5,
        socket_connect_timeout=5,
        decode_responses=False,
    )


def main() -> int:
    global RUNNING

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    args = parse_args()

    interval_sec = max(5, int(args.interval_sec))
    item_ttl_sec = max(60, int(args.item_ttl_sec))
    max_items = max(1, min(10, int(args.max_items)))

    redis_client = connect_redis(args)

    try:
        redis_client.ping()
    except Exception as exc:
        print(f"ERROR: cannot connect to Redis: {exc}", file=sys.stderr)
        return 1

    if args.once:
        try:
            run_once(redis_client, max_items=max_items, item_ttl_sec=item_ttl_sec, verbose=args.verbose)
            return 0
        except Exception as exc:
            print(f"ERROR: advisor engine run failed: {exc}", file=sys.stderr)
            return 1

    if args.verbose:
        print(
            f"{SOURCE} starting: interval_sec={interval_sec} "
            f"item_ttl_sec={item_ttl_sec} max_items={max_items}"
        )

    while RUNNING:
        start = time.time()
        try:
            run_once(redis_client, max_items=max_items, item_ttl_sec=item_ttl_sec, verbose=args.verbose)
        except Exception as exc:
            print(f"ERROR: advisor engine loop failed: {exc}", file=sys.stderr)

        elapsed = time.time() - start
        sleep_for = max(1.0, interval_sec - elapsed)

        end_at = time.time() + sleep_for
        while RUNNING and time.time() < end_at:
            time.sleep(min(0.5, end_at - time.time()))

    if args.verbose:
        print(f"{SOURCE} stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())