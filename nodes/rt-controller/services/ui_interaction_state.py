#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime, timezone

import redis


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
INTERACTION_HEARTBEAT_MS = int(os.environ.get("RT_UI_INTERACTION_HEARTBEAT_MS", "1000"))
INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")

CONFIG_PAGES_DIR = Path(
    os.environ.get("RT_PAGES_PATH", "/opt/rollingthunder/config/pages")
)

INTERACTION_KEY = "rt:interaction:state"
WRITER_LOCK_KEY = "rt:interaction:writer"

NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

SYSTEM_NODES_SET_KEY = "rt:system:nodes"
NODE_KEY_PREFIX = "rt:nodes:"
SERVICE_KEY_PREFIX = "rt:services:"

CONFIG_APP_PATH = Path(os.environ.get("RT_APP_CONFIG_PATH", "/opt/rollingthunder/config/app.json"))

HOME_SERVICES_MODEL_KEY = "rt:ui:model:controller_services_summary"
HOME_SERVICES_REFRESH_HOME_MS = int(os.environ.get("RT_HOME_SERVICES_REFRESH_HOME_MS", "60000"))
HOME_SERVICES_REFRESH_AWAY_MS = int(os.environ.get("RT_HOME_SERVICES_REFRESH_AWAY_MS", "3600000"))

POTA_CONTEXT_KEY = "rt:pota:context"
POTA_NEARBY_KEY = "rt:pota:nearby"
POTA_BANDS_KEY = "rt:pota:ui:ssb:bands"
POTA_SPOTS_SELECTED_KEY = "rt:pota:ui:ssb:spots:selected"
POTA_SPOT_STATUS_KEY_PREFIX ="rt:pota:spot_status:"

VHF_SELECT_REQUEST_KEY = "rt:vhf:select:request"
VHF_SELECT_STATE_KEY = "rt:vhf:select:state"
VHF_SELECT_DUPLICATE_SUPPRESS_MS = 1500
VHF_SELECT_COMPLETED_SUPPRESS_MS = 10000
VHF_SCAN_REQUEST_KEY = "rt:vhf:scan:request"
VHF_SCAN_STATE_KEY = "rt:vhf:scan"

VHF_RIGHT_PANEL_ID = "vhf_side_b_summary"

VHF_RIGHT_IDLE_OPTIONS = [
    {"key": "start_scan", "label": "Start Scan"},
    {"key": "repeaters", "label": "Repeaters"},
    {"key": "air", "label": "Air"},
    {"key": "news", "label": "News"},
]

VHF_RIGHT_SCANNING_OPTIONS = [
    {"key": "stop_scan", "label": "Stop Scan"},
]

def utc_day_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def publish_vhf_repeater_select_request(
    r: redis.Redis,
    item: Dict[str, Any],
    selected_index: int,
    selected_id: str | None = None,
) -> str:
    """
    Controller-side VHF repeater select request.

    This does not command the radio.
    This does not write rt:vhf:adapter:request.
    The scan manager owns the serialized stop-scan + tune transaction.
    """
    item = as_dict(item)
    now = now_ms()

    resolved_id = str(
        selected_id
        or item.get("id")
        or item.get("repeater_id")
        or item.get("source_id")
        or item.get("callsign")
        or item.get("label")
        or ""
    ).strip()

    if not resolved_id:
        return "ignored_no_selected_id"

    # Button-bounce / repeated-OK suppression before the scan manager even sees it.
    current_request = as_dict(get_json_or_value(r, VHF_SELECT_REQUEST_KEY))
    current_selected_id = str(current_request.get("selected_id") or "").strip()
    try:
        current_requested_at_ms = int(current_request.get("requested_at_ms") or 0)
    except Exception:
        current_requested_at_ms = 0

    if (
        current_selected_id
        and current_selected_id == resolved_id
        and current_requested_at_ms > 0
        and now - current_requested_at_ms < VHF_SELECT_DUPLICATE_SUPPRESS_MS
    ):
        return "ignored_duplicate_vhf_select"

    # If the scan manager is already in the actual tune phase, do not replace it.
    # If it is only queued/stopping_scan, a different selected row may replace the pending request.
    select_state = as_dict(get_json_or_value(r, VHF_SELECT_STATE_KEY))
    active = bool(select_state.get("active"))
    phase = str(select_state.get("phase") or "").strip()

    if active and phase in {"stopping_scan", "tuning", "waiting_result"}:
        active_id = str(select_state.get("selected_id") or "").strip()
        if active_id == resolved_id:
            return "ignored_vhf_select_already_tuning"
        return "ignored_vhf_select_tune_busy"

    # Suppress repeated OK on the same row shortly after a completed tune.
    # This prevents stacked button presses from re-tuning the same repeater
    # immediately after the first transaction completes.
    state_selected_id = str(select_state.get("selected_id") or "").strip()
    state_status = str(select_state.get("status") or "").strip().lower()
    state_phase = str(select_state.get("phase") or "").strip().lower()
    state_updated_raw = str(select_state.get("updated_at_ms") or select_state.get("updated_ms") or "").strip()

    state_updated_ms = 0
    try:
        state_updated_ms = int(state_updated_raw)
    except Exception:
        state_updated_ms = 0

    # Current select_state uses updated_utc, not updated_ms, so add a fallback:
    # if same row is complete and no ms timestamp exists, use the request timestamp
    # suppression path below as the primary bounce guard.
    if (
        state_selected_id == resolved_id
        and state_phase == "complete"
        and state_status in {"ok", "partial"}
        and state_updated_ms > 0
        and now - state_updated_ms < VHF_SELECT_COMPLETED_SUPPRESS_MS
    ):
        return "ignored_recently_completed_vhf_select"

    request_id = f"vhf-select-{now}-{resolved_id}"

    payload = {
        "request_id": request_id,
        "selected_id": resolved_id,
        "selected_index": int(selected_index),
        "page": "vhf",
        "panel": "vhf_repeater_scan_summary",
        "source": "ui_interaction_state",
        "requested_at_ms": now,
        "requested_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    # Include the display item for diagnostics only.
    # The scan manager must re-resolve the authoritative repeater before tuning.
    payload["display_item"] = item

    r.set(
        VHF_SELECT_REQUEST_KEY,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )
    publish_state_changed(r, [VHF_SELECT_REQUEST_KEY], source="ui_interaction_state")
    return "vhf_select_requested"

def truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "active", "scanning"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "inactive"}:
            return False
    return default


def is_vhf_repeater_scan_active(r: redis.Redis) -> bool:
    scan = as_dict(get_json_or_value(r, VHF_SCAN_STATE_KEY))

    status = str(scan.get("status") or "").strip().lower()
    actual = str(
        scan.get("actual_scan_state")
        or scan.get("scan_state")
        or scan.get("state")
        or ""
    ).strip().lower()

    if truthy(scan.get("scanning"), False):
        return True

    if actual == "scanning":
        return True

    # Treat requested-but-paused scan states as "scan running" for right-panel
    # control purposes, because the only valid action should still be Stop Scan.
    if truthy(scan.get("requested"), False) and status in {
        "priming_radio",
        "scanning",
        "confirming_activity",
        "stopped_on_activity",
        "adapter_waiting",
        "adapter_timeout",
    }:
        return True

    return False


def vhf_right_action_options(r: redis.Redis) -> list[Dict[str, Any]]:
    if is_vhf_repeater_scan_active(r):
        return [dict(item) for item in VHF_RIGHT_SCANNING_OPTIONS]
    return [dict(item) for item in VHF_RIGHT_IDLE_OPTIONS]


def vhf_right_action_item_id(item: Any) -> str:
    item = as_dict(item)
    return str(item.get("key") or item.get("id") or item.get("label") or "").strip()


def resolve_vhf_right_panel_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    items = vhf_right_action_options(r)
    if not items:
        return None

    return {
        "items": items,
        "count": len(items),
        "anchor_index": 0,
        "window_size": len(items),
        "get_id": vhf_right_action_item_id,
    }


def write_vhf_scan_request(
    r: redis.Redis,
    *,
    requested: bool,
    action_key: str,
) -> None:
    now = now_ms()

    payload = {
        "requested": bool(requested),
        "enabled": bool(requested),
        "reason": "right_panel_start_scan" if requested else "right_panel_stop_scan",
        "source": "ui_interaction_state",
        "action": action_key,
        "updated_at_ms": now,
        "updated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    r.set(
        VHF_SCAN_REQUEST_KEY,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )
    publish_state_changed(
        r,
        [VHF_SCAN_REQUEST_KEY],
        source="ui_interaction_state:vhf_right_panel",
    )


def build_vhf_future_enhancement_modal(action_key: str) -> Dict[str, Any]:
    action_key = str(action_key or "").strip()
    labels = {
        item["key"]: item["label"]
        for item in VHF_RIGHT_IDLE_OPTIONS
        if isinstance(item, dict) and item.get("key")
    }

    title = labels.get(action_key, "Future")

    ts = now_ms()
    return {
        "active": True,
        "id": f"vhf_future_enhancement:{action_key}:{ts}",
        "type": "vhf_future_enhancement",
        "title": title,
        "message": "Future enhancement",
        "confirm_label": "OK",
        "cancel_label": "Cancel",
        "confirmable": True,
        "cancelable": True,
        "destructive": False,
        "opened_at_ms": ts,
    }


def selected_vhf_right_action(
    r: redis.Redis,
    state: Dict[str, Any],
) -> tuple[Dict[str, Any], int, Dict[str, Any]]:
    model = resolve_vhf_right_panel_browse_model(r) or {
        "items": [],
        "count": 0,
        "anchor_index": 0,
        "window_size": 1,
    }

    items = as_list(model.get("items"))
    count = len(items)

    if count <= 0:
        return {}, 0, model

    browse = as_dict(state.get("browse"))
    selected_index = 0

    if str(browse.get("panel") or "").strip() == VHF_RIGHT_PANEL_ID:
        try:
            selected_index = int(browse.get("selected_index", 0))
        except Exception:
            selected_index = 0

    selected_index = clamp_index(selected_index, count)
    return as_dict(items[selected_index]), selected_index, model


def handle_vhf_right_panel_action(
    r: redis.Redis,
    state: Dict[str, Any],
    intent: str,
) -> tuple[str, bool]:
    item, selected_index, model = selected_vhf_right_action(r, state)
    action_key = str(item.get("key") or "").strip()

    if not action_key:
        return "ignored_vhf_right_no_action", False

    # Keep browse state anchored to the right-panel option group.
    state["browse"] = build_browse_state(
        "vhf",
        VHF_RIGHT_PANEL_ID,
        model,
        selected_index,
    )

    if action_key == "start_scan":
        write_vhf_scan_request(r, requested=True, action_key=action_key)
        return "vhf_scan_start_requested", True

    if action_key == "stop_scan":
        write_vhf_scan_request(r, requested=False, action_key=action_key)
        return "vhf_scan_stop_requested", True

    if action_key == "repeaters":
        # Explicit no-op for now.
        return "vhf_repeaters_action_noop", True

    if action_key in {"air", "news"}:
        state["modal"] = build_vhf_future_enhancement_modal(action_key)
        return "vhf_future_enhancement_opened", True

    return "ignored_vhf_right_unknown_action", True

def publish_radio_log_qso_intent(r: redis.Redis, spot: Dict[str, Any]) -> None:
    context = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))

    selected_refs = context.get("selected_park_refs")
    if not isinstance(selected_refs, list):
        selected_refs = []

    freq_hz = spot.get("freq_hz")
    if freq_hz is None:
        try:
            freq_hz = int(float(str(spot.get("frequency") or "0")))
        except Exception:
            freq_hz = 0

    band = str(
        spot.get("band")
        or context.get("selected_band")
        or context.get("band")
        or ""
    ).strip()

    mode = str(spot.get("mode") or "SSB").strip() or "SSB"

    params = {
        "call": str(spot.get("callsign") or spot.get("call") or "").strip(),
        "freq_hz": int(freq_hz or 0),
        "band": band,
        "mode": mode,
        "park_ref": str(spot.get("park_ref") or spot.get("reference") or "").strip(),
        "their_pota_ref": str(spot.get("park_ref") or spot.get("reference") or "").strip(),
        "my_pota_refs": selected_refs,
    }

    publish_intent(r, "radio.log_qso", params)

def get_pota_spot_status_for_item(r: redis.Redis, item: Dict[str, Any]) -> str | None:
    band = str(item.get("band") or "").strip()
    spot_id = str(item.get("spot_id") or spot_item_id(item) or "").strip()
    if not band or not spot_id:
        return None

    state = load_pota_spot_status_state(r, band)
    spots = as_dict(state.get("spots"))
    entry = as_dict(spots.get(spot_id))
    status = str(entry.get("status") or "").strip()
    return status or None

def is_browse_skippable_pota_spot_fast(
    item: Dict[str, Any],
    status_map: Dict[str, Any],
) -> bool:
    spot_id = spot_item_id(item)
    entry = as_dict(status_map.get(spot_id))
    status = str(entry.get("status") or "").strip()
    return status == "worked"

def is_browse_skippable_pota_spot(r: redis.Redis, item: Dict[str, Any]) -> bool:
    status = get_pota_spot_status_for_item(r, item)
    return status == "worked"


def find_next_browse_index_for_pota_spots(
    r: redis.Redis,
    model: Dict[str, Any],
    current_index: int,
    delta: int,
) -> int:
    items = as_list(model.get("items"))
    count = len(items)
    if count <= 0:
        return current_index

    # 🔥 NEW: resolve band once
    band = None
    if items:
        band = str(as_dict(items[0]).get("band") or "").lower()

    # 🔥 NEW: load status ONCE
    status_state = load_pota_spot_status_state(r, band)
    status_map = as_dict(status_state.get("spots"))

    direction = 1 if delta > 0 else -1
    start = clamp_index(current_index, count)

    for step in range(1, count + 1):
        idx = (start + (step * direction)) % count
        item = as_dict(items[idx])
        if not item:
            continue

        if not is_browse_skippable_pota_spot_fast(item, status_map):
            return idx

    return start

def pota_spot_status_key(band: str) -> str:
    return f"{POTA_SPOT_STATUS_KEY_PREFIX}{str(band or '').strip().lower()}"


def load_pota_spot_status_state(r: redis.Redis, band: str) -> Dict[str, Any]:
    today = utc_day_str()
    if not band:
        return {"day_utc": today, "spots": {}}

    raw = get_json_or_value(r, pota_spot_status_key(band))
    state = as_dict(raw)

    day_utc = str(state.get("day_utc") or "").strip()
    spots = as_dict(state.get("spots"))

    if day_utc != today:
        return {"day_utc": today, "spots": {}}

    return {
        "day_utc": today,
        "spots": spots,
    }


def save_pota_spot_status_state(r: redis.Redis, band: str, state: Dict[str, Any]) -> None:
    if not band:
        return
    payload = {
        "day_utc": str(state.get("day_utc") or utc_day_str()),
        "spots": as_dict(state.get("spots")),
        "updated_at_ms": now_ms(),
    }
    r.set(
        pota_spot_status_key(band),
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )


def apply_pota_spot_outcome_state(r: redis.Redis, spot: Dict[str, Any], outcome: str) -> None:
    band = str(spot.get("band") or "").strip()
    spot_id = str(spot.get("spot_id") or spot_item_id(spot) or "").strip()
    outcome = str(outcome or "").strip()

    if not band or not spot_id or outcome not in {"cannot_hear", "worked", "heard_not_worked"}:
        return

    state = load_pota_spot_status_state(r, band)
    spots = as_dict(state.get("spots"))

    spots[spot_id] = {
        "status": outcome,
        "updated_at_ms": now_ms(),
    }

    state["spots"] = spots
    save_pota_spot_status_state(r, band, state)


def spot_freq_hz(item: Dict[str, Any]) -> int:
    value = item.get("freq_hz")
    if value is None:
        value = item.get("frequency")

    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def spot_sort_key(item: Dict[str, Any]) -> tuple[int, str, str]:
    freq = spot_freq_hz(item)
    call = str(item.get("callsign") or item.get("call") or "").strip().upper()
    park = str(item.get("park_ref") or item.get("reference") or "").strip().upper()
    return (freq, call, park)

def now_ms() -> int:
    return int(time.time() * 1000)

def spot_freq_hz(item: Dict[str, Any]) -> int:
    value = item.get("freq_hz")
    if value is None:
        value = item.get("frequency")

    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def spot_sort_key(item: Dict[str, Any]) -> tuple[int, str, str]:
    freq = spot_freq_hz(item)
    call = str(item.get("callsign") or item.get("call") or "").strip().upper()
    park = str(item.get("park_ref") or item.get("reference") or "").strip().upper()
    return (freq, call, park)

def selected_item_from_model(model: Dict[str, Any], selected_index: int) -> Dict[str, Any] | None:
    items = as_list(model.get("items"))
    count = len(items)
    if count <= 0:
        return None

    idx = clamp_index(selected_index, count)
    item = items[idx]
    return as_dict(item) if isinstance(item, dict) else None

def extract_node_id(item: Dict[str, Any]) -> str:
    for key in ("id", "node_id", "hostname", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""

def publish_ui_result(r: redis.Redis, intent: str, result: str = "accepted") -> None:
    try:
        payload = {
            "intent": intent,
            "result": result,
            "ts_ms": now_ms(),
        }
        r.set(
            "rt:ui:last_result",
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        )
    except Exception:
        pass

SYSTEM_BUS_CH = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")

def publish_state_changed(r: redis.Redis, keys: list[str], source: str = "ui_interaction_state") -> None:
    evt = {
        "topic": "state.changed",
        "payload": {"keys": keys[:50]},
        "ts_ms": now_ms(),
        "source": source,
    }
    r.publish(SYSTEM_BUS_CH, json.dumps(evt, separators=(",", ":"), ensure_ascii=False))


def publish_intent(r: redis.Redis, intent: str, params: Dict[str, Any]) -> None:
    payload = {
        "intent": intent,
        "params": params or {},
        "source": {
            "type": "ui_interaction_state",
            "node": NODE_ID,
        },
        "timestamp": now_ms(),
    }
    r.publish(INTENTS_CH, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

def publish_radio_tune_intent(r: redis.Redis, spot: Dict[str, Any]) -> None:
    freq_hz = spot.get("freq_hz")
    if freq_hz is None:
        try:
            freq_hz = int(float(str(spot.get("frequency") or "0")))
        except Exception:
            freq_hz = 0

    band = str(spot.get("band") or "").strip()
    raw_mode = str(spot.get("mode") or "SSB").strip().upper()

    if raw_mode == "SSB":
        if int(freq_hz or 0) > 0:
            mode = "LSB" if int(freq_hz) < 10_000_000 else "USB"
        elif band.lower() in {"160m", "80m", "60m", "40m"}:
            mode = "LSB"
        else:
            mode = "USB"
    else:
        mode = raw_mode or "USB"

    params = {
        "freq_hz": int(freq_hz or 0),
        "band": band or None,
        "mode": mode,
        "spot_id": str(spot.get("spot_id") or spot_item_id(spot) or "").strip() or None,
        "nodeId": "rt-radio",
    }

    publish_intent(r, "radio.tune", params)

def hf_band_item_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(
            item.get("id")
            or item.get("band")
            or item.get("label")
            or item.get("name")
            or ""
        ).strip()

    return str(item or "").strip()


def hf_spot_item_id(item: Any) -> str:
    item = as_dict(item)

    explicit_id = str(item.get("id") or item.get("spot_id") or "").strip()
    if explicit_id:
        return explicit_id

    band = str(item.get("band") or "").strip()
    freq_hz = str(item.get("freq_hz") or item.get("frequency") or "").strip()
    callsign = str(item.get("callsign") or item.get("call") or "").strip().lower()

    return f"{band}-{freq_hz}-{callsign}".strip("-")


def publish_hf_select_band_intent(r: redis.Redis, item: Any) -> None:
    band = hf_band_item_id(item)
    if not band:
        return

    publish_intent(
        r,
        "hf.select_band",
        {
            "band": band,
            "band_id": band,
            "selected_band": band,
        },
    )


def publish_hf_select_spot_intent(r: redis.Redis, item: Dict[str, Any]) -> None:
    item = as_dict(item)
    spot_id = hf_spot_item_id(item)

    try:
        freq_hz = int(item.get("freq_hz") or item.get("frequency") or 0)
    except Exception:
        freq_hz = 0

    publish_intent(
        r,
        "hf.select_spot",
        {
            "spot_id": spot_id,
            "id": spot_id,
            "selected_spot_id": spot_id,
            "callsign": str(item.get("callsign") or item.get("call") or "").strip(),
            "freq_hz": freq_hz,
            "band": str(item.get("band") or "").strip(),
            "mode": str(item.get("mode") or "").strip(),
        },
    )


def build_hf_spot_outcome_modal(spot: Dict[str, Any]) -> Dict[str, Any]:
    spot = as_dict(spot)
    ts = now_ms()

    callsign = str(spot.get("callsign") or spot.get("call") or "").strip() or "HF Spot"
    freq = str(spot.get("freq") or "").strip()

    if not freq:
        try:
            freq_hz = int(spot.get("freq_hz") or spot.get("frequency") or 0)
        except Exception:
            freq_hz = 0
        if freq_hz > 0:
            freq = f"{freq_hz / 1000000:.3f}"

    mode = str(spot.get("mode") or "").strip()
    band = str(spot.get("band") or "").strip()

    subtitle = " • ".join([x for x in [freq, mode, band] if x])

    return {
        "active": True,
        "id": f"hf_spot_outcome:{ts}",
        "type": "hf_spot_outcome",
        "title": callsign,
        "message": subtitle or "HF spot outcome",
        "spot_id": hf_spot_item_id(spot),
        "callsign": callsign,
        "freq_hz": spot.get("freq_hz") or spot.get("frequency"),
        "band": band,
        "mode": mode,

        # Match POTA outcome ordering:
        # 0 = Can't Hear, 1 = Worked, 2 = Heard not Worked
        "selected_option_index": 1,
        "options": [
            {"key": "cannot_hear", "label": "Can't Hear"},
            {"key": "worked", "label": "Worked"},
            {"key": "heard_not_worked", "label": "Heard not Worked"},
        ],

        # Keep physical/virtual OK and Cancel semantics.
        "confirmable": True,
        "cancelable": True,
        "destructive": False,
        "opened_at_ms": ts,
    }


def publish_hf_spot_outcome_intent(
    r: redis.Redis,
    spot: Dict[str, Any],
    outcome_key: str,
) -> None:
    spot = as_dict(spot)
    outcome_key = str(outcome_key or "").strip()
    if not outcome_key:
        return

    try:
        freq_hz = int(spot.get("freq_hz") or spot.get("frequency") or 0)
    except Exception:
        freq_hz = 0

    publish_intent(
        r,
        "hf.spot.outcome",
        {
            "spot_id": hf_spot_item_id(spot),
            "status": outcome_key,
            "callsign": str(spot.get("callsign") or spot.get("call") or "").strip(),
            "freq_hz": freq_hz,
            "band": str(spot.get("band") or "").strip(),
            "mode": str(spot.get("mode") or "").strip(),
        },
    )

def build_band_tune_reminder_modal(band: str) -> Dict[str, Any]:
    ts = now_ms()
    return {
        "active": True,
        "id": f"band_tune_reminder:{band}:{ts}",
        "type": "band_tune_reminder",
        "title": "Tune Reminder",
        "message": f"Tune radio for {band}",
        "confirmable": False,
        "cancelable": False,
        "destructive": False,
        "duration_ms": 3000,
        "auto_close_at_ms": ts + 3000,
        "opened_at_ms": ts,
    }

SPOT_OUTCOME_OPTIONS = [
    {"key": "cannot_hear", "label": "Can't hear"},
    {"key": "worked", "label": "Worked"},
    {"key": "heard_not_worked", "label": "Heard not worked"},
]


def build_pota_spot_outcome_modal(spot: Dict[str, Any]) -> Dict[str, Any]:
    ts = now_ms()

    spot_id = str(spot.get("spot_id") or spot_item_id(spot) or "").strip()
    callsign = str(spot.get("callsign") or spot.get("call") or "").strip()
    park_ref = str(spot.get("park_ref") or spot.get("reference") or "").strip()
    band = str(spot.get("band") or "").strip()
    freq_hz = spot.get("freq_hz")

    if freq_hz is None:
        try:
            freq_hz = int(float(str(spot.get("frequency") or "0")))
        except Exception:
            freq_hz = 0

    title_parts = [part for part in [callsign, park_ref] if part]
    title = " / ".join(title_parts) if title_parts else "Spot Outcome"

    return {
        "active": True,
        "id": f"pota_spot_outcome:{spot_id or 'unknown'}:{ts}",
        "type": "pota_spot_outcome",
        "title": title,
        "spot_id": spot_id or None,
        "callsign": callsign or None,
        "park_ref": park_ref or None,
        "band": band or None,
        "freq_hz": int(freq_hz or 0),
        "selected_option_index": 1,
        "options": list(SPOT_OUTCOME_OPTIONS),
        "confirmable": True,
        "cancelable": True,
        "destructive": False,
        "confirm_label": "OK",
        "cancel_label": "Cancel",
        "opened_at_ms": ts,
    }

def update_pota_context_selected_band(r: redis.Redis, new_band: str) -> None:
    current = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
    current["selected_band"] = new_band
    current["selection_ts"] = now_ms()
    r.set(POTA_CONTEXT_KEY, json.dumps(current, separators=(",", ":"), ensure_ascii=False))

def build_node_reboot_modal(node_id: str, step: str = "warn") -> Dict[str, Any]:
    node_id = str(node_id or "").strip().lower()

    if node_id == "rt-controller":
        if step == "armed":
            return {
                "active": True,
                "id": f"node_reboot:{node_id}:armed",
                "type": "node_reboot_confirm",
                "title": "Confirm",
                "node_id": node_id,
                "step": "armed",
                "warning": "PRESS OK TO REBOOT",
                "message": "",
                "confirm_label": "OK",
                "cancel_label": "Cancel",
                "confirmable": True,
                "cancelable": True,
                "destructive": True,
                "opened_at_ms": now_ms(),
            }

        return {
            "active": True,
            "id": f"node_reboot:{node_id}:warn",
            "type": "node_reboot_confirm",
            "title": "Confirm",
            "node_id": node_id,
            "step": "warn",
            "warning": "WARNING",
            "message": "System will go down during reboot",
            "submessage": "Selecting OK begins the process",
            "confirm_label": "OK",
            "cancel_label": "Exit",
            "confirmable": True,
            "cancelable": True,
            "destructive": True,
            "opened_at_ms": now_ms(),
        }

    return {
        "active": True,
        "id": f"node_reboot:{node_id}:warn",
        "type": "node_reboot_confirm",
        "title": "Confirm",
        "node_id": node_id,
        "step": "warn",
        "warning": "WARNING",
        "message": "Selecting OK will reboot this node",
        "confirm_label": "OK",
        "cancel_label": "Exit",
        "confirmable": True,
        "cancelable": True,
        "destructive": True,
        "opened_at_ms": now_ms(),
    }

def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    r.ping()
    return r

def service_item_id(item: Dict[str, Any]) -> str | None:
    for key in ("id", "service_id", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None

HOME_SERVICE_NODE_ORDER = {
    "rt-controller": 0,
    "rt-radio": 1,
    "rt-wpsd": 2,
    "rt-display": 3,
}

SYSTEM_SERVICE_IDS = {
    "redis_state",
    "mqtt_bus",
    "gps_ingest",
    "logging",
    "node_health",
    "noaa_same",
    "meshtastic_c2",
}


def service_row_item_id(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""

    # Skip headers completely
    if row.get("type") == "node_header":
        return ""

    node = str(row.get("node") or "").strip()
    service = str(row.get("service") or "").strip()

    if not node or not service:
        return ""

    return f"{node}:{service}"


def normalize_service_state(raw: str) -> str:
    state = str(raw or "").strip().lower()
    if state in {"active", "running", "ok", "healthy"}:
        return "active"
    if state in {"inactive", "stopped"}:
        return "inactive"
    if state in {"failed", "error", "degraded"}:
        return state
    return state or "unknown"


def service_sort_key(item: Dict[str, Any]) -> tuple[int, str, str]:
    node = str(item.get("node") or item.get("ownerNode") or "").strip()
    service = str(item.get("service") or item.get("id") or item.get("name") or "").strip()

    return (
        HOME_SERVICE_NODE_ORDER.get(node, 999),
        node.lower(),
        service.lower(),
    )

def load_pages() -> List[Dict[str, Any]]:
    pages = []
    for f in CONFIG_PAGES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            pages.append(data)
        except Exception as e:
            continue
    pages.sort(key=lambda p: int(p.get("order", 9999)))
    return pages

def load_app_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_APP_PATH.read_text())
    except Exception:
        return {}


def get_has_tuner(app_cfg: Dict[str, Any]) -> bool:
    return bool((((app_cfg.get("globals") or {}).get("radio") or {}).get("has_tuner")))

def build_page_index(pages):
    return {p["id"]: p for p in pages}


def default_state(pages):
    if not pages:
        return None

    first = pages[0]
    focus = first.get("focusPolicy", {}).get("defaultPanel")

    return {
        "page": first["id"],
        "focus": focus,
        "modal": None,
        "browse": None,
        "pending_action": None,
        "authority": {
            "degraded": False,
            "stale": False,
            "reason": None,
        },
        "updated_at_ms": now_ms(),
    }


def acquire_lock(r):
    while True:
        ok = r.set(WRITER_LOCK_KEY, NODE_ID, nx=True, px=10000)
        if ok:
            return

        # Optional: log once every few seconds if you want
        time.sleep(1)


def save_state(r: redis.Redis, state: Dict[str, Any]):
    state["updated_at_ms"] = now_ms()

    # Keep the single-writer lock alive so no second writer resets state.
    r.pexpire(WRITER_LOCK_KEY, 10000)

    r.set(INTERACTION_KEY, json.dumps(state, separators=(",", ":")))

def is_browse_active(state: Dict[str, Any]) -> bool:
    browse = state.get("browse")
    return isinstance(browse, dict) and bool(browse.get("active", True))

def get_json_or_value(r: redis.Redis, key: str):
    try:
        key_type = r.type(key)
    except Exception:
        return None

    try:
        if key_type == "string":
            raw = r.get(key)
            if not raw:
                return None
            raw = raw.strip()
            if not raw:
                return None
            if raw.startswith("{") or raw.startswith("["):
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
            return raw

        if key_type == "hash":
            return r.hgetall(key)

        return None
    except Exception:
        return None


def as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return []


def as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}

def clamp_index(index: int, count: int) -> int:
    if count <= 0:
        return 0
    if index < 0:
        return 0
    if index >= count:
        return count - 1
    return index

def node_item_id(item: Dict[str, Any]) -> str | None:
    for key in ("id", "node_id", "hostname", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None

def park_item_id(item: Dict[str, Any]) -> str | None:
    for key in ("reference", "park_ref", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None

def band_item_id(item: Any) -> str | None:
    if isinstance(item, str):
        s = item.strip()
        return s or None
    if isinstance(item, dict):
        for key in ("band", "id", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return None

def spot_item_id(item: Dict[str, Any]) -> str | None:
    for key in ("spot_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value

    call = str(item.get("callsign") or item.get("call") or "").strip()
    park = str(item.get("park_ref") or item.get("reference") or "").strip()
    freq = str(item.get("freq_hz") or item.get("frequency") or "").strip()
    if call or park or freq:
        return "|".join([call, park, freq]).strip("|") or None

    return None

def resolve_home_services_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    rows: List[Dict[str, Any]] = []

    # Preferred future path: rt:system:services contains full Redis keys.
    try:
        keys = r.smembers("rt:system:services") or []

        services: List[Dict[str, Any]] = []

        for ks in keys:
            ks = str(ks).strip()
            if not ks:
                continue

            if not ks.startswith(SERVICE_KEY_PREFIX):
                ks = f"{SERVICE_KEY_PREFIX}{ks}"

            if r.type(ks) != "hash":
                continue

            raw = r.hgetall(ks) or {}
            if not raw:
                continue

            service_id = str(
                raw.get("id")
                or raw.get("service_id")
                or raw.get("name")
                or ks[len(SERVICE_KEY_PREFIX):]
                or ""
            ).strip()

            node = str(
                raw.get("ownerNode")
                or raw.get("node")
                or raw.get("node_id")
                or raw.get("host")
                or ""
            ).strip()

            if not service_id or not node:
                continue

            if service_id in SYSTEM_SERVICE_IDS:
                continue

            services.append({
                "type": "service",
                "node": node,
                "service": service_id,
                "state": normalize_service_state(str(raw.get("state") or "")),
                "last_update_ms": raw.get("last_update_ms"),
            })

        if services:
            services.sort(key=service_sort_key)

            current_node = None
            for svc in services:
                node = str(svc.get("node") or "").strip()
                if node != current_node:
                    current_node = node
                    rows.append({
                        "type": "node_header",
                        "node": node,
                        "service": "",
                        "state": "",
                    })
                rows.append(svc)

            model_payload = {
                "items": rows,
                "count": len(rows),
                "updated_at_ms": now_ms(),
            }

            r.set(
                HOME_SERVICES_MODEL_KEY,
                json.dumps(model_payload, separators=(",", ":"), ensure_ascii=False),
            )

    except Exception:
        rows = []

    # Fallback: use existing controller_services_summary model.
    if not rows:
        cached = get_json_or_value(r, HOME_SERVICES_MODEL_KEY)
        cached_obj = as_dict(cached)
        rows = [as_dict(item) for item in as_list(cached_obj.get("items")) if isinstance(item, dict)]

    if not rows:
        return None

    first_valid_index = 0
    for i, row in enumerate(rows):
        if row.get("type") == "service":
            first_valid_index = i
            break

    return {
        "items": rows,
        "count": len(rows),
        "anchor_index": first_valid_index,
        "get_id": service_row_item_id,
    }

def resolve_home_nodes_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    items: List[Dict[str, Any]] = []

    try:
        node_ids = r.smembers("rt:system:nodes") or []

        for node_id in node_ids:
            key = f"{NODE_KEY_PREFIX}{node_id}"

            if r.type(key) != "hash":
                continue

            item = r.hgetall(key) or {}
            if not item:
                continue

            if not item.get("id"):
                item["id"] = node_id

            items.append(item)

    except Exception:
        items = []

    if not items:
        return None

    items.sort(key=lambda n: str(n.get("id") or "").lower())

    return {
        "items": items,
        "count": len(items),
        "anchor_index": 0,
        "get_id": lambda x: str(x.get("id") or x.get("node_id") or ""),
    }

def resolve_pota_parks_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    context = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
    nearby = get_json_or_value(r, POTA_NEARBY_KEY)

    items = []
    if isinstance(nearby, dict):
        items = as_list(
            nearby.get("choices")
            or nearby.get("parks")
            or nearby.get("items")
            or nearby.get("nearby")
        )
    elif isinstance(nearby, list):
        items = nearby

    if not items:
        return None

    selected_ref = str(
        context.get("selected_park_ref")
        or context.get("park_ref")
        or context.get("reference")
        or ""
    ).strip()

    anchor_index = 0
    if selected_ref:
        for i, item in enumerate(items):
            if park_item_id(as_dict(item)) == selected_ref:
                anchor_index = i
                break

    return {
        "items": items,
        "count": len(items),
        "anchor_index": anchor_index,
        "get_id": park_item_id,
    }


def band_sort_key(item: Any) -> tuple[int, str]:
    raw = str(band_item_id(item) or "").strip().lower()
    if not raw:
        return (9999, "")

    # Common ham band labels like "10m", "20m", "40m"
    if raw.endswith("m"):
        try:
            meters = int(raw[:-1])
            return (meters, raw)
        except Exception:
            pass

    return (9999, raw)


def resolve_pota_bands_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    context = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
    bands_raw = get_json_or_value(r, POTA_BANDS_KEY)

    items = []
    if isinstance(bands_raw, list):
        items = bands_raw
    elif isinstance(bands_raw, dict):
        items = as_list(
            bands_raw.get("bands")
            or bands_raw.get("items")
            or bands_raw.get("choices")
            or bands_raw.get("rows")
        )

    if not items:
        return None

    # Canonical display/order: 10m, 12m, 15m, 17m, 20m, 30m, 40m, 60m, 80m, 160m
    # i.e. ascending meter value to match the current screen behavior
    items = sorted(items, key=band_sort_key)

    selected_band = str(context.get("selected_band") or context.get("band") or "").strip()

    anchor_index = 0
    if selected_band:
        for i, item in enumerate(items):
            if (band_item_id(item) or "") == selected_band:
                anchor_index = i
                break

    return {
        "items": items,
        "count": len(items),
        "anchor_index": anchor_index,
        "get_id": band_item_id,
    }


def resolve_pota_spots_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    spots_raw = get_json_or_value(r, POTA_SPOTS_SELECTED_KEY)

    items = []
    if isinstance(spots_raw, list):
        items = spots_raw
    elif isinstance(spots_raw, dict):
        items = as_list(spots_raw.get("spots") or spots_raw.get("items"))

    if not items:
        return None

    normalized_items = [as_dict(item) for item in items if isinstance(item, dict)]
    if not normalized_items:
        return None

    normalized_items.sort(key=spot_sort_key)

    return {
        "items": normalized_items,
        "count": len(normalized_items),
        "anchor_index": 0,
        "get_id": spot_item_id,
    }

def resolve_hf_bands_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    raw = r.get("rt:hf:bands")
    model = json.loads(raw) if raw else None
    if not isinstance(model, dict):
        return None

    items = as_list(model.get("items"))
    if not items:
        return None

    selected_id = str(model.get("selected_id") or "").strip()

    if not selected_id:
        ctx_raw = r.get("rt:hf:context")
        ctx = json.loads(ctx_raw) if ctx_raw else {}
        if isinstance(ctx, dict):
            selected_id = str(ctx.get("selected_band") or "").strip()

    def get_id(item: Dict[str, Any]) -> str:
        item = as_dict(item)
        return str(
            item.get("id")
            or item.get("band")
            or item.get("label")
            or item.get("name")
            or ""
        ).strip()

    anchor_index = 0
    if selected_id:
        for i, item in enumerate(items):
            if get_id(item) == selected_id:
                anchor_index = i
                break

    return {
        "items": items,
        "count": len(items),
        "anchor_index": anchor_index,
        "window_size": int(model.get("window_size") or 8),
        "get_id": get_id,
    }



def resolve_hf_spots_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    raw = r.get("rt:hf:spots:selected")
    model = json.loads(raw) if raw else None
    if not isinstance(model, dict):
        return None

    items = as_list(model.get("items"))
    if not items:
        return None

    selected_id = str(model.get("selected_id") or "").strip()

    if not selected_id:
        ctx_raw = r.get("rt:hf:context")
        ctx = json.loads(ctx_raw) if ctx_raw else {}
        if isinstance(ctx, dict):
            selected_id = str(ctx.get("selected_spot_id") or "").strip()

    def get_id(item: Dict[str, Any]) -> str:
        item = as_dict(item)
        return str(
            item.get("id")
            or f"{item.get('band')}-{item.get('freq_hz')}-{item.get('callsign')}"
        ).strip()

    anchor_index = 0
    if selected_id:
        for i, item in enumerate(items):
            if get_id(item) == selected_id:
                anchor_index = i
                break

    return {
        "items": items,
        "count": len(items),
        "anchor_index": anchor_index,
        "window_size": int(model.get("window_size") or 10),
        "get_id": get_id,
    }

def hf_selected_spots_model_matches_band(r: redis.Redis, band: str) -> bool:
    band = str(band or "").strip()
    if not band:
        return False

    raw = get_json_or_value(r, "rt:hf:spots:selected")
    model = as_dict(raw)
    if not model:
        return False

    model_band = str(model.get("band") or "").strip()
    if model_band == band:
        return True

    items = as_list(model.get("items"))
    if not items:
        return False

    first = as_dict(items[0])
    first_band = str(first.get("band") or "").strip()
    return first_band == band

def resolve_alerts_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    raw = get_json_or_value(r, "rt:alerts:active")

    items = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = as_list(
            raw.get("alerts")
            or raw.get("items")
            or raw.get("data")
        )

    if not items:
        return None

    normalized_items = [as_dict(item) for item in items if isinstance(item, dict)]

    if not normalized_items:
        return None

    # IMPORTANT: Do NOT sort — preserve upstream order
    return {
        "items": normalized_items,
        "count": len(normalized_items),
        "anchor_index": 0,
        "get_id": lambda x: str(x.get("id") or x.get("alert_id") or ""),
    }

def resolve_vhf_repeaters_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    raw = get_json_or_value(r, "rt:vhf:page")
    model = as_dict(raw)

    left_panel = as_dict(model.get("left_panel"))
    items = as_list(left_panel.get("items"))

    if not items:
        return None

    normalized_items = [as_dict(item) for item in items if isinstance(item, dict)]
    if not normalized_items:
        return None

    def get_id(item: Dict[str, Any]) -> str:
        item = as_dict(item)
        return str(
            item.get("id")
            or item.get("repeater_id")
            or item.get("callsign")
            or item.get("label")
            or ""
        ).strip()

    # Do not auto-anchor to the active scanning row.
    # Start manual browse at the top of the controller-provided list.
    return {
        "items": normalized_items,
        "count": len(normalized_items),
        "anchor_index": 0,
        "window_size": 7,
        "get_id": get_id,
    }

def resolve_browse_model(r: redis.Redis, page_id: str, panel_id: str) -> Dict[str, Any] | None:
    page_id = str(page_id or "").strip()
    panel_id = str(panel_id or "").strip()

    # Alert overlay exists on Home, POTA, HF, and RF Intel.
    # Its browse model is always active-alert based and controller-owned.
    if panel_id == "alerts_overlay":
        return resolve_alerts_browse_model(r)

    if page_id == "home":
        if panel_id == "node_health_summary":
            return resolve_home_nodes_browse_model(r)

        if panel_id == "controller_services_summary":
            return resolve_home_services_browse_model(r)

    if page_id == "pota":
        if panel_id == "pota_parks_summary":
            return resolve_pota_parks_browse_model(r)

        if panel_id == "pota_bands_summary":
            return resolve_pota_bands_browse_model(r)

        if panel_id == "pota_spots_summary":
            return resolve_pota_spots_browse_model(r)

    if page_id == "hf":
        if panel_id == "hf_bands_summary":
            return resolve_hf_bands_browse_model(r)

        if panel_id == "hf_spots_summary":
            return resolve_hf_spots_browse_model(r)
        
    if page_id == "vhf":
        if panel_id == "vhf_repeater_scan_summary":
            return resolve_vhf_repeaters_browse_model(r)

        if panel_id == VHF_RIGHT_PANEL_ID:
            return resolve_vhf_right_panel_browse_model(r)
        
    return None

def build_browse_state(
    page_id: str,
    panel_id: str,
    model: Dict[str, Any],
    selected_index: int,
) -> Dict[str, Any]:
    count = int(model.get("count", 0))
    items = as_list(model.get("items"))
    get_id = model.get("get_id")

    selected_index = clamp_index(selected_index, count)

    selected_id = None
    if 0 <= selected_index < len(items) and callable(get_id):
        item = items[selected_index]
        if isinstance(item, dict):
            selected_id = get_id(as_dict(item))
        else:
            selected_id = get_id(item)
    return {
        "active": True,
        "page": page_id,
        "panel": panel_id,
        "selected_index": selected_index,
        "selected_id": selected_id,
        "count": count,
        "window_size": int(model.get("window_size") or 18),
        "updated_at_ms": now_ms(),
    }

def rotate(lst, current, direction):
    if current not in lst:
        return lst[0] if lst else None

    idx = lst.index(current)
    if direction == "next":
        idx = (idx + 1) % len(lst)
    else:
        idx = (idx - 1) % len(lst)
    return lst[idx]

ALERT_SEVERITY_RANK = {
    "critical": 60,
    "error": 55,
    "bad": 50,
    "warn": 40,
    "warning": 40,
    "watch": 30,
    "info": 20,
    "ok": 10,
}


def alert_severity_rank(alert: Dict[str, Any]) -> int:
    sev = str(alert.get("severity") or alert.get("level") or "").strip().lower()
    return ALERT_SEVERITY_RANK.get(sev, 25)


def alert_time_rank(alert: Dict[str, Any]) -> int:
    for key in ("created_ms", "updated_ms", "last_update_ms", "timestamp_ms", "ts_ms"):
        try:
            value = int(alert.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def ranked_alert_items(items: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    normalized = [as_dict(item) for item in items if isinstance(item, dict)]
    return sorted(
        normalized,
        key=lambda item: (
            alert_severity_rank(item),
            alert_time_rank(item),
        ),
        reverse=True,
    )


def build_alert_list_modal(items: list[Dict[str, Any]]) -> Dict[str, Any]:
    ts = now_ms()
    ranked = ranked_alert_items(items)
    shown = ranked[:10]
    total = len(ranked)

    modal_items = []
    for item in shown:
        title = str(
            item.get("title")
            or item.get("event")
            or item.get("message")
            or item.get("name")
            or "Active alert"
        ).strip()

        message = str(item.get("message") or item.get("details") or item.get("description") or "").strip()
        severity = str(item.get("severity") or item.get("level") or "").strip()
        kind = str(item.get("kind") or item.get("type") or item.get("category") or "alert").strip()
        source = str(item.get("source") or item.get("service") or "").strip()
        when = str(
            item.get("when")
            or item.get("timestamp_utc")
            or item.get("created_utc")
            or item.get("time")
            or item.get("ts")
            or ""
        ).strip()

        meta = " • ".join([x for x in (kind, severity, source, when) if x])

        modal_items.append({
            "title": title,
            "message": message,
            "severity": severity,
            "kind": kind,
            "source": source,
            "when": when,
            "meta": meta,
        })

    if total <= 0:
        summary = "No active alerts."
    elif total <= 10:
        summary = f"Showing {total} active alert{'s' if total != 1 else ''}."
    else:
        summary = f"Showing 10 of {total} active alerts."

    return {
        "active": True,
        "id": f"alert_list:{ts}",
        "type": "alert_list",
        "title": "Active Alerts",
        "message": summary,
        "items": modal_items,
        "total_count": total,
        "shown_count": len(modal_items),
        "confirmable": False,
        "cancelable": True,
        "destructive": False,
        "opened_at_ms": ts,
    }

def build_alert_detail_modal(alert: Dict[str, Any]) -> Dict[str, Any]:
    ts = now_ms()

    title = str(alert.get("title") or "Alert").strip()

    message = str(alert.get("message") or "").strip()
    description = str(alert.get("details") or alert.get("description") or "").strip()

    # Combine cleanly
    if message and description:
        full_text = f"{message}\n\n{description}"
    else:
        full_text = message or description or "No additional details"

    meta_parts = []
    if alert.get("kind"):
        meta_parts.append(str(alert.get("kind")))
    if alert.get("when"):
        meta_parts.append(str(alert.get("when")))
    if alert.get("source"):
        meta_parts.append(str(alert.get("source")))

    submessage = " • ".join(meta_parts) if meta_parts else None

    return {
        "active": True,
        "id": f"alert_detail:{ts}",
        "type": "alert_detail",
        "title": title,
        "message": full_text,
        "submessage": submessage,
        "confirmable": False,
        "cancelable": True,
        "destructive": False,
        "opened_at_ms": ts,
    }


def run_main_loop():
    last_persist_ms = 0
    last_home_services_refresh_ms = 0
    r = redis_client()
    acquire_lock(r)

    pages = load_pages()
    page_index = build_page_index(pages)

    app_cfg = load_app_config()
    has_tuner = get_has_tuner(app_cfg)

    state = default_state(pages)
    if not state:
        raise RuntimeError("no pages loaded")

    save_state(r, state)

    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    def persist_now(changed_keys: list[str]) -> None:
        nonlocal last_persist_ms
        save_state(r, state)
        last_persist_ms = now_ms()
        if changed_keys:
            publish_state_changed(r, changed_keys, source="ui_interaction_state")

    while True:
        msg = ps.get_message(timeout=1.0)
        state_changed = False
        pota_context_changed = False

        if msg:
            try:
                obj = json.loads(msg["data"])
            except Exception:
                obj = None

            if obj:
                intent = obj.get("intent")
                params = obj.get("params") or {}

                current_page = page_index.get(state["page"])
                allowed = current_page.get("controls", {}).get("allowedIntents", []) if current_page else []

                modal_active = isinstance(state.get("modal"), dict)

                modal_intents = {
                    "ui.ok",
                    "ui.cancel",
                    "ui.back",
                    "ui.browse.delta",
                }

                contextual_info_intents = {
                    "ui.info",
                    "info",
                    "button.info",
                }

                if intent in allowed or intent in contextual_info_intents or (modal_active and intent in modal_intents):
                    if intent == "ui.page.next":
                        ids = [p["id"] for p in pages]
                        next_page = rotate(ids, state["page"], "next")
                        page = page_index[next_page]
                        state["page"] = next_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True
                        publish_ui_result(r, intent)

                    elif intent == "ui.page.prev":
                        ids = [p["id"] for p in pages]
                        prev_page = rotate(ids, state["page"], "prev")
                        page = page_index[prev_page]
                        state["page"] = prev_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True
                        publish_ui_result(r, intent)

                    elif intent == "ui.page.goto":
                        target = params.get("page")
                        if target in page_index:
                            page = page_index[target]
                            state["page"] = target
                            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                            state["browse"] = None
                            state["modal"] = None
                            state_changed = True
                            publish_ui_result(r, intent)

                    elif intent == "ui.focus.next":
                        if is_browse_active(state):
                            continue
                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "next")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True
                            publish_ui_result(r, intent)

                    elif intent in ("ui.info", "info", "button.info"):
                        # Contextual INFO action v1:
                        # - Controller-owned
                        # - Active alerts only
                        # - No alert history
                        # - No UI-side modal decision logic
                        if state.get("modal") is not None:
                            publish_ui_result(r, intent, "ignored_modal_active")
                            continue

                        browse = as_dict(state.get("browse"))
                        browse_panel = str(browse.get("panel") or "").strip()
                        focus_panel = str(state.get("focus") or "").strip()

                        info_applies_to_alerts = (
                            browse_panel == "alerts_overlay"
                            or focus_panel == "alerts_overlay"
                        )

                        if not info_applies_to_alerts:
                            publish_ui_result(r, intent, "ignored_no_info_action")
                            continue

                        model = resolve_alerts_browse_model(r)
                        if not model:
                            publish_ui_result(r, intent, "ignored_no_active_alert")
                            continue

                        try:
                            selected_index = (
                                int(browse.get("selected_index", 0))
                                if browse_panel == "alerts_overlay"
                                else int(model.get("anchor_index", 0))
                            )
                        except Exception:
                            selected_index = 0

                        items = as_list(model.get("items"))
                        if not items:
                            publish_ui_result(r, intent, "ignored_no_alert_items")
                            continue

                        state["modal"] = build_alert_list_modal(items)
                        state_changed = True
                        publish_ui_result(r, intent, "alert_list_opened")

                    elif intent == "ui.focus.prev":
                        if is_browse_active(state):
                            continue
                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "prev")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True
                            publish_ui_result(r, intent)

                    elif intent == "ui.focus.set":
                        if is_browse_active(state):
                            continue
                        panel = params.get("panel")
                        if panel in current_page.get("focusPolicy", {}).get("rotation", []):
                            if panel != state["focus"]:
                                state["focus"] = panel
                                state["browse"] = None
                                state_changed = True
                                publish_ui_result(r, intent)

                    elif intent == "ui.cancel":
                        if state.get("modal") is not None:
                            state["modal"] = None
                            state_changed = True
                            publish_ui_result(r, intent)
                        elif is_browse_active(state):
                            state["browse"] = None
                            state_changed = True
                            publish_ui_result(r, intent)

                    elif intent == "ui.back":
                        if state.get("modal") is not None:
                            state["modal"] = None
                            state_changed = True
                            publish_ui_result(r, intent)
                        elif is_browse_active(state):
                            state["browse"] = None
                            state_changed = True
                            publish_ui_result(r, intent)
                        else:
                            ids = [p["id"] for p in pages]
                            prev_page = rotate(ids, state["page"], "prev")
                            page = page_index[prev_page]
                            state["page"] = prev_page
                            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                            state["browse"] = None
                            state["modal"] = None
                            state_changed = True
                            publish_ui_result(r, intent)

                    elif intent == "ui.ok":
                        modal = state.get("modal")

                        if isinstance(modal, dict):
                            modal_type = str(modal.get("type") or "").strip()

                            if modal_type == "node_reboot_confirm":
                                node_id = str(modal.get("node_id") or "").strip().lower()
                                step = str(modal.get("step") or "warn").strip().lower()

                                if node_id == "rt-controller" and step == "warn":
                                    state["modal"] = build_node_reboot_modal(node_id, "armed")
                                else:
                                    if node_id:
                                        publish_intent(r, "node.reboot", {"nodeId": node_id, "confirm": True})
                                    state["modal"] = None

                                state_changed = True
                                publish_ui_result(r, intent)

                            elif modal_type == "pota_spot_outcome":
                                spot_id = str(modal.get("spot_id") or "").strip()
                                options = as_list(modal.get("options"))

                                try:
                                    selected_option_index = int(modal.get("selected_option_index", 0))
                                except Exception:
                                    selected_option_index = 0

                                selected_option_index = clamp_index(selected_option_index, len(options))
                                selected_option = as_dict(options[selected_option_index]) if options else {}
                                outcome_key = str(selected_option.get("key") or "").strip()

                                if not outcome_key:
                                    continue

                                spots_model = resolve_pota_spots_browse_model(r)
                                if not spots_model:
                                    state["modal"] = None
                                    state_changed = True
                                    publish_ui_result(r, intent)
                                    continue

                                target_spot = None
                                for candidate in as_list(spots_model.get("items")):
                                    candidate_dict = as_dict(candidate)
                                    candidate_spot_id = str(
                                        candidate_dict.get("spot_id") or spot_item_id(candidate_dict) or ""
                                    ).strip()
                                    if candidate_spot_id and candidate_spot_id == spot_id:
                                        target_spot = candidate_dict
                                        break

                                if target_spot is None:
                                    browse = as_dict(state.get("browse"))
                                    try:
                                        selected_index = int(browse.get("selected_index", 0))
                                    except Exception:
                                        selected_index = 0
                                    target_spot = selected_item_from_model(spots_model, selected_index)

                                if target_spot:
                                    apply_pota_spot_outcome_state(r, target_spot, outcome_key)
                                    if outcome_key == "worked":
                                        publish_radio_log_qso_intent(r, target_spot)

                                state["modal"] = None
                                state_changed = True
                                publish_ui_result(r, intent)

                            elif modal_type == "hf_spot_outcome":
                                spot_id = str(modal.get("spot_id") or "").strip()
                                options = as_list(modal.get("options"))

                                try:
                                    selected_option_index = int(modal.get("selected_option_index", 0))
                                except Exception:
                                    selected_option_index = 0

                                selected_option_index = clamp_index(selected_option_index, len(options))
                                selected_option = as_dict(options[selected_option_index]) if options else {}
                                outcome_key = str(selected_option.get("key") or "").strip()

                                if not outcome_key:
                                    continue

                                spots_model = resolve_hf_spots_browse_model(r)
                                if not spots_model:
                                    state["modal"] = None
                                    state_changed = True
                                    publish_ui_result(r, intent)
                                    continue

                                target_spot = None
                                for candidate in as_list(spots_model.get("items")):
                                    candidate_dict = as_dict(candidate)
                                    candidate_spot_id = hf_spot_item_id(candidate_dict)
                                    if candidate_spot_id and candidate_spot_id == spot_id:
                                        target_spot = candidate_dict
                                        break

                                if target_spot is None:
                                    browse = as_dict(state.get("browse"))
                                    try:
                                        selected_index = int(browse.get("selected_index", 0))
                                    except Exception:
                                        selected_index = 0
                                    target_spot = selected_item_from_model(spots_model, selected_index)

                                if target_spot:
                                    publish_hf_spot_outcome_intent(r, target_spot, outcome_key)

                                state["modal"] = None
                                state_changed = True
                                publish_ui_result(r, intent)

                            elif modal_type == "vhf_future_enhancement":
                                state["modal"] = None
                                state_changed = True
                                publish_ui_result(r, intent, "vhf_future_enhancement_closed")

                            else:
                                publish_ui_result(r, intent, "ignored_unknown_modal")

                        elif (
                            state.get("page") == "vhf"
                            and str(state.get("focus") or "").strip() == VHF_RIGHT_PANEL_ID
                        ):
                            result, changed = handle_vhf_right_panel_action(r, state, intent)
                            state_changed = state_changed or changed
                            publish_ui_result(r, intent, result)

                        elif is_browse_active(state):
                            browse = as_dict(state.get("browse"))
                            panel_id = str(browse.get("panel") or "").strip()

                            model = resolve_browse_model(r, state["page"], panel_id)
                            if not model:
                                continue

                            try:
                                selected_index = int(browse.get("selected_index", 0))
                            except Exception:
                                selected_index = 0

                            item = selected_item_from_model(model, selected_index)
                            if not item:
                                continue

                            if state["page"] == "home" and panel_id == "node_health_summary":
                                node_id = extract_node_id(item)
                                if node_id:
                                    state["modal"] = build_node_reboot_modal(node_id, "warn")
                                    state_changed = True
                                    publish_ui_result(r, intent)

                            elif state["page"] == "home" and panel_id == "controller_services_summary":
                                publish_ui_result(r, intent, "ignored_service_row")

                            elif panel_id == "alerts_overlay":
                                items = as_list(model.get("items"))
                                state["modal"] = build_alert_list_modal(items)
                                state_changed = True
                                publish_ui_result(r, intent, "alert_list_opened")

                            elif state["page"] == "pota" and panel_id == "pota_parks_summary":
                                park_ref = str(
                                    item.get("reference")
                                    or item.get("park_ref")
                                    or item.get("id")
                                    or ""
                                ).strip()
                                if not park_ref:
                                    continue

                                publish_intent(r, "pota.select_park", {"park_ref": park_ref})
                                state_changed = True
                                publish_ui_result(r, intent)

                            elif state["page"] == "pota" and panel_id == "pota_bands_summary":
                                # Resolve band from ACTIVE browse state (authoritative)
                                browse = as_dict(state.get("browse"))

                                selected_id = str(browse.get("selected_id") or "").strip()
                                new_band = selected_id

                                # Fallback ONLY if browse is missing (should not happen)
                                if not new_band:
                                    new_band = str(
                                        item.get("band")
                                        or item.get("id")
                                        or item.get("name")
                                        or item
                                        or ""
                                    ).strip()

                                if not new_band:
                                    continue

                                # Clear any stale band-select/tune action before applying the newly selected band.
                                state["modal"] = None
                                state["pending_action"] = None

                                current_ctx = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
                                old_band = str(current_ctx.get("selected_band") or current_ctx.get("band") or "").strip()
                                band_changed = old_band != new_band

                                update_pota_context_selected_band(r, new_band)
                                pota_context_changed = True

                                state["focus"] = "pota_spots_summary"
                                state["browse"] = None

                                state["pending_action"] = {
                                    "type": "tune_first_spot_after_band_select",
                                    "page": "pota",
                                    "panel": "pota_spots_summary",
                                    "band": new_band,
                                }

                                state_changed = True
                                publish_ui_result(r, intent)
                                publish_state_changed(r, [POTA_CONTEXT_KEY], source="ui_interaction_state")

                                if band_changed and not has_tuner:
                                    state["modal"] = build_band_tune_reminder_modal(new_band)
                                    state["pending_action"] = {
                                        "type": "tune_first_spot_after_reminder",
                                        "page": "pota",
                                        "panel": "pota_spots_summary",
                                        "band": new_band,
                                        "ts_ms": now_ms(),
                                    }

                            elif state["page"] == "pota" and panel_id == "pota_spots_summary":
                                state["modal"] = build_pota_spot_outcome_modal(item)
                                state_changed = True
                                publish_ui_result(r, intent)

                            elif state["page"] == "hf" and panel_id == "hf_bands_summary":
                                band = hf_band_item_id(item)
                                if not band:
                                    continue

                                publish_hf_select_band_intent(r, item)

                                # Do NOT switch focus/browse yet.
                                # ui_intent_worker must first update rt:hf:spots:selected.
                                # The bottom pending_action block will enter hf_spots_summary
                                # only after the selected spots model actually matches this band.
                                state["pending_action"] = {
                                    "type": "enter_hf_spots_after_band_select",
                                    "page": "hf",
                                    "panel": "hf_spots_summary",
                                    "band": band,
                                    "ts_ms": now_ms(),
                                }

                                state_changed = True
                                publish_ui_result(r, intent, "hf_band_selected")

                            elif state["page"] == "hf" and panel_id == "hf_spots_summary":
                                # HF handler owns detail/history/tune so OK and encoder
                                # press follow the same single-tune path.
                                publish_hf_select_spot_intent(r, item)

                                state["modal"] = build_hf_spot_outcome_modal(item)
                                state_changed = True
                                publish_ui_result(r, intent, "hf_spot_selected")

                            elif state["page"] == "vhf" and panel_id == "vhf_repeater_scan_summary":
                                browse = as_dict(state.get("browse"))

                                try:
                                    browse_selected_index = int(browse.get("selected_index", selected_index))
                                except Exception:
                                    browse_selected_index = selected_index

                                browse_selected_id = str(browse.get("selected_id") or "").strip()

                                result = publish_vhf_repeater_select_request(
                                    r,
                                    item,
                                    browse_selected_index,
                                    browse_selected_id,
                                )

                                publish_ui_result(r, intent, result)

                            else:
                                publish_ui_result(r, intent, "ignored_no_ok_handler")

                        else:
                            publish_ui_result(r, intent, "ignored_no_modal_or_browse")

                    elif intent == "ui.encoder.press":
                        # Encoder press is a panel-local shortcut. It must not confirm modals.
                        if state.get("modal") is not None:
                            publish_ui_result(r, intent, "ignored_modal_active")
                            continue

                        if (
                            state.get("page") == "vhf"
                            and str(state.get("focus") or "").strip() == VHF_RIGHT_PANEL_ID
                        ):
                            publish_intent(r, "ui.ok", {})
                            publish_ui_result(r, intent, "vhf_right_encoder_press_routed_to_ok")
                            continue

                        if not is_browse_active(state):
                            publish_ui_result(r, intent, "ignored_no_browse")
                            continue

                        browse = as_dict(state.get("browse"))
                        panel_id = str(browse.get("panel") or "").strip()

                        model = resolve_browse_model(r, state["page"], panel_id)
                        if not model:
                            publish_ui_result(r, intent, "ignored_no_model")
                            continue

                        try:
                            selected_index = int(browse.get("selected_index", 0))
                        except Exception:
                            selected_index = 0

                        item = selected_item_from_model(model, selected_index)
                        if not item:
                            publish_ui_result(r, intent, "ignored_no_item")
                            continue

                        if state["page"] == "pota" and panel_id == "pota_spots_summary":
                            # Encoder press tunes only. OK still opens/logs outcome modal.
                            publish_radio_tune_intent(r, item)
                            publish_ui_result(r, intent, "pota_spot_tuned")
                            continue

                        if state["page"] == "hf" and panel_id == "hf_spots_summary":
                            # Encoder press selects/tunes via the controller-owned HF handler.
                            # Do not publish radio.tune here; hf.select_spot owns detail/history/tune.
                            # Do not rebuild/clear browse; stay exactly where the selector is.
                            publish_hf_select_spot_intent(r, item)
                            publish_ui_result(r, intent, "hf_spot_selected")
                            continue
                        
                        if state["page"] == "hf" and panel_id == "hf_bands_summary":
                            band = hf_band_item_id(item)
                            if not band:
                                continue

                            publish_hf_select_band_intent(r, item)

                            # Same delayed-entry flow as OK on an HF band.
                            # Keep current band browse active until the new spots model is ready.
                            state["pending_action"] = {
                                "type": "enter_hf_spots_after_band_select",
                                "page": "hf",
                                "panel": "hf_spots_summary",
                                "band": band,
                                "ts_ms": now_ms(),
                            }

                            state_changed = True
                            publish_ui_result(r, intent, "hf_band_selected")
                            continue


                    elif intent == "ui.browse.enter":
                        panel_id = state.get("focus")

                        model = resolve_browse_model(r, state["page"], panel_id)
                        if not model:
                            continue

                        count = int(model.get("count", 0))
                        if count <= 0:
                            continue

                        anchor_index = int(model.get("anchor_index", 0))

                        state["browse"] = build_browse_state(
                            state["page"],
                            panel_id,
                            model,
                            anchor_index,
                        )

                        state_changed = True

                    elif intent == "ui.browse.delta":
                        if state.get("focus"):
                            try:
                                delta = int(params.get("delta", 0))
                            except Exception:
                                delta = 0

                            if delta == 0:
                                continue

                            modal = as_dict(state.get("modal"))
                            modal_type = str(modal.get("type") or "").strip()

                            if modal_type in ("pota_spot_outcome", "hf_spot_outcome"):
                                options = as_list(modal.get("options"))
                                option_count = len(options)
                                if option_count <= 0:
                                    continue

                                try:
                                    current_option_index = int(modal.get("selected_option_index", 0))
                                except Exception:
                                    current_option_index = 0

                                new_option_index = clamp_index(current_option_index + delta, option_count)

                                if new_option_index != current_option_index:
                                    modal["selected_option_index"] = new_option_index
                                    state["modal"] = modal
                                    state_changed = True
                                    publish_ui_result(r, intent)

                                # CRITICAL: modal movement must publish immediately.
                                if state_changed:
                                    # Persist but DO NOT short-circuit loop
                                    save_state(r, state)
                                    last_persist_ms = now_ms()
                                    publish_state_changed(r, [INTERACTION_KEY], source="ui_interaction_state")

                                continue

                            if (
                                state.get("page") == "vhf"
                                and str(state.get("focus") or "").strip() == VHF_RIGHT_PANEL_ID
                            ):
                                model = resolve_vhf_right_panel_browse_model(r)
                                if not model:
                                    continue

                                count = int(model.get("count", 0))
                                if count <= 0:
                                    continue

                                browse = as_dict(state.get("browse"))
                                browse_panel = str(browse.get("panel") or "").strip()

                                if browse_panel == VHF_RIGHT_PANEL_ID and bool(browse.get("active", True)):
                                    try:
                                        current_index = int(browse.get("selected_index", 0))
                                    except Exception:
                                        current_index = 0
                                else:
                                    try:
                                        current_index = int(model.get("anchor_index", 0))
                                    except Exception:
                                        current_index = 0

                                current_index = clamp_index(current_index, count)
                                new_index = clamp_index(current_index + delta, count)

                                if new_index != current_index or browse_panel != VHF_RIGHT_PANEL_ID:
                                    state["browse"] = build_browse_state(
                                        "vhf",
                                        VHF_RIGHT_PANEL_ID,
                                        model,
                                        new_index,
                                    )
                                    state_changed = True
                                    publish_ui_result(r, intent, "vhf_right_action_selected")

                                if state_changed:
                                    save_state(r, state)
                                    last_persist_ms = now_ms()
                                    publish_state_changed(
                                        r,
                                        [INTERACTION_KEY],
                                        source="ui_interaction_state",
                                    )

                                continue

                            model = resolve_browse_model(r, state["page"], state["focus"])
                            if not model:
                                continue

                            count = int(model.get("count", 0))
                            if count <= 0:
                                continue

                            browse = state.get("browse")
                            panel_id = state["focus"]

                            if not isinstance(browse, dict) or browse.get("panel") != panel_id or not browse.get("active", True):
                                anchor_index = int(model.get("anchor_index", 0))
                                if state["page"] == "pota" and panel_id == "pota_spots_summary":
                                    new_index = find_next_browse_index_for_pota_spots(r, model, anchor_index, delta)
                                else:
                                    if state["page"] == "vhf" and panel_id == "vhf_repeater_scan_summary":
                                        #new_index = max(0, min(count - 1, current_index + delta))
                                        new_index = max(0, min(count - 1, anchor_index + delta))
                                    else:
                                        new_index = clamp_index(current_index + delta, count)

                                state["browse"] = build_browse_state(state["page"], panel_id, model, new_index)
                                state_changed = True

                            else:
                                try:
                                    current_index = int(browse.get("selected_index", 0))
                                except Exception:
                                    current_index = 0

                                if state["page"] == "pota" and panel_id == "pota_spots_summary":
                                    new_index = find_next_browse_index_for_pota_spots(r, model, current_index, delta)
                                else:
                                    if state["page"] == "vhf" and panel_id == "vhf_repeater_scan_summary":
                                        #new_index = max(0, min(count - 1, anchor_index + delta))
                                        new_index = max(0, min(count - 1, current_index + delta))
                                    else:
                                        new_index = clamp_index(anchor_index + delta, count)

                                if new_index != current_index:
                                    state["browse"] = build_browse_state(state["page"], panel_id, model, new_index)
                                    state_changed = True

        now = now_ms()

        pending_action = as_dict(state.get("pending_action"))

        if pending_action.get("type") == "enter_hf_spots_after_band_select":
            target_band = str(pending_action.get("band") or "").strip()

            if hf_selected_spots_model_matches_band(r, target_band):
                spots_model = resolve_hf_spots_browse_model(r)

                if spots_model:
                    state["focus"] = "hf_spots_summary"
                    state["browse"] = build_browse_state(
                        "hf",
                        "hf_spots_summary",
                        spots_model,
                        0,
                    )

                    state["pending_action"] = None
                    state_changed = True

        elif (
            pending_action.get("type") == "tune_first_spot_after_band_select"
            or (
                pending_action.get("type") == "tune_first_spot_after_reminder"
                and state.get("modal") is None
            )
        ):
            spots_model = resolve_pota_spots_browse_model(r)

            if spots_model:
                # Enter browse correctly
                state["focus"] = "pota_spots_summary"
                state["browse"] = build_browse_state(
                    "pota",
                    "pota_spots_summary",
                    spots_model,
                    0,
                )

                # Tune first spot
                first_spot = selected_item_from_model(spots_model, 0)
                if first_spot:
                    publish_radio_tune_intent(r, first_spot)

                state["pending_action"] = None
                state_changed = True

        # Controller-owned services model refresh cadence.
        # HOME active: refresh every 60s.
        # Away from HOME: refresh every 1h.
        current_page_id = str(state.get("page") or "").strip()
        services_refresh_interval_ms = (
            HOME_SERVICES_REFRESH_HOME_MS
            if current_page_id == "home"
            else HOME_SERVICES_REFRESH_AWAY_MS
        )

        if (now - last_home_services_refresh_ms) >= services_refresh_interval_ms:
            resolve_home_services_browse_model(r)
            last_home_services_refresh_ms = now
            publish_state_changed(
                r,
                [HOME_SERVICES_MODEL_KEY],
                source="ui_interaction_state:home_services_refresh",
            )

        modal = state.get("modal")
        if isinstance(modal, dict):
            modal_type = str(modal.get("type") or "").strip()
            try:
                auto_close_at_ms = int(modal.get("auto_close_at_ms", 0))
            except Exception:
                auto_close_at_ms = 0

            if modal_type == "band_tune_reminder" and auto_close_at_ms and now >= auto_close_at_ms:
                state["modal"] = None
                state_changed = True

        if state_changed:
            save_state(r, state)
            last_persist_ms = now

            changed_keys = [INTERACTION_KEY]
            if pota_context_changed:
                changed_keys.append(POTA_CONTEXT_KEY)

            publish_state_changed(r, changed_keys, source="ui_interaction_state")

        elif (now - last_persist_ms) >= INTERACTION_HEARTBEAT_MS:
            # Keep writer lock alive without rewriting unchanged interaction state.
            r.pexpire(WRITER_LOCK_KEY, 10000)
            last_persist_ms = now

        time.sleep(0.05)

def main():
    while True:
        try:
            run_main_loop()
        except redis.exceptions.RedisError as e:
            print(f"ui_interaction_state: Redis error, reconnecting: {type(e).__name__}: {e}", flush=True)
            time.sleep(1)
        except Exception as e:
            import traceback
            print(f"ui_interaction_state: fatal loop error, restarting: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            time.sleep(1)


if __name__ == "__main__":
    main()