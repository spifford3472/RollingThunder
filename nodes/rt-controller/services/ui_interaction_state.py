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
POTA_CONTEXT_KEY = "rt:pota:context"
POTA_NEARBY_KEY = "rt:pota:nearby"
POTA_BANDS_KEY = "rt:pota:ui:ssb:bands"
POTA_SPOTS_SELECTED_KEY = "rt:pota:ui:ssb:spots:selected"
POTA_SPOT_STATUS_KEY_PREFIX ="rt:pota:spot_status:"

def utc_day_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    direction = 1 if delta > 0 else -1
    start = clamp_index(current_index, count)

    for step in range(1, count + 1):
        idx = (start + (step * direction)) % count
        item = as_dict(items[idx])
        if not item:
            continue
        if not is_browse_skippable_pota_spot(r, item):
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

    params = {
        "freq_hz": int(freq_hz or 0),
        "band": str(spot.get("band") or "").strip() or None,
        "mode": str(spot.get("mode") or "SSB").strip() or "SSB",
        "spot_id": str(spot.get("spot_id") or spot_item_id(spot) or "").strip() or None,
    }

    publish_intent(r, "radio.tune", params)

def publish_pota_spot_outcome_intent(r: redis.Redis, spot: Dict[str, Any], outcome: str) -> None:
    params = {
        "outcome": str(outcome or "").strip(),
        "spot_id": str(spot.get("spot_id") or spot_item_id(spot) or "").strip() or None,
        "callsign": str(spot.get("callsign") or spot.get("call") or "").strip() or None,
        "park_ref": str(spot.get("park_ref") or spot.get("reference") or "").strip() or None,
        "band": str(spot.get("band") or "").strip() or None,
        "mode": str(spot.get("mode") or "SSB").strip() or "SSB",
    }

    freq_hz = spot.get("freq_hz")
    if freq_hz is None:
        try:
            freq_hz = int(float(str(spot.get("frequency") or "0")))
        except Exception:
            freq_hz = 0

    params["freq_hz"] = int(freq_hz or 0)

    publish_intent(r, "pota.spot.outcome", params)

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

def load_pages() -> List[Dict[str, Any]]:
    pages = []
    for f in CONFIG_PAGES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            pages.append(data)
        except Exception:
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

def resolve_home_nodes_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    items: List[Dict[str, Any]] = []

    try:
        for key in r.scan_iter(match=f"{NODE_KEY_PREFIX}*"):
            ks = str(key)
            if not ks.startswith(NODE_KEY_PREFIX):
                continue

            if r.type(ks) != "hash":
                continue

            item = r.hgetall(ks) or {}
            if not item:
                continue

            node_id = ks[len(NODE_KEY_PREFIX):].strip()
            if node_id and not item.get("id"):
                item["id"] = node_id

            items.append(item)
    except Exception:
        items = []

    if not items:
        return None

    items.sort(key=lambda n: str(n.get("id") or n.get("node_id") or "").lower())

    return {
        "items": items,
        "count": len(items),
        "anchor_index": 0,
        "get_id": node_item_id,
    }

def resolve_pota_parks_browse_model(r: redis.Redis) -> Dict[str, Any] | None:
    context = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
    nearby = get_json_or_value(r, POTA_NEARBY_KEY)

    items = []
    if isinstance(nearby, dict):
        items = as_list(nearby.get("parks") or nearby.get("items") or nearby.get("nearby"))
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


def resolve_browse_model(r: redis.Redis, page_id: str, panel_id: str) -> Dict[str, Any] | None:
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


def main():
    last_persist_ms = 0
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

    while True:
        msg = ps.get_message(timeout=1.0)
        state_changed = False

        if msg:
            try:
                obj = json.loads(msg["data"])
            except Exception:
                obj = None

            if obj:
                intent = obj.get("intent")
                params = obj.get("params") or {}

                current_page = page_index.get(state["page"])
                allowed = current_page.get("controls", {}).get("allowedIntents", [])

                if intent in allowed:
                    if intent == "ui.page.next":
                        ids = [p["id"] for p in pages]
                        next_page = rotate(ids, state["page"], "next")
                        page = page_index[next_page]
                        state["page"] = next_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True

                    elif intent == "ui.page.prev":
                        ids = [p["id"] for p in pages]
                        prev_page = rotate(ids, state["page"], "prev")
                        page = page_index[prev_page]
                        state["page"] = prev_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True

                    elif intent == "ui.page.goto":
                        target = params.get("page")
                        if target in page_index:
                            page = page_index[target]
                            state["page"] = target
                            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                            state["browse"] = None
                            state["modal"] = None
                            state_changed = True

                    elif intent == "ui.focus.next":
                        if is_browse_active(state):
                            continue

                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "next")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True

                    elif intent == "ui.focus.prev":
                        if is_browse_active(state):
                            continue

                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "prev")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True

                    elif intent == "ui.focus.set":
                        if is_browse_active(state):
                            continue

                        panel = params.get("panel")
                        if panel in current_page.get("focusPolicy", {}).get("rotation", []):
                            if panel != state["focus"]:
                                state["focus"] = panel
                                state["browse"] = None
                                state_changed = True

                    elif intent == "ui.cancel":
                        modal = as_dict(state.get("modal"))
                        modal_type = str(modal.get("type") or "").strip()

                        if modal_type == "pota_spot_outcome":
                            state["modal"] = None
                            state_changed = True
                        elif state.get("modal") is not None:
                            state["modal"] = None
                            state_changed = True
                        elif is_browse_active(state):
                            state["browse"] = None
                            state_changed = True

                    elif intent == "ui.back":
                        if state.get("modal") is not None:
                            state["modal"] = None
                            state_changed = True
                        elif is_browse_active(state):
                            state["browse"] = None
                            state_changed = True
                        else:
                            ids = [p["id"] for p in pages]
                            prev_page = rotate(ids, state["page"], "prev")
                            page = page_index[prev_page]
                            state["page"] = prev_page
                            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                            state["browse"] = None
                            state["modal"] = None
                            state_changed = True

                    elif intent == "ui.ok":
                        modal = state.get("modal")
                        if isinstance(modal, dict):
                            modal_type = str(modal.get("type") or "").strip()

                            if modal_type == "node_reboot_confirm":
                                node_id = str(modal.get("node_id") or "").strip().lower()
                                step = str(modal.get("step") or "warn").strip().lower()

                                if node_id == "rt-controller" and step == "warn":
                                    state["modal"] = build_node_reboot_modal(node_id, "armed")
                                    state_changed = True
                                else:
                                    if node_id:
                                        publish_intent(r, "node.reboot", {"nodeId": node_id, "confirm": True})
                                    state["modal"] = None
                                    state_changed = True
                            elif modal_type == "pota_spot_outcome":
                                spot_id = str(modal.get("spot_id") or "").strip()
                                options = as_list(modal.get("options"))

                                selected_option_index = 0
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
                                    selected_index = 0
                                    try:
                                        selected_index = int(browse.get("selected_index", 0))
                                    except Exception:
                                        selected_index = 0
                                    target_spot = selected_item_from_model(spots_model, selected_index)

                                if target_spot:
                                    publish_pota_spot_outcome_intent(r, target_spot, outcome_key)
                                    apply_pota_spot_outcome_state(r, target_spot, outcome_key)
                                    if outcome_key == "worked":
                                        publish_radio_log_qso_intent(r, target_spot)
                                state["modal"] = None
                                state_changed = True                                    

                        elif is_browse_active(state):
                            browse = as_dict(state.get("browse"))
                            panel_id = str(browse.get("panel") or "").strip()

                            model = resolve_browse_model(r, state["page"], panel_id)
                            if not model:
                                continue

                            selected_index = 0
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

                            elif state["page"] == "home" and panel_id == "controller_services_summary":
                                continue

                            elif state["page"] == "pota" and panel_id == "pota_bands_summary":
                                new_band = str(
                                    item.get("band")
                                    or item.get("id")
                                    or item.get("name")
                                    or item
                                    or ""
                                ).strip()
                                if not new_band:
                                    continue

                                current_ctx = as_dict(get_json_or_value(r, POTA_CONTEXT_KEY))
                                old_band = str(current_ctx.get("selected_band") or current_ctx.get("band") or "").strip()
                                band_changed = (old_band != new_band)

                                update_pota_context_selected_band(r, new_band)

                                state["browse"] = None
                                state["focus"] = "pota_spots_summary"
                                state_changed = True

                                if band_changed and not has_tuner:
                                    state["modal"] = build_band_tune_reminder_modal(new_band)
                                    state["pending_action"] = {
                                        "type": "tune_first_spot_after_reminder",
                                        "band": new_band,
                                        "ts_ms": now_ms(),
                                    }
                                else:
                                    spots_model = resolve_pota_spots_browse_model(r)
                                    first_spot = selected_item_from_model(spots_model, 0) if spots_model else None
                                    if first_spot:
                                        publish_radio_tune_intent(r, first_spot)
                                    state["pending_action"] = None

                            elif state["page"] == "pota" and panel_id == "pota_spots_summary":
                                state["modal"] = build_pota_spot_outcome_modal(item)
                                state_changed = True

                    elif intent == "ui.browse.delta":
                        if state.get("focus"):
                            delta = 0
                            try:
                                delta = int(params.get("delta", 0))
                            except Exception:
                                delta = 0

                            if delta == 0:
                                continue

                            modal = as_dict(state.get("modal"))
                            modal_type = str(modal.get("type") or "").strip()

                            if modal_type == "pota_spot_outcome":
                                options = as_list(modal.get("options"))
                                option_count = len(options)
                                if option_count <= 0:
                                    continue

                                current_option_index = 0
                                try:
                                    current_option_index = int(modal.get("selected_option_index", 0))
                                except Exception:
                                    current_option_index = 0

                                new_option_index = clamp_index(current_option_index + delta, option_count)
                                if new_option_index != current_option_index:
                                    modal["selected_option_index"] = new_option_index
                                    state["modal"] = modal
                                    state_changed = True
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
                                    new_index = clamp_index(anchor_index + delta, count)
                                state["browse"] = build_browse_state(
                                    state["page"],
                                    panel_id,
                                    model,
                                    new_index,
                                )
                                if state["page"] == "pota" and panel_id == "pota_spots_summary":
                                    item = selected_item_from_model(model, new_index)
                                    if item:
                                        publish_radio_tune_intent(r, item)                                
                                state_changed = True
                            else:
                                current_index = 0
                                try:
                                    current_index = int(browse.get("selected_index", 0))
                                except Exception:
                                    current_index = 0
                                if state["page"] == "pota" and panel_id == "pota_spots_summary":
                                    new_index = find_next_browse_index_for_pota_spots(r, model, current_index, delta)
                                else:
                                    new_index = clamp_index(current_index + delta, count)
                                if new_index != current_index:
                                    state["browse"] = build_browse_state(
                                        state["page"],
                                        panel_id,
                                        model,
                                        new_index,
                                    )
                                    if state["page"] == "pota" and panel_id == "pota_spots_summary":
                                        item = selected_item_from_model(model, new_index)
                                        if item:
                                            publish_radio_tune_intent(r, item)                                    
                                    state_changed = True

        now = now_ms()

        modal = state.get("modal")
        if isinstance(modal, dict):
            modal_type = str(modal.get("type") or "").strip()
            auto_close_at_ms = 0
            try:
                auto_close_at_ms = int(modal.get("auto_close_at_ms", 0))
            except Exception:
                auto_close_at_ms = 0

            if modal_type == "band_tune_reminder" and auto_close_at_ms and now >= auto_close_at_ms:
                state["modal"] = None

                pending = as_dict(state.get("pending_action"))
                if pending.get("type") == "tune_first_spot_after_reminder":
                    spots_model = resolve_pota_spots_browse_model(r)
                    first_spot = selected_item_from_model(spots_model, 0) if spots_model else None
                    if first_spot:
                        publish_radio_tune_intent(r, first_spot)
                    state["pending_action"] = None

                state_changed = True

        if state_changed or (now - last_persist_ms) >= INTERACTION_HEARTBEAT_MS:
            save_state(r, state)
            last_persist_ms = now

        time.sleep(0.05)

if __name__ == "__main__":
    main()