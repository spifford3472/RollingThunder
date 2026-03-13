#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
UI_BUS_CH = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")

CONFIG_PATH = Path(os.environ.get("RT_CONFIG_PATH", "/opt/rollingthunder/config/app.json"))
NODE_ID = os.environ.get("RT_NODE_ID", "unknown-node")

# Reboot behavior:
# - "reboot" (default) -> systemctl reboot
# - "poweroff"         -> systemctl poweroff
REBOOT_MODE = os.environ.get("RT_NODE_REBOOT_MODE", "reboot").strip().lower()
SYSTEMCTL_TIMEOUT_SEC = float(os.environ.get("RT_SYSTEMCTL_TIMEOUT_SEC", "8.0"))

# Safety: default off unless explicitly enabled on that node.
ALLOW_NODE_REBOOT = (
    os.environ.get("RT_ALLOW_REBOOT", "0").strip() == "1"
    or os.environ.get("RT_ALLOW_NODE_REBOOT", "0").strip() == "1"
)

# POTA UI context key in Redis, used by both the context manager and the UI intent worker.
POTA_CONTEXT_KEY = os.environ.get("RT_POTA_CONTEXT_KEY", "rt:pota:context")

POTA_BAND_ORDER = {
    "160m", "80m", "60m", "40m", "30m",
    "20m", "17m", "15m", "12m", "10m",
    "6m",
}

def env_truthy(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def default_pota_context() -> Dict[str, Any]:
    return {
        "selected_park_ref": "",
        "selected_park_name": "Not in a park",
        "selected_park_refs": [],
        "selected_park_names": [],
        "left_selected_park_refs": [],
        "selected_band": "",
        "grid": "",
        "selection_ts": now_ms(),
    }

def load_json_object(r: redis.Redis, key: str) -> Dict[str, Any] | None:
    raw = r.get(key)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen = set()
    for item in value:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def normalize_pota_context(existing: Dict[str, Any] | None) -> Dict[str, Any]:
    base = default_pota_context()
    if not existing:
        return base

    ctx = {
        "selected_park_ref": str(existing.get("selected_park_ref", "") or ""),
        "selected_park_name": str(existing.get("selected_park_name", "") or ""),
        "selected_park_refs": _normalize_string_list(existing.get("selected_park_refs", [])),
        "selected_park_names": _normalize_string_list(existing.get("selected_park_names", [])),
        "left_selected_park_refs": _normalize_string_list(existing.get("left_selected_park_refs", [])),
        "selected_band": str(existing.get("selected_band", "") or ""),
        "grid": str(existing.get("grid", "") or ""),
        "selection_ts": existing.get("selection_ts", base["selection_ts"]),
    }

    if ctx["selected_band"] and ctx["selected_band"] not in POTA_BAND_ORDER:
        ctx["selected_band"] = ""

    # Keep singular/plural park fields compatible
    if ctx["selected_park_refs"]:
        ctx["selected_park_ref"] = ctx["selected_park_refs"][0]
        if ctx["selected_park_names"]:
            ctx["selected_park_name"] = ctx["selected_park_names"][0]
        elif not ctx["selected_park_name"]:
            ctx["selected_park_name"] = ""
    else:
        if ctx["selected_park_ref"]:
            ctx["selected_park_refs"] = [ctx["selected_park_ref"]]
            if ctx["selected_park_name"] and ctx["selected_park_name"] != "Not in a park":
                ctx["selected_park_names"] = [ctx["selected_park_name"]]
        else:
            ctx["selected_park_ref"] = ""
            ctx["selected_park_name"] = "Not in a park"
            ctx["selected_park_refs"] = []
            ctx["selected_park_names"] = []

    try:
        ctx["selection_ts"] = int(ctx["selection_ts"])
    except Exception:
        ctx["selection_ts"] = base["selection_ts"]

    return ctx

def now_ms() -> int:
    return int(time.time() * 1000)

def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    r.ping()
    return r

def publish_bus(r: redis.Redis, payload: Dict[str, Any]) -> None:
    r.publish(UI_BUS_CH, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

def _truthy(x: Any) -> bool:
    if x is True:
        return True
    if isinstance(x, str) and x.strip().lower() in ("1", "true", "yes", "y", "on"):
        return True
    if isinstance(x, (int, float)) and x == 1:
        return True
    return False

def reboot_this_node() -> tuple[bool, str]:
    cmd = ["systemctl", "--no-wall"]
    if REBOOT_MODE == "poweroff":
        cmd += ["poweroff"]
    else:
        cmd += ["reboot"]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
        if res.returncode == 0:
            return True, f"{REBOOT_MODE}_initiated"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"{REBOOT_MODE}_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        return True, f"{REBOOT_MODE}_initiated_timeout"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"

def handle_node_reboot(r: redis.Redis, params: Dict[str, Any]) -> None:
    target = str(params.get("nodeId") or params.get("node_id") or "").strip()
    confirm = _truthy(params.get("confirm"))

    base = {
        "topic": "ui.node.reboot.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
    }

    if not target:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:missing_nodeId"})
        return

    if target != NODE_ID:
        publish_bus(r, {**base, "ok": False, "msg": "not_for_this_node"})
        return

    if not ALLOW_NODE_REBOOT:
        publish_bus(r, {**base, "ok": False, "msg": "reboot_disabled"})
        return

    if not confirm:
        publish_bus(r, {**base, "ok": False, "msg": "not_confirmed"})
        return

    ok, msgtxt = reboot_this_node()
    publish_bus(r, {**base, "ok": ok, "msg": msgtxt})

def handle_radio_tune(r: redis.Redis, params: Dict[str, Any]) -> None:
    freq_hz = params.get("freq_hz")
    band = str(params.get("band") or "").strip()
    mode = str(params.get("mode") or "").strip()
    autotune = bool(params.get("autotune", False))
    target = str(params.get("nodeId") or params.get("node_id") or "rt-radio").strip()

    base = {
        "topic": "ui.radio.tune.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
        "freq_hz": freq_hz,
        "band": band,
        "mode": mode,
        "autotune": autotune,
    }

    if target != NODE_ID:
        publish_bus(r, {**base, "ok": False, "msg": "not_for_this_node"})
        return

    if NODE_ID != "rt-radio":
        publish_bus(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    if not isinstance(freq_hz, int):
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:invalid_freq_hz"})
        return

    if freq_hz < 1_000_000 or freq_hz > 60_000_000:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:freq_out_of_range"})
        return

    if not band:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:missing_band"})
        return

    if not mode:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:missing_mode"})
        return

    # MVP behavior:
    # Accept the request and acknowledge receipt on rt-radio.
    # Real CAT/radio execution will be added later.
    publish_bus(r, {**base, "ok": True, "msg": "accepted_not_implemented"})

def handle_pota_select_band(r: redis.Redis, params: Dict[str, Any]) -> None:
    band = str(params.get("band") or "").strip()

    base = {
        "topic": "ui.pota.select_band.result",
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "band": band,
        "context_key": POTA_CONTEXT_KEY,
    }

    if NODE_ID != "rt-controller":
        publish_bus(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    if not band:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:missing_band"})
        return

    if band not in POTA_BAND_ORDER:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:invalid_band"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))
    ctx["selected_band"] = band
    ctx["selection_ts"] = now_ms()

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_bus(r, {**base, "ok": True, "msg": "selected_band_updated"})

POTA_NEARBY_KEY = os.environ.get("RT_POTA_NEARBY_KEY", "rt:pota:nearby")

def nearby_choices_by_ref(r: redis.Redis) -> dict[str, dict[str, Any]]:
    nearby = load_json_object(r, POTA_NEARBY_KEY)
    if not isinstance(nearby, dict):
        return {}

    choices = nearby.get("choices")
    if not isinstance(choices, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for item in choices:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("reference", "") or "").strip()
        # allow synthetic empty ref row to exist in nearby, but do not index it
        if not ref:
            continue
        out[ref] = item
    return out

def handle_pota_select_park(r: redis.Redis, params: Dict[str, Any]) -> None:
    park_ref = str(params.get("park_ref") or params.get("reference") or "").strip()

    base = {
        "topic": "ui.pota.select_park.result",
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "park_ref": park_ref,
        "context_key": POTA_CONTEXT_KEY,
        "nearby_key": POTA_NEARBY_KEY,
    }

    if NODE_ID != "rt-controller":
        publish_bus(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))

    # Empty ref means "Not in a park" / clear all selections.
    if not park_ref:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"
        ctx["selected_park_refs"] = []
        ctx["selected_park_names"] = []
        ctx["left_selected_park_refs"] = []
        ctx["selection_ts"] = now_ms()

        r.set(POTA_CONTEXT_KEY, compact_json(ctx))
        publish_bus(r, {**base, "ok": True, "msg": "selected_park_cleared"})
        return

    nearby_map = nearby_choices_by_ref(r)
    choice = nearby_map.get(park_ref)
    if not choice:
        publish_bus(r, {**base, "ok": False, "msg": "bad_request:park_not_in_nearby_choices"})
        return

    park_name = str(choice.get("name") or "").strip()
    grid = str(choice.get("grid") or ctx.get("grid", "") or "").strip()

    selected_refs = list(ctx.get("selected_park_refs", []))
    selected_names = list(ctx.get("selected_park_names", []))

    # Build a stable name map from existing selections.
    name_by_ref: dict[str, str] = {}
    for i, ref in enumerate(selected_refs):
        ref_s = str(ref).strip()
        if not ref_s:
            continue
        if i < len(selected_names):
            nm = str(selected_names[i]).strip()
            if nm:
                name_by_ref[ref_s] = nm

    # Toggle membership.
    if park_ref in selected_refs:
        selected_refs = [ref for ref in selected_refs if ref != park_ref]
        name_by_ref.pop(park_ref, None)
        result_msg = "selected_park_removed"
    else:
        selected_refs.append(park_ref)
        if park_name:
            name_by_ref[park_ref] = park_name
        result_msg = "selected_park_added"

    # Rebuild aligned names in selected_refs order.
    rebuilt_names: list[str] = []
    for ref in selected_refs:
        nm = name_by_ref.get(ref) or str(nearby_map.get(ref, {}).get("name") or "").strip()
        if nm:
            rebuilt_names.append(nm)
        else:
            rebuilt_names.append("")

    # Drop parks from left_selected_park_refs if they are now actively selected.
    prior_left = [str(x).strip() for x in ctx.get("left_selected_park_refs", []) if str(x).strip()]
    left_selected = [ref for ref in prior_left if ref not in selected_refs]

    ctx["selected_park_refs"] = selected_refs
    ctx["selected_park_names"] = rebuilt_names
    ctx["left_selected_park_refs"] = left_selected
    ctx["grid"] = grid
    ctx["selection_ts"] = now_ms()

    # Backward-compatible singular fields.
    if selected_refs:
        ctx["selected_park_ref"] = selected_refs[0]
        first_name = rebuilt_names[0] if rebuilt_names else ""
        ctx["selected_park_name"] = first_name or ""
    else:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_bus(r, {
        **base,
        "ok": True,
        "msg": result_msg,
        "park_name": park_name,
        "selected_park_refs": selected_refs,
    })


def main() -> None:
    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_bus(
        r,
        {
            "topic": "ui.intent.worker.hello",
            "node": NODE_ID,
            "ts_ms": now_ms(),
            "intents_channel": INTENTS_CH,
            "capabilities": {
                "node_reboot": ALLOW_NODE_REBOOT,
                "radio_tune": NODE_ID == "rt-radio",
                "pota_select_band": NODE_ID == "rt-controller",
                "pota_select_park": NODE_ID == "rt-controller",
                "mode": REBOOT_MODE,
            },
        },
    )

    while True:
        msg = ps.get_message(timeout=1.0)
        if not msg or msg.get("type") != "message":
            time.sleep(0.05)
            continue

        raw = msg.get("data")
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            continue

        intent = str(obj.get("intent") or "").strip()
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}

        if intent == "node.reboot":
            handle_node_reboot(r, params)
            continue

        if intent == "pota.select_band":
            handle_pota_select_band(r, params)
            continue

        if intent == "radio.tune":
            handle_radio_tune(r, params)
            continue

        if intent == "pota.select_park":
            handle_pota_select_park(r, params)
            continue

if __name__ == "__main__":
    main()