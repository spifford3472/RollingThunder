#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

import redis
import serial
from redis.exceptions import RedisError
from serial import SerialException

# -----------------------------------------------------------------------------
# RollingThunder console LED sender - Redis-backed stage
#
# Stage scope:
# - stable console serial device by-id
# - reconnect-safe serial lifecycle
# - reset_leds + full snapshot on connect
# - derive conservative LED meaning from Redis-backed controller truth
# - resend full snapshot only when the effective snapshot changes
#
# Intentionally NOT implemented yet:
# - incremental set updates
# - show_push integration
# - bus-triggered updates (this version polls)
# - page-specific deep capability derivation
#
# Assumptions (explicit, conservative):
# - Redis is authoritative for controller truth
# - current_page: rt:ui:current_page
# - focused_panel: rt:ui:focused_panel
# - modal: rt:ui:modal
# - browse flag: rt:ui:browse:<focused_panel> when focused_panel is present
# - system health: rt:system:health if present
# - recent result: rt:input:last_result if present
# - service health fallback hashes may exist under rt:services:<id>
#
# Conservative first-pass meanings:
# - page: pulse when controller is authoritative and a page exists
# - primary: on when controller is authoritative and not degraded
# - cancel: on when modal is active; blink when degraded
# - mode: on when browse is active
# - back: on when modal or browse is active
# - info: on when recent result/info exists; pulse when degraded
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

SERVICE_KEYS = (
    "rt:services:redis_state",
    "rt:services:mqtt_bus",
    "rt:services:gps_ingest",
)

RESET_LEDS = {
    "schema": 1,
    "type": "led",
    "cmd": "reset_leds",
}

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


def _mode_off() -> dict[str, Any]:
    return {"mode": "off"}


def _mode_on() -> dict[str, Any]:
    return {"mode": "on"}


def _mode_blink(period_ms: int = 400) -> dict[str, Any]:
    return {"mode": "blink", "period_ms": int(period_ms)}


def _mode_pulse(period_ms: int = 900) -> dict[str, Any]:
    return {"mode": "pulse", "period_ms": int(period_ms)}


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

        # Ignore unsupported types for now.
        return None

    except RedisError:
        return None


def redis_hgetall_safe(r: redis.Redis, key: str) -> dict[str, str]:
    try:
        if r.type(key) == "hash":
            return r.hgetall(key)
    except Exception:
        pass
    return {}


def is_modal_active(modal_obj: Any) -> bool:
    if modal_obj is None:
        return False
    if isinstance(modal_obj, str):
        return bool(modal_obj.strip())
    return _truthy(modal_obj)


def is_browse_active(r: redis.Redis, focused_panel: str | None) -> bool:
    if not focused_panel:
        return False
    key = f"rt:ui:browse:{focused_panel}"
    value = redis_get_obj(r, key)
    return _truthy(value)


def is_system_degraded(
    system_health_obj: Any,
    service_hashes: dict[str, dict[str, str]],
) -> bool:
    if isinstance(system_health_obj, str):
        s = system_health_obj.strip().lower()
        if s in {"degraded", "failed", "fault", "error", "offline", "stale"}:
            return True
    elif isinstance(system_health_obj, dict):
        for key in ("state", "status", "health"):
            if key in system_health_obj:
                s = str(system_health_obj[key]).strip().lower()
                if s in {"degraded", "failed", "fault", "error", "offline", "stale"}:
                    return True
        for key in ("degraded", "stale", "fault", "error"):
            if key in system_health_obj and _truthy(system_health_obj[key]):
                return True

    for _svc_key, fields in service_hashes.items():
        state = str(fields.get("state") or "").strip().lower()
        if state and state not in {"running", "active"}:
            return True
        if fields.get("publisher_error"):
            return True

    return False


def has_recent_result(last_result_obj: Any) -> bool:
    if last_result_obj is None:
        return False
    if isinstance(last_result_obj, str):
        return bool(last_result_obj.strip())
    return True


def read_controller_led_inputs(r: redis.Redis) -> dict[str, Any]:
    page = _string_or_none(redis_get_obj(r, KEY_UI_PAGE))
    focus = _string_or_none(redis_get_obj(r, KEY_UI_FOCUS))
    layer = _string_or_none(redis_get_obj(r, KEY_UI_LAYER))
    authority_obj = redis_get_obj(r, KEY_UI_AUTHORITY) or {}
    modal_obj = redis_get_obj(r, KEY_UI_MODAL)
    browse_obj = redis_get_obj(r, KEY_UI_BROWSE)

    degraded = bool(authority_obj.get("degraded"))
    controller_authoritative = bool(authority_obj.get("controller_authoritative"))

    modal_active = _truthy(modal_obj)
    browse_active = _truthy(browse_obj)

    log(
        f"UI inputs page={page!r} focus={focus!r} "
        f"layer={layer!r} degraded={degraded} "
        f"modal={modal_active} browse={browse_active}"
    )

    return {
        "page": page,
        "focus": focus,
        "layer": layer,
        "degraded": degraded,
        "controller_authoritative": controller_authoritative,
        "modal_active": modal_active,
        "browse_active": browse_active,
    }


# -----------------------------------------------------------------------------
# LED derivation
# -----------------------------------------------------------------------------
def derive_snapshot_from_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    page = inputs["page"]
    focus = inputs["focus"]
    layer = inputs["layer"]
    degraded = inputs["degraded"]
    modal_active = inputs["modal_active"]
    browse_active = inputs["browse_active"]

    leds: dict[str, dict[str, Any]] = {
        "back": _mode_off(),
        "page": _mode_off(),
        "primary": _mode_off(),
        "cancel": _mode_off(),
        "mode": _mode_off(),
        "info": _mode_off(),
    }

    # INFO (system state)
    if degraded:
        leds["info"] = _mode_blink(400)
    else:
        leds["info"] = _mode_off()

    # LAYER → cancel + mode
    if layer == "modal":
        leds["cancel"] = _mode_on()
    elif degraded:
        leds["cancel"] = _mode_blink(400)

    if layer == "browse":
        leds["mode"] = _mode_pulse(900)

    # PAGE
    if page:
        leds["page"] = _mode_on()

    # BACK
    if page and page != "home":
        leds["back"] = _mode_on()

    # PRIMARY (focus exists and not degraded)
    if focus and not degraded:
        leds["primary"] = _mode_on()

    return {
        "schema": 1,
        "type": "led",
        "cmd": "snapshot",
        "leds": leds,
    }


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
                snapshot = derive_snapshot_from_inputs(inputs)

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
            snapshot = derive_snapshot_from_inputs(inputs)
            last_snapshot = send_snapshot_if_changed(ser, snapshot, last_snapshot)

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
            time.sleep(RETRY_SEC)

    close_quietly(ser)
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())