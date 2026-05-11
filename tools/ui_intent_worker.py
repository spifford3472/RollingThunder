#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timezone

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
SYSTEM_BUS_CH = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")
LAST_RESULT_KEY = os.environ.get("RT_UI_LAST_RESULT_KEY", "rt:controller:ui:last_result")

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

# HF UI context keys in Redis, used by both the context manager and the UI intent worker.
HF_CONTEXT_KEY = os.environ.get("RT_HF_CONTEXT_KEY", "rt:hf:context")
HF_SPOTS_SELECTED_KEY = os.environ.get("RT_HF_SPOTS_SELECTED_KEY", "rt:hf:spots:selected")
HF_SELECTED_DETAIL_KEY = os.environ.get("RT_HF_SELECTED_DETAIL_KEY", "rt:hf:spots:selected_detail")

# POTA UI context key in Redis, used by both the context manager and the UI intent worker.
POTA_CONTEXT_KEY = os.environ.get("RT_POTA_CONTEXT_KEY", "rt:pota:context")

POTA_BAND_ORDER = {
    "160m", "80m", "60m", "40m", "30m",
    "20m", "17m", "15m", "12m", "10m",
    "6m",
}

RT_RADIO_SERVICES_DIR = Path("/opt/rollingthunder/nodes/rt-radio/services")

_radio_runtime: Dict[str, Any] | None = None
_radio_runtime_error: str | None = None


def _load_radio_runtime() -> Dict[str, Any]:
    """
    Lazy-load the rt-radio local radio package only when needed.
    This worker is shared across nodes, so we do not want import-time
    failures on nodes that never execute radio control.
    """
    global _radio_runtime, _radio_runtime_error

    if _radio_runtime is not None:
        return _radio_runtime

    if _radio_runtime_error is not None:
        raise RuntimeError(_radio_runtime_error)

    try:
        if str(RT_RADIO_SERVICES_DIR) not in sys.path:
            sys.path.insert(0, str(RT_RADIO_SERVICES_DIR))

        from radio import RadioService, load_radio_config
        from radio.hamlib_client import (
            HamlibError,
            RigctldCommandError,
            RigctldProtocolError,
            RigctldUnreachable,
        )
        from radio.radios.ft891 import RadioValidationError

        service = RadioService(load_radio_config())

        _radio_runtime = {
            "service": service,
            "HamlibError": HamlibError,
            "RigctldCommandError": RigctldCommandError,
            "RigctldProtocolError": RigctldProtocolError,
            "RigctldUnreachable": RigctldUnreachable,
            "RadioValidationError": RadioValidationError,
        }
        return _radio_runtime

    except Exception as e:
        _radio_runtime_error = f"{type(e).__name__}:{e}"
        raise


#=================================================================================
#   HF HELPERS                                                      
#=================================================================================                                                 
def _json_get(r, key, default=None):
    try:
        raw = r.get(key)
        if not raw:
            return default
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        return default


def _json_set(r, key, value):
    r.set(key, json.dumps(value, separators=(",", ":")))


def _hf_selected_spot(spots_model, selected_id=None):
    items = (spots_model or {}).get("items") or []
    if not items:
        return None

    if selected_id:
        for item in items:
            if item.get("id") == selected_id:
                return item

    return items[0]


def _hf_mode_for_freq(freq_hz, band=None):
    try:
        freq_hz = int(freq_hz)
    except Exception:
        return "USB"

    if band in {"160m", "80m", "60m", "40m"}:
        return "LSB"

    if freq_hz < 10000000:
        return "LSB"

    return "USB"


def _hf_publish_state_changed(r: redis.Redis, keys: list[str]) -> None:
    clean_keys = [str(k).strip() for k in keys if str(k).strip()]
    if not clean_keys:
        return

    ts = now_ms()

    r.publish(
        "rt:system:bus",
        json.dumps({
            "topic": "state.changed",
            "payload": {
                "keys": clean_keys,
            },
            "source": "ui_intent_worker:hf",
            "ts_ms": ts,
        })
    )


def _utc_date_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _hf_status_hash_key():
    return f"rt:hf:spot_status:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

def _hf_status_map(r: redis.Redis):
    key = _hf_status_hash_key()
    raw = r.hgetall(key) or {}
    out = {}
    for k, v in raw.items():
        try:
            out[k] = json.loads(v)
        except Exception:
            continue
    return out

def _hf_spot_key(spot, fallback_band=""):
    call = str(spot.get("callsign") or "").strip().upper()
    freq = int(spot.get("freq_hz") or 0)
    band = str(spot.get("band") or fallback_band or "").strip()
    return f"{call}|{freq}|{band}"


def _hf_apply_status(spots_model, status_map):
    items = spots_model.get("items") or []
    fallback_band = str(spots_model.get("band") or "").strip()

    for item in items:
        key = _hf_spot_key(item, fallback_band)
        status_obj = status_map.get(key) or {}
        status = status_obj.get("status")

        item["band"] = item.get("band") or fallback_band

        if status in {"worked", "heard", "cannot_hear"}:
            item["status"] = status
            item["row_style"] = status
        else:
            item["status"] = ""
            item["row_style"] = "default"
#=================================================================================
#   END OF HF PAGE ADDITIONS                                                      
#=================================================================================

def env_truthy(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def handle_ui_browse_delta(r: redis.Redis, params: Dict[str, Any]) -> None:
    delta = int(params.get("delta", 0))
    panel = str(params.get("panel") or "").strip()

    if delta == 0 or not panel:
        return

    key = f"rt:ui:browse:{panel}"
    raw = r.get(key)

    try:
        state = json.loads(raw) if raw else {}
    except Exception:
        state = {}

    # Initialize if needed
    if not state:
        state = {
            "active": True,
            "panel": panel,
            "selected_index": 0,
            "window_start": 0,
            "window_size": 7
        }

    selected = int(state.get("selected_index", 0))
    window_start = int(state.get("window_start", 0))
    window_size = int(state.get("window_size", 7))

    selected += 1 if delta > 0 else -1
    selected = max(0, selected)

    # Window tracking
    if selected < window_start:
        window_start = selected
    elif selected >= window_start + window_size:
        window_start = selected - window_size + 1

    state["selected_index"] = selected

    # --- NEW: auto tune on selection change ---
    try:
        # Get spots list for current band
        spots_key = f"rt:pota:ui:ssb:spots:selected"
        raw_spots = r.get(spots_key)
        spots = json.loads(raw_spots) if raw_spots else []

        if isinstance(spots, list) and 0 <= selected < len(spots):
            spot = spots[selected]

            freq_hz = int(spot.get("freq_hz", 0))
            mode = str(spot.get("mode", "SSB")).strip()
            band = str(spot.get("band", "")).strip()

            if freq_hz > 0:
                intent = {
                    "intent": "radio.tune",
                    "params": {
                        "freq_hz": freq_hz,
                        "mode": mode,
                        "band": band,
                        "autotune": False,
                        "nodeId": "rt-radio"
                    }
                }
                r.publish("rt:ui:intents", json.dumps(intent, separators=(",", ":")))

    except Exception:
        pass

    state["window_start"] = window_start
    state["window_size"] = window_size
    state["updated_at_ms"] = int(time.time() * 1000)

    r.set(key, json.dumps(state, separators=(",", ":")))

    publish_state_changed(r, [key], source="ui_intent_worker")

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


def publish_state_changed(r: redis.Redis, keys: list[str], source: str = "ui_intent_worker") -> None:
    evt = {
        "topic": "state.changed",
        "payload": {"keys": keys[:50]},
        "ts_ms": now_ms(),
        "source": source,
    }
    r.publish(SYSTEM_BUS_CH, json.dumps(evt, separators=(",", ":"), ensure_ascii=False))


def publish_last_result(r: redis.Redis, payload: Dict[str, Any]) -> None:
    obj = dict(payload)

    result = "ok" if bool(obj.get("ok")) else "error"
    reason = obj.get("msg") or obj.get("message") or obj.get("error") or obj.get("status")

    last_result = {
        "result": result,
        "intent": str(obj.get("topic") or ""),
        "reason": str(reason or ""),
        "execution_id": str(obj.get("ts_ms") or now_ms()),
        "page": "pota",
        "focused_panel": None,
        "ts_ms": int(obj.get("ts_ms") or now_ms()),
    }

    r.set(LAST_RESULT_KEY, json.dumps(last_result, separators=(",", ":")), px=5000)
    publish_state_changed(r, [LAST_RESULT_KEY], source="ui_intent_worker")


def _truthy(x: Any) -> bool:
    if x is True:
        return True
    if isinstance(x, str) and x.strip().lower() in ("1", "true", "yes", "y", "on"):
        return True
    if isinstance(x, (int, float)) and x == 1:
        return True
    return False


def _radio_result_base(params: Dict[str, Any]) -> Dict[str, Any]:
    target = str(params.get("nodeId") or params.get("node_id") or "rt-radio").strip()
    return {
        "topic": "ui.radio.tune.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
        "freq_hz": params.get("freq_hz"),
        "band": str(params.get("band") or "").strip(),
        "mode": str(params.get("mode") or "").strip(),
        "passband_hz": params.get("passband_hz"),
        "autotune": _truthy(params.get("autotune")),
    }


def _publish_radio_tune_ok(
    r: redis.Redis,
    base: Dict[str, Any],
    *,
    freq_hz: int,
    mode: str,
    passband_hz: int,
    autotune_requested: bool,
    autotune_attempted: bool,
    autotune_error: str | None = None,
) -> None:
    payload = {
        **base,
        "ok": True,
        "status": "ok",
        "msg": "tuned_successfully",
        "message": "tuned successfully",
        "freq_hz": freq_hz,
        "mode": mode,
        "passband_hz": passband_hz,
        "autotune_requested": autotune_requested,
        "autotune_attempted": autotune_attempted,
    }
    if autotune_error:
        payload["autotune_error"] = autotune_error

    publish_last_result(r, payload)


def _publish_radio_tune_error(
    r: redis.Redis,
    base: Dict[str, Any],
    *,
    error_code: str,
    message: str,
) -> None:
    publish_last_result(
        r,
        {
            **base,
            "ok": False,
            "status": "error",
            "error_code": error_code,
            "msg": message,
            "message": message,
        },
    )


def _publish_radio_atas_tune_result(
    r: redis.Redis,
    *,
    ok: bool,
    target: str,
    band: str,
    msg: str,
    error_code: str | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "topic": "ui.radio.atas_tune.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
        "band": band,
        "ok": ok,
        "msg": msg,
        "message": msg,
    }
    if ok:
        payload["status"] = "ok"
    else:
        payload["status"] = "error"
        if error_code:
            payload["error_code"] = error_code

    publish_last_result(r, payload)


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
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:missing_nodeId"})
        return

    if target != NODE_ID:
        publish_last_result(r, {**base, "ok": False, "msg": "not_for_this_node"})
        return

    if not ALLOW_NODE_REBOOT:
        publish_last_result(r, {**base, "ok": False, "msg": "reboot_disabled"})
        return

    if not confirm:
        publish_last_result(r, {**base, "ok": False, "msg": "not_confirmed"})
        return

    ok, msgtxt = reboot_this_node()
    publish_last_result(r, {**base, "ok": ok, "msg": msgtxt})


# --- ONLY SHOWING THE MODIFIED FUNCTION ---

def handle_radio_tune(r: redis.Redis, params: Dict[str, Any]) -> None:
    base = _radio_result_base(params)
    target = str(base["target"]).strip()

    if target != NODE_ID:
        _publish_radio_tune_error(r, base, error_code="not_for_this_node", message="not_for_this_node")
        return

    if NODE_ID != "rt-radio":
        _publish_radio_tune_error(r, base, error_code="wrong_node_role", message="wrong_node_role")
        return

    freq_raw = params.get("freq_hz")
    try:
        freq_hz = int(freq_raw)
    except Exception:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message="freq_hz is required and must be an integer",
        )
        return

    if freq_hz < 30_000 or freq_hz > 56_000_000:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message="freq_hz out of supported range",
        )
        return

    mode_raw = params.get("mode")
    mode = str(mode_raw).strip() if mode_raw is not None else None
    if mode == "":
        mode = None

    # ✅ NEW: extract band (safe, optional)
    band_raw = params.get("band")
    band = str(band_raw).strip() if band_raw is not None else None
    if band == "":
        band = None

    passband_raw = params.get("passband_hz")
    if passband_raw in (None, ""):
        passband_hz = None
    else:
        try:
            passband_hz = int(passband_raw)
        except Exception:
            _publish_radio_tune_error(
                r,
                base,
                error_code="invalid_payload",
                message="passband_hz must be an integer when provided",
            )
            return

    autotune = _truthy(params.get("autotune"))

    try:
        runtime = _load_radio_runtime()
    except Exception as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="radio_runtime_unavailable",
            message=f"radio runtime unavailable: {type(e).__name__}: {e}",
        )
        return

    service = runtime["service"]
    HamlibError = runtime["HamlibError"]
    RigctldCommandError = runtime["RigctldCommandError"]
    RigctldProtocolError = runtime["RigctldProtocolError"]
    RigctldUnreachable = runtime["RigctldUnreachable"]
    RadioValidationError = runtime["RadioValidationError"]

    try:
        # ✅ NEW: pass band into backend
        result = service.tune(
            freq_hz=freq_hz,
            mode=mode,
            passband_hz=passband_hz,
            autotune=autotune,
            band=band,  # <-- THIS is the key change
        )

        _publish_radio_tune_ok(
            r,
            base,
            freq_hz=int(result.freq_hz),
            mode=str(result.mode),
            passband_hz=int(result.passband_hz),
            autotune_requested=bool(result.autotune_requested),
            autotune_attempted=bool(result.autotune_attempted),
            autotune_error=result.autotune_error,
        )

    except RadioValidationError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message=str(e),
        )
    except RigctldUnreachable:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_unreachable",
            message="unable to contact rigctld",
        )
    except RigctldProtocolError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_protocol_error",
            message=str(e),
        )
    except RigctldCommandError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_command_error",
            message=f"rigctld rejected command: {e.code}",
        )
    except HamlibError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="radio_error",
            message=str(e),
        )
    except Exception as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="unexpected_error",
            message=f"{type(e).__name__}:{e}",
        )


def handle_radio_atas_tune(r: redis.Redis, params: Dict[str, Any]) -> None:
    target = str(params.get("nodeId") or params.get("node_id") or "rt-radio").strip()
    band = str(params.get("band") or "").strip()

    if target != NODE_ID:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="not_for_this_node",
            error_code="not_for_this_node",
        )
        return

    if NODE_ID != "rt-radio":
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="wrong_node_role",
            error_code="wrong_node_role",
        )
        return

    if not band:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="band is required",
            error_code="invalid_payload",
        )
        return

    try:
        runtime = _load_radio_runtime()
    except Exception as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"radio runtime unavailable: {type(e).__name__}: {e}",
            error_code="radio_runtime_unavailable",
        )
        return

    service = runtime["service"]
    HamlibError = runtime["HamlibError"]
    RigctldCommandError = runtime["RigctldCommandError"]
    RigctldProtocolError = runtime["RigctldProtocolError"]
    RigctldUnreachable = runtime["RigctldUnreachable"]
    RadioValidationError = runtime["RadioValidationError"]

    atas_fn = getattr(service, "atas_tune", None)
    if not callable(atas_fn):
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="atas_tune not supported by current radio service",
            error_code="not_supported",
        )
        return

    try:
        result = atas_fn(band=band)

        if isinstance(result, dict):
            completed = bool(result.get("completed", False))
            timed_out = bool(result.get("timed_out", False))
            msg = str(result.get("msg") or result.get("message") or "atas_tune_requested")

            payload_msg = msg
            if timed_out:
                _publish_radio_atas_tune_result(
                    r,
                    ok=False,
                    target=target,
                    band=band,
                    msg=payload_msg,
                    error_code="timeout",
                )
                return

            _publish_radio_atas_tune_result(
                r,
                ok=completed or bool(result.get("tuner_started", False)),
                target=target,
                band=band,
                msg=payload_msg,
            )
            return

        _publish_radio_atas_tune_result(
            r,
            ok=True,
            target=target,
            band=band,
            msg="atas_tune_requested",
        )

    except RadioValidationError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="invalid_payload",
        )
    except RigctldUnreachable:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="unable to contact rigctld",
            error_code="rigctld_unreachable",
        )
    except RigctldProtocolError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="rigctld_protocol_error",
        )
    except RigctldCommandError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"rigctld rejected command: {e.code}",
            error_code="rigctld_command_error",
        )
    except HamlibError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="radio_error",
        )
    except Exception as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"{type(e).__name__}:{e}",
            error_code="unexpected_error",
        )


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
        publish_last_result(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    if not band:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:missing_band"})
        return

    if band not in POTA_BAND_ORDER:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:invalid_band"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))
    ctx["selected_band"] = band
    ctx["selection_ts"] = now_ms()

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_last_result(r, {**base, "ok": True, "msg": "selected_band_updated"})


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
        publish_last_result(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))

    if not park_ref:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"
        ctx["selected_park_refs"] = []
        ctx["selected_park_names"] = []
        ctx["left_selected_park_refs"] = []
        ctx["selection_ts"] = now_ms()

        r.set(POTA_CONTEXT_KEY, compact_json(ctx))
        publish_last_result(r, {**base, "ok": True, "msg": "selected_park_cleared"})
        return

    nearby_map = nearby_choices_by_ref(r)
    choice = nearby_map.get(park_ref)
    if not choice:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:park_not_in_nearby_choices"})
        return

    park_name = str(choice.get("name") or "").strip()
    grid = str(choice.get("grid") or ctx.get("grid", "") or "").strip()

    selected_refs = list(ctx.get("selected_park_refs", []))
    selected_names = list(ctx.get("selected_park_names", []))

    name_by_ref: dict[str, str] = {}
    for i, ref in enumerate(selected_refs):
        ref_s = str(ref).strip()
        if not ref_s:
            continue
        if i < len(selected_names):
            nm = str(selected_names[i]).strip()
            if nm:
                name_by_ref[ref_s] = nm

    if park_ref in selected_refs:
        selected_refs = [ref for ref in selected_refs if ref != park_ref]
        name_by_ref.pop(park_ref, None)
        result_msg = "selected_park_removed"
    else:
        selected_refs.append(park_ref)
        if park_name:
            name_by_ref[park_ref] = park_name
        result_msg = "selected_park_added"

    rebuilt_names: list[str] = []
    for ref in selected_refs:
        nm = name_by_ref.get(ref) or str(nearby_map.get(ref, {}).get("name") or "").strip()
        if nm:
            rebuilt_names.append(nm)
        else:
            rebuilt_names.append("")

    prior_left = [str(x).strip() for x in ctx.get("left_selected_park_refs", []) if str(x).strip()]
    left_selected = [ref for ref in prior_left if ref not in selected_refs]

    ctx["selected_park_refs"] = selected_refs
    ctx["selected_park_names"] = rebuilt_names
    ctx["left_selected_park_refs"] = left_selected
    ctx["grid"] = grid
    ctx["selection_ts"] = now_ms()

    if selected_refs:
        ctx["selected_park_ref"] = selected_refs[0]
        first_name = rebuilt_names[0] if rebuilt_names else ""
        ctx["selected_park_name"] = first_name or ""
    else:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_last_result(r, {
        **base,
        "ok": True,
        "msg": result_msg,
        "park_name": park_name,
        "selected_park_refs": selected_refs,
    })

#=================================================================================
# HF HANDLEERS and PROCEDURES
#=================================================================================
def handle_hf_spot_outcome(r: redis.Redis, params: Dict[str, Any]) -> None:
    status = str(params.get("status") or "").strip()

    if status not in {"worked", "heard", "cannot_hear"}:
        return

    # --- enrich from selected detail if missing ---
    detail = _json_get(r, HF_SELECTED_DETAIL_KEY, {}) or {}

    callsign = params.get("callsign") or detail.get("callsign") or ""
    freq_hz = int(params.get("freq_hz") or detail.get("freq_hz") or 0)
    band = params.get("band") or detail.get("band") or ""
    mode = params.get("mode") or detail.get("mode") or _hf_mode_for_freq(freq_hz, band)

    ts_utc = params.get("timestamp_utc") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    date_key = _utc_date_str()
    redis_key = f"rt:hf:spot_status:{date_key}"

    spot_key = f"{callsign}|{freq_hz}|{band}"

    value = {
        "status": status,
        "callsign": callsign,
        "freq_hz": freq_hz,
        "band": band,
        "mode": mode,
        "timestamp_utc": ts_utc,
        "source": "hf"
    }

    r.hset(redis_key, spot_key, json.dumps(value, separators=(",", ":")))

    # --- publish state change ---
    _hf_publish_state_changed(r, [redis_key])

    # --- only log contacts when worked ---
    if status == "worked":
        intent = {
            "intent": "radio.log_qso",
            "params": {
                "call": callsign,
                "band": band,
                "freq_hz": freq_hz,
                "mode": mode,
                "timestamp_utc": ts_utc,
                "comment": "HF contact",
                "source": "hf"
            },
            "source": {
                "type": "hf",
                "node": NODE_ID
            },
            "timestamp": int(time.time() * 1000)
        }

        r.publish(INTENTS_CH, json.dumps(intent, separators=(",", ":")))

#=================================================================================
# END OF HF PROCEDURES
#================================================================================

def main() -> None:
    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_last_result(
        r,
        {
            "topic": "ui.intent.worker.hello",
            "node": NODE_ID,
            "ts_ms": now_ms(),
            "intents_channel": INTENTS_CH,
            "capabilities": {
                "node_reboot": ALLOW_NODE_REBOOT,
                "radio_tune": NODE_ID == "rt-radio",
                "radio_atas_tune": NODE_ID == "rt-radio",
                "radio_tune_backend": NODE_ID == "rt-radio",
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

        if intent == "ui.browse.delta":
            handle_ui_browse_delta(r, params)
            continue

#=================================================================================
# Radio intents
#=================================================================================
        if intent == "radio.tune":
            if NODE_ID == "rt-radio":
                handle_radio_tune(r, params)
            continue

#=================================================================================
# POTA intents
#=================================================================================        
        if intent == "pota.select_band":
            handle_pota_select_band(r, params)
            continue

        if intent == "radio.atas_tune":
            handle_radio_atas_tune(r, params)
            continue

        if intent == "pota.select_park":
            handle_pota_select_park(r, params)
            continue

#=================================================================================
# HF intents
#=================================================================================
        if intent == "hf.select_band":

            params = obj.get("params") or {}
            band = params.get("band") or params.get("id") or params.get("selected_id")

            if not band:
                continue

            HF_CONTEXT_KEY = os.environ.get("RT_HF_CONTEXT_KEY", "rt:hf:context")

            ctx = load_json_object(r, HF_CONTEXT_KEY) or {}
            ctx["selected_band"] = band
            ctx["selection_ts"] = now_ms()

            r.set(HF_CONTEXT_KEY, compact_json(ctx))

            # Load spots
            spots_key = f"rt:hf:spots:{band}"
            spots_model = _json_get(r, spots_key)

            if not spots_model:
                spots_model = _json_get(r, "rt:hf:spots:selected", {}) or {}

            spots_model["band"] = band

            status_map = _hf_status_map(r)
            _hf_apply_status(spots_model, status_map)
            items = spots_model.get("items") or []
            selected = items[0] if items else None

            if not selected:
                continue

            selected_id = selected.get("id")

            ctx["selected_spot_id"] = selected_id
            r.set(HF_CONTEXT_KEY, compact_json(ctx))

            spots_model["selected_id"] = selected_id
            spots_model["band"] = band

            detail = dict(selected)
            detail["band"] = band
            detail["mode"] = detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band)

            _json_set(r, "rt:hf:spots:selected", spots_model)
            _json_set(r, "rt:hf:spots:selected_detail", detail)

            _hf_publish_state_changed(r, [
                "rt:hf:context",
                "rt:hf:spots:selected",
                "rt:hf:spots:selected_detail",
            ])

            freq = selected.get("freq_hz")

            if freq:

                r.publish("rt:ui:intents", json.dumps({
                    "intent": "radio.tune",
                    "params": {
                        "freq_hz": int(freq),
                        "band": band,
                        "mode": detail["mode"],
                        "source": "hf",
                        "spot_id": selected_id,
                        "callsign": selected.get("callsign"),
                        "nodeId": "rt-radio"
                    },
                    "source": {
                        "type": "ui_intent_worker",
                        "node": "rt-controller"
                    },
                    "timestamp": now_ms()
                }))

            continue

        if intent == "hf.select_spot":
            params = obj.get("params") or {}

            selected_id = (
                params.get("spot_id")
                or params.get("id")
                or params.get("selected_id")
            )

            context = _json_get(r, "rt:hf:context", {}) or {}
            band = params.get("band") or context.get("selected_band")

            spots_model = _json_get(r, "rt:hf:spots:selected", {}) or {}

            if band:
                spots_model["band"] = band

            status_map = _hf_status_map(r)
            _hf_apply_status(spots_model, status_map)

            selected = _hf_selected_spot(spots_model, selected_id)

            if not selected:
                return

            selected_id = selected.get("id")
            band = band or spots_model.get("band") or selected.get("band")

            context["selected_band"] = band
            context["selected_spot_id"] = selected_id
            context["selection_ts"] = int(time.time() * 1000)

            spots_model["selected_id"] = selected_id

            detail = dict(selected)
            detail["band"] = band
            detail["mode"] = detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band)

            _json_set(r, "rt:hf:context", context)
            _json_set(r, "rt:hf:spots:selected", spots_model)
            _json_set(r, "rt:hf:spots:selected_detail", detail)

            _hf_publish_state_changed(r, [
                "rt:hf:context",
                "rt:hf:spots:selected",
                "rt:hf:spots:selected_detail",
            ])

            if detail.get("freq_hz"):
                tune_payload = {
                    "intent": "radio.tune",
                    "params": {
                        "freq_hz": int(detail["freq_hz"]),
                        "band": band,
                        "mode": detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band),
                        "source": "hf",
                        "spot_id": selected_id,
                        "callsign": detail.get("callsign"),
                        "nodeId": "rt-radio",
                    },
                    "source": {
                        "type": "ui_interaction_state",
                        "node": "rt-controller",
                    },
                    "timestamp": int(time.time() * 1000),
                }
                r.publish("rt:ui:intents", json.dumps(tune_payload, separators=(",", ":")))

            return   

        elif intent == "hf.spot.outcome":
            handle_hf_spot_outcome(r, params)                 

if __name__ == "__main__":
    main()