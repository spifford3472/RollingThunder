#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redis
import serial
from redis.exceptions import RedisError
from serial import SerialException

# -----------------------------------------------------------------------------
# RollingThunder console LED sender - controller-owned semantic LED mapping
#
# Phase B scope:
# - stable console serial device by-id
# - reconnect-safe serial lifecycle
# - reset_leds + full snapshot on connect
# - controller-owned LED semantics derived from projected controller state
# - resend full snapshot only when the effective snapshot changes
#
# Non-negotiable architecture alignment:
# - Redis is the source of truth
# - controller owns all state and LED meaning
# - UI is renderer-only
# - console hardware remains a dumb sink
# - existing serial LED contract remains the transport contract
#
# Semantic LED modes used internally here:
# - off
# - on
# - blink_slow
# - blink_fast
# - pulse
#
# These are translated onto the existing transport contract:
# - off       -> {mode:off}
# - on        -> {mode:on}
# - blink_*   -> {mode:blink,period_ms:...}
# - pulse     -> {mode:pulse,period_ms:900}
# -----------------------------------------------------------------------------

CONSOLE_PORT = (
    "/dev/serial/by-id/"
    "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
)

BAUD = 115200
OPEN_SETTLE_SEC = 1.5
POST_RESET_DELAY_SEC = 0.20
POST_SNAPSHOT_DELAY_SEC = 0.20
RETRY_SEC = 2.0
POLL_SEC = 0.50

KEY_UI_PAGE = "rt:ui:page"
KEY_UI_FOCUS = "rt:ui:focus"
KEY_UI_LAYER = "rt:ui:layer"
KEY_UI_AUTHORITY = "rt:ui:authority"
KEY_UI_MODAL = "rt:ui:modal"
KEY_UI_BROWSE = "rt:ui:browse"
KEY_UI_LAST_RESULT = "rt:ui:last_result"

BLINK_SLOW_MS = 900
BLINK_FAST_MS = 400
PULSE_MS = 900

RESET_LEDS = {
    "schema": 1,
    "type": "led",
    "cmd": "reset_leds",
}


def build_show_push(button: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "type": "led",
        "cmd": "show_push",
        "button": button,
    }

CONTROL_NAMES = ("back", "page", "primary", "cancel", "mode", "info")
CONFIG_PAGES_DIR = Path(os.environ.get("RT_PAGES_PATH", "/opt/rollingthunder/config/pages"))
CONFIG_PANELS_DIR = Path(os.environ.get("RT_PANELS_PATH", "/opt/rollingthunder/config/panels"))

_running = True


# -----------------------------------------------------------------------------
# Signal handling / logging
# -----------------------------------------------------------------------------
def _handle_signal(signum: int, frame: Any) -> None:
    global _running
    _running = False


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# -----------------------------------------------------------------------------
# Env / config helpers
# -----------------------------------------------------------------------------
def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    redis_host: str = env_str("RT_REDIS_HOST", "127.0.0.1")
    redis_port: int = env_int("RT_REDIS_PORT", 6379)
    redis_password: str = env_str("RT_REDIS_PASSWORD", "")
    redis_db: int = env_int("RT_REDIS_DB", 0)


class RedisManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client: redis.Redis | None = None

    def connect(self) -> redis.Redis:
        if self.client is not None:
            try:
                self.client.ping()
                return self.client
            except RedisError:
                self.client = None

        while _running:
            try:
                self.client = redis.Redis(
                    host=self.cfg.redis_host,
                    port=self.cfg.redis_port,
                    password=(self.cfg.redis_password or None),
                    db=self.cfg.redis_db,
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    health_check_interval=15,
                )
                self.client.ping()
                log(
                    f"redis connected host={self.cfg.redis_host} "
                    f"port={self.cfg.redis_port} db={self.cfg.redis_db}"
                )
                return self.client
            except RedisError as exc:
                log_err(f"redis connect failed: {type(exc).__name__}: {exc}")
                time.sleep(RETRY_SEC)

        raise RuntimeError("shutdown requested")

    def get(self) -> redis.Redis:
        return self.connect()


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------
def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "1", "true", "yes", "y", "on", "open", "active", "enabled"
        }
    if isinstance(value, dict):
        if not value:
            return False
        for key in ("active", "open", "visible", "present", "ok", "value"):
            if key in value and _truthy(value[key]):
                return True
        return True
    return bool(value)


def _jsonish_load(value: str | None) -> Any:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[") or s.startswith('"')):
        return s
    try:
        return json.loads(s)
    except Exception:
        return s


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# -----------------------------------------------------------------------------
# Semantic LED helpers
# -----------------------------------------------------------------------------
def semantic_off() -> str:
    return "off"


def semantic_on() -> str:
    return "on"


def semantic_blink_slow() -> str:
    return "blink_slow"


def semantic_blink_fast() -> str:
    return "blink_fast"


def semantic_pulse() -> str:
    return "pulse"


def semantic_to_transport(mode: str) -> dict[str, Any]:
    if mode == "off":
        return {"mode": "off"}
    if mode == "on":
        return {"mode": "on"}
    if mode == "blink_slow":
        return {"mode": "blink", "period_ms": BLINK_SLOW_MS}
    if mode == "blink_fast":
        return {"mode": "blink", "period_ms": BLINK_FAST_MS}
    if mode == "pulse":
        return {"mode": "pulse", "period_ms": PULSE_MS}
    return {"mode": "off"}



# -----------------------------------------------------------------------------
# Page order / breadcrumb helpers
# -----------------------------------------------------------------------------
def load_page_ids() -> list[str]:
    pages: list[tuple[int, str]] = []
    try:
        if not CONFIG_PAGES_DIR.exists():
            return []
        for f in CONFIG_PAGES_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            page_id = _string_or_none(data.get("id"))
            if not page_id:
                continue
            order = _coerce_int(data.get("order"), 9999)
            pages.append((order, page_id))
    except Exception:
        return []

    pages.sort(key=lambda item: (item[0], item[1]))
    seen: set[str] = set()
    ids: list[str] = []
    for _order, page_id in pages:
        if page_id in seen:
            continue
        seen.add(page_id)
        ids.append(page_id)
    return ids


def _return_button_for_transition(
    page_ids: list[str],
    previous_page: str | None,
    current_page: str | None,
) -> str | None:
    if not previous_page or not current_page:
        return None
    if previous_page == current_page:
        return None
    if previous_page not in page_ids or current_page not in page_ids:
        return None

    count = len(page_ids)
    if count <= 1:
        return None

    prev_idx = page_ids.index(previous_page)
    cur_idx = page_ids.index(current_page)

    if page_ids[(prev_idx + 1) % count] == current_page:
        return "back"
    if page_ids[(prev_idx - 1) % count] == current_page:
        return "page"
    return None


def update_breadcrumb_state(
    breadcrumb: dict[str, Any],
    page_ids: list[str],
    current_page: str | None,
) -> dict[str, Any]:
    if not isinstance(breadcrumb, dict):
        breadcrumb = {}

    last_page = _string_or_none(breadcrumb.get("last_page"))
    return_button = _string_or_none(breadcrumb.get("return_button"))

    if current_page != last_page:
        return_button = _return_button_for_transition(page_ids, last_page, current_page)
        breadcrumb = {
            "last_page": current_page,
            "return_button": return_button,
        }
    else:
        breadcrumb = {
            "last_page": last_page,
            "return_button": return_button,
        }

    return breadcrumb


# -----------------------------------------------------------------------------
# Serial helpers
# -----------------------------------------------------------------------------
def encode_line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def send_line(ser: serial.Serial, obj: dict[str, Any]) -> None:
    payload = encode_line(obj)
    log(f"TX: {payload.decode('utf-8').strip()}")
    ser.write(payload)
    ser.flush()


def open_console() -> serial.Serial:
    log(f"opening {CONSOLE_PORT} at {BAUD}")
    ser = serial.Serial(
        CONSOLE_PORT,
        BAUD,
        timeout=0.25,
        write_timeout=1.0,
        dsrdtr=False,
        rtscts=False,
    )
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(OPEN_SETTLE_SEC)
    return ser


def close_quietly(ser: serial.Serial | None) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Redis state reads
# -----------------------------------------------------------------------------
def redis_get_obj(r: redis.Redis, key: str) -> Any:
    try:
        key_type = r.type(key)
    except Exception:
        return None

    if key_type in ("none", None):
        return None

    try:
        if key_type == "string":
            raw = r.get(key)
            return _jsonish_load(raw)

        if key_type == "hash":
            return r.hgetall(key)

        return None
    except RedisError:
        return None


def _page_navigation_available(page: str | None) -> bool:
    return bool(page)


def _back_available(page: str | None, modal_active: bool, browse_active: bool) -> bool:
    if modal_active or browse_active:
        return True
    return bool(page and page != "home")


def _focus_navigation_available(focus: str | None, modal_active: bool) -> bool:
    if modal_active:
        return False
    return bool(focus)


def load_browsable_panel_ids() -> set[str]:
    browsable: set[str] = set()
    try:
        if not CONFIG_PANELS_DIR.exists():
            return browsable

        for f in CONFIG_PANELS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            panel_id = _string_or_none(data.get("id"))
            if not panel_id:
                continue

            interaction = _as_dict(data.get("interaction"))
            if bool(interaction.get("browsable", False)):
                browsable.add(panel_id)
    except Exception:
        return set()

    return browsable


def _browse_capable_focus(page: str | None, focus: str | None, browsable_panel_ids: set[str] | None = None) -> bool:
    _ = _string_or_none(page)
    focus = _string_or_none(focus)
    if not focus:
        return False
    if not isinstance(browsable_panel_ids, set):
        return False
    return focus in browsable_panel_ids


def _has_browse_selection(browse_obj: dict[str, Any]) -> bool:
    return _coerce_int(browse_obj.get("count"), 0) > 0


def _is_destructive_modal(modal_obj: dict[str, Any]) -> bool:
    return bool(modal_obj.get("destructive", False))


def _modal_confirmable(modal_obj: dict[str, Any]) -> bool:
    return bool(modal_obj.get("confirmable", False))


def _modal_cancelable(modal_obj: dict[str, Any]) -> bool:
    return bool(modal_obj.get("cancelable", False))


def _destructive_modal_armed(modal_obj: dict[str, Any]) -> bool:
    step = str(modal_obj.get("step") or "").strip().lower()
    return step == "armed"


def _has_recent_result(last_result_obj: Any) -> bool:
    if last_result_obj is None:
        return False
    if isinstance(last_result_obj, str):
        return bool(last_result_obj.strip())
    if isinstance(last_result_obj, dict):
        return bool(last_result_obj)
    return True


def read_controller_led_inputs(r: redis.Redis) -> dict[str, Any]:
    page = _string_or_none(redis_get_obj(r, KEY_UI_PAGE))
    focus = _string_or_none(redis_get_obj(r, KEY_UI_FOCUS))
    layer = _string_or_none(redis_get_obj(r, KEY_UI_LAYER)) or "default"
    authority_obj = _as_dict(redis_get_obj(r, KEY_UI_AUTHORITY))
    modal_obj = _as_dict(redis_get_obj(r, KEY_UI_MODAL))
    browse_obj = _as_dict(redis_get_obj(r, KEY_UI_BROWSE))
    last_result_obj = redis_get_obj(r, KEY_UI_LAST_RESULT)

    degraded = bool(authority_obj.get("degraded"))
    stale = bool(authority_obj.get("stale"))
    controller_authoritative = bool(authority_obj.get("controller_authoritative"))

    modal_active = _truthy(modal_obj)
    browse_active = _truthy(browse_obj)
    recent_result = _has_recent_result(last_result_obj)
    last_result = _as_dict(last_result_obj)

    log(
        f"UI inputs page={page!r} focus={focus!r} layer={layer!r} "
        f"degraded={degraded} stale={stale} controller_authoritative={controller_authoritative} "
        f"modal={modal_active} browse={browse_active} recent_result={recent_result}"
    )

    return {
        "page": page,
        "focus": focus,
        "layer": layer,
        "authority": authority_obj,
        "degraded": degraded,
        "stale": stale,
        "controller_authoritative": controller_authoritative,
        "modal": modal_obj,
        "modal_active": modal_active,
        "browse": browse_obj,
        "browse_active": browse_active,
        "last_result": last_result,
        "recent_result": recent_result,
    }


# -----------------------------------------------------------------------------
# LED derivation
# -----------------------------------------------------------------------------
def derive_semantic_leds(
    inputs: dict[str, Any],
    breadcrumb: dict[str, Any] | None = None,
    browsable_panel_ids: set[str] | None = None,
) -> dict[str, str]:
    page = inputs["page"]
    focus = inputs["focus"]
    layer = inputs["layer"]
    degraded = bool(inputs["degraded"])
    stale = bool(inputs["stale"])
    controller_authoritative = bool(inputs["controller_authoritative"])
    modal_obj = _as_dict(inputs["modal"])
    modal_active = bool(inputs["modal_active"])
    browse_obj = _as_dict(inputs["browse"])
    browse_active = bool(inputs["browse_active"])
    recent_result = bool(inputs["recent_result"])

    leds: dict[str, str] = {name: semantic_off() for name in CONTROL_NAMES}

    # Base/default availability layer.
    return_button = _string_or_none(_as_dict(breadcrumb).get("return_button"))

    if _page_navigation_available(page):
        leds["page"] = semantic_on()

    if _back_available(page, modal_active, browse_active):
        leds["back"] = semantic_on()

    if _focus_navigation_available(focus, modal_active) and _browse_capable_focus(page, focus, browsable_panel_ids):
        leds["mode"] = semantic_pulse()

    # Primary remains dark by default. It only lights when OK has an actual
    # current role, such as an actionable browse selection or a confirmable modal.
    leds["primary"] = semantic_off()

    if recent_result and not (degraded or stale):
        leds["info"] = semantic_pulse()

    # Default-layer breadcrumb cue:
    # the button that returns to the most recently departed page pulses.
    if not modal_active and not browse_active and not (degraded or stale or not controller_authoritative):
        if return_button == "back" and leds["back"] != semantic_off():
            leds["back"] = semantic_pulse()
        elif return_button == "page" and leds["page"] != semantic_off():
            leds["page"] = semantic_pulse()

    # Browse overrides default.
    if browse_active or layer == "browse":
        leds["mode"] = semantic_on()
        leds["back"] = semantic_on()
        leds["cancel"] = semantic_on()
        if _has_browse_selection(browse_obj):
            leds["primary"] = semantic_on()
        else:
            leds["primary"] = semantic_off()

    # Modal overrides browse/default.
    if modal_active or layer == "modal":
        leds["mode"] = semantic_off()
        leds["back"] = semantic_on()

        if _modal_cancelable(modal_obj):
            leds["cancel"] = semantic_on()
        else:
            leds["cancel"] = semantic_off()

        if _modal_confirmable(modal_obj):
            if _is_destructive_modal(modal_obj):
                if _destructive_modal_armed(modal_obj):
                    leds["primary"] = semantic_blink_fast()
                    if _modal_cancelable(modal_obj):
                        leds["cancel"] = semantic_blink_slow()
                else:
                    leds["primary"] = semantic_blink_slow()
                    if _modal_cancelable(modal_obj):
                        leds["cancel"] = semantic_on()
            else:
                leds["primary"] = semantic_blink_slow()
        else:
            leds["primary"] = semantic_off()

    # Degraded/fault posture has highest priority.
    if degraded or stale or not controller_authoritative or layer == "degraded":
        leds["info"] = semantic_blink_slow()
        leds["primary"] = semantic_off()
        leds["mode"] = semantic_off()

        if modal_active and _modal_confirmable(modal_obj):
            if _is_destructive_modal(modal_obj):
                leds["primary"] = (
                    semantic_blink_fast() if _destructive_modal_armed(modal_obj) else semantic_blink_slow()
                )
            else:
                leds["primary"] = semantic_blink_slow()

        if _page_navigation_available(page):
            leds["page"] = semantic_pulse()

        if _back_available(page, modal_active, browse_active):
            leds["back"] = semantic_pulse()

        if _modal_cancelable(modal_obj) or browse_active or (page and page != "home"):
            leds["cancel"] = semantic_pulse() if not modal_active else leds["cancel"]

    return leds


def derive_snapshot_from_inputs(
    inputs: dict[str, Any],
    breadcrumb: dict[str, Any] | None = None,
    browsable_panel_ids: set[str] | None = None,
) -> dict[str, Any]:
    semantic_leds = derive_semantic_leds(inputs, breadcrumb, browsable_panel_ids)
    leds = {name: semantic_to_transport(mode) for name, mode in semantic_leds.items()}

    log(
        "LED semantics "
        + " ".join(f"{name}={mode}" for name, mode in sorted(semantic_leds.items()))
    )

    return {
        "schema": 1,
        "type": "led",
        "cmd": "snapshot",
        "leds": leds,
    }


def _last_result_token(last_result_obj: dict[str, Any]) -> str | None:
    if not isinstance(last_result_obj, dict) or not last_result_obj:
        return None

    execution_id = _string_or_none(last_result_obj.get("execution_id"))
    if execution_id:
        return execution_id

    ts_ms = _string_or_none(last_result_obj.get("ts_ms"))
    intent = _string_or_none(last_result_obj.get("intent"))
    result = _string_or_none(last_result_obj.get("result"))
    if ts_ms and intent and result:
        return f"{ts_ms}:{intent}:{result}"
    return None


def _result_is_positive(last_result_obj: dict[str, Any]) -> bool:
    result = str(last_result_obj.get("result") or "").strip().lower()
    if not result:
        return False

    negative = {
        "rejected", "error", "failed", "denied", "ignored",
        "invalid", "timeout", "unavailable", "blocked",
    }
    return result not in negative


def _show_push_button_for_result(last_result_obj: dict[str, Any]) -> str | None:
    if not _result_is_positive(last_result_obj):
        return None

    intent = str(last_result_obj.get("intent") or "").strip().lower()
    return {
        "ui.ok": "primary",
        "ui.cancel": "cancel",
        "ui.back": "back",
        "ui.page.next": "page",
        "ui.focus.next": "mode",
        "ui.focus.prev": "info",
    }.get(intent)


def send_show_push_if_needed(
    ser: serial.Serial,
    inputs: dict[str, Any],
    last_push_token: str | None,
) -> str | None:
    last_result_obj = _as_dict(inputs.get("last_result"))
    token = _last_result_token(last_result_obj)
    if not token or token == last_push_token:
        return last_push_token

    button = _show_push_button_for_result(last_result_obj)
    if not button:
        return token

    send_line(ser, build_show_push(button))
    log(f"show_push sent button={button} intent={last_result_obj.get('intent')!r} result={last_result_obj.get('result')!r}")
    return token


def snapshots_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None or b is None:
        return a == b
    return json.dumps(a, sort_keys=True, separators=(",", ":")) == json.dumps(
        b, sort_keys=True, separators=(",", ":")
    )


# -----------------------------------------------------------------------------
# Sync lifecycle
# -----------------------------------------------------------------------------
def sync_console(ser: serial.Serial, snapshot: dict[str, Any]) -> None:
    send_line(ser, RESET_LEDS)
    time.sleep(POST_RESET_DELAY_SEC)

    send_line(ser, snapshot)
    time.sleep(POST_SNAPSHOT_DELAY_SEC)

    log("synced: reset_leds + snapshot")


def send_snapshot_if_changed(
    ser: serial.Serial,
    snapshot: dict[str, Any],
    last_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if snapshots_equal(snapshot, last_snapshot):
        return last_snapshot or snapshot

    send_line(ser, snapshot)
    log("updated snapshot")
    return snapshot


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = Config()
    redis_mgr = RedisManager(cfg)

    ser: serial.Serial | None = None
    r: redis.Redis | None = None
    last_snapshot: dict[str, Any] | None = None
    page_ids = load_page_ids()
    browsable_panel_ids = load_browsable_panel_ids()
    breadcrumb_state: dict[str, Any] = {"last_page": None, "return_button": None}
    last_push_token: str | None = None
    log(f"loaded page ids for breadcrumb semantics: {page_ids!r}")
    log(f"loaded browsable panel ids for mode semantics: {sorted(browsable_panel_ids)!r}")

    while _running:
        if r is None:
            try:
                r = redis_mgr.get()
            except Exception as e:
                log_err(f"redis connect failed: {type(e).__name__}: {e}")
                time.sleep(RETRY_SEC)
                continue

        if ser is None:
            try:
                inputs = read_controller_led_inputs(r)
                breadcrumb_state = update_breadcrumb_state(breadcrumb_state, page_ids, inputs.get("page"))
                snapshot = derive_snapshot_from_inputs(inputs, breadcrumb_state, browsable_panel_ids)
                last_push_token = _last_result_token(_as_dict(inputs.get("last_result")))

                ser = open_console()
                log("console connected")
                sync_console(ser, snapshot)
                last_snapshot = snapshot
            except Exception as e:
                log_err(f"console open/sync failed: {type(e).__name__}: {e}")
                close_quietly(ser)
                ser = None
                time.sleep(RETRY_SEC)
                continue

        try:
            if not ser.is_open:
                raise SerialException("serial port closed")

            inputs = read_controller_led_inputs(r)
            breadcrumb_state = update_breadcrumb_state(breadcrumb_state, page_ids, inputs.get("page"))
            snapshot = derive_snapshot_from_inputs(inputs, breadcrumb_state, browsable_panel_ids)
            last_snapshot = send_snapshot_if_changed(ser, snapshot, last_snapshot)
            last_push_token = send_show_push_if_needed(ser, inputs, last_push_token)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            break
        except RedisError as e:
            log_err(f"redis error: {type(e).__name__}: {e}")
            redis_mgr.client = None
            r = None
            time.sleep(RETRY_SEC)
        except Exception as e:
            log_err(f"connection lost: {type(e).__name__}: {e}")
            close_quietly(ser)
            ser = None
            last_snapshot = None
            last_push_token = None
            time.sleep(RETRY_SEC)

    close_quietly(ser)
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
