#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any

import redis
import serial
from redis.exceptions import RedisError
from serial import SerialException

SERIAL_PORT = os.environ.get(
    "RT_PANEL_SERIAL_PORT",
    "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
)
SERIAL_BAUD = int(os.environ.get("RT_PANEL_SERIAL_BAUD", "115200"))
SERIAL_TIMEOUT = float(os.environ.get("RT_PANEL_SERIAL_TIMEOUT", "0.25"))

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

RETRY_SEC = float(os.environ.get("RT_PANEL_BRIDGE_RETRY_SEC", "2.0"))

# New hardening knobs
SERIAL_STABILIZE_SEC = float(os.environ.get("RT_PANEL_SERIAL_STABILIZE_SEC", "1.5"))
SYNC_MAX_CONSECUTIVE_NONJSON = int(os.environ.get("RT_PANEL_SYNC_MAX_CONSECUTIVE_NONJSON", "10"))
POST_SYNC_MAX_CONSECUTIVE_NONJSON = int(os.environ.get("RT_PANEL_POST_SYNC_MAX_CONSECUTIVE_NONJSON", "25"))
LAST_ENCODER_ROTATE_MS = 0
ENCODER_ROTATE_THROTTLE_MS = int(os.environ.get("RT_ENCODER_ROTATE_THROTTLE_MS", "120"))

_running = True


class SerialResyncRequired(Exception):
    """Raised when serial input appears unhealthy and should be reopened."""


def _handle_signal(signum: int, frame: Any) -> None:
    global _running
    _running = False


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        health_check_interval=15,
    )
    r.ping()
    return r


def open_serial() -> serial.Serial:
    log(f"opening serial port {SERIAL_PORT} @ {SERIAL_BAUD}")
    ser = serial.Serial(
        SERIAL_PORT,
        SERIAL_BAUD,
        timeout=SERIAL_TIMEOUT,
        write_timeout=1.0,
        dsrdtr=False,
        rtscts=False,
    )

    # Give the ESP32 time to finish booting and emitting bootloader noise.
    if SERIAL_STABILIZE_SEC > 0:
        log(f"serial stabilize delay: {SERIAL_STABILIZE_SEC:.2f}s")
        time.sleep(SERIAL_STABILIZE_SEC)

    # Clear any startup garbage before entering sync mode.
    try:
        ser.reset_input_buffer()
        log("serial input buffer reset")
    except Exception as e:
        log_err(f"serial input buffer reset failed: {type(e).__name__}: {e}")

    return ser


def safe_close_serial(ser: serial.Serial | None) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def normalize_button(name: Any) -> str | None:
    s = str(name or "").strip().lower()
    if not s:
        return None
    aliases = {
        "ok": "primary",
        "enter": "primary",
        "select": "primary",
        "page": "page",
        "back": "back",
        "cancel": "cancel",
        "mode": "mode",
        "info": "info",
        "primary": "primary",
    }
    return aliases.get(s, s)


def map_event_to_intent(event: dict[str, Any]) -> dict[str, Any] | None:
    control = str(event.get("control_id") or "").strip().lower()
    etype = str(event.get("event_type") or "").strip().lower()

    if not control:
        return None

    # ---- Buttons ----
    if control == "btn_page" and etype == "press":
        return {"intent": "ui.page.next", "params": {}}

    if control == "btn_back" and etype == "press":
        return {"intent": "ui.back", "params": {}}

    if control == "btn_primary" and etype == "press":
        return {"intent": "ui.ok", "params": {}}

    if control == "btn_cancel" and etype == "press":
        return {"intent": "ui.cancel", "params": {}}

    if control == "btn_mode" and etype == "press":
        return {"intent": "ui.focus.next", "params": {}}

    if control == "btn_info" and etype == "press":
        return {"intent": "ui.focus.prev", "params": {}}

    # ---- Encoder ----
    if control == "enc_main" and etype == "rotate":
        global LAST_ENCODER_ROTATE_MS

        try:
            delta = int(event.get("value", 0))
        except Exception:
            return None

        if delta == 0:
            return None

        now_ms = int(time.time() * 1000)
        if now_ms - LAST_ENCODER_ROTATE_MS < ENCODER_ROTATE_THROTTLE_MS:
            return None

        LAST_ENCODER_ROTATE_MS = now_ms

        return {"intent": "ui.browse.delta", "params": {"delta": 1 if delta > 0 else -1}}

    if control == "enc_main" and etype == "press":
        return {"intent": "ui.encoder.press", "params": {}}

    return None


def publish_intent(r: redis.Redis, intent_obj: dict[str, Any]) -> None:
    payload = {
        **intent_obj,
        "source": {
            "type": "panel_bridge",
            "node": os.environ.get("RT_NODE_ID", "rt-controller"),
        },
        "timestamp": int(time.time() * 1000),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    r.publish(INTENTS_CH, raw)
    log(f"published intent: {raw}")


def read_serial_text_line(ser: serial.Serial) -> str | None:
    line = ser.readline()
    if not line:
        return None

    try:
        text = line.decode("utf-8", errors="replace").strip()
    except Exception:
        return None

    if not text:
        return None

    return text


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except Exception:
        return None

    if not isinstance(obj, dict):
        return None

    return obj


def sync_to_first_valid_json(ser: serial.Serial) -> None:
    nonjson_count = 0
    log("entering serial sync mode")

    while _running:
        text = read_serial_text_line(ser)
        if text is None:
            continue

        event = parse_json_object(text)
        if event is not None:
            log(f"serial sync acquired with event: {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}")
            return

        nonjson_count += 1
        log_err(f"sync mode ignored non-json serial line ({nonjson_count}/{SYNC_MAX_CONSECUTIVE_NONJSON})")

        if nonjson_count >= SYNC_MAX_CONSECUTIVE_NONJSON:
            raise SerialResyncRequired(
                f"failed to acquire JSON sync after {nonjson_count} consecutive non-json lines"
            )


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    r: redis.Redis | None = None
    ser: serial.Serial | None = None
    synced = False
    consecutive_nonjson_after_sync = 0

    while _running:
        try:
            if r is None:
                r = redis_client()
                log("redis connected")

            if ser is None:
                ser = open_serial()
                log("serial connected")
                synced = False
                consecutive_nonjson_after_sync = 0

            if not synced:
                sync_to_first_valid_json(ser)
                synced = True
                consecutive_nonjson_after_sync = 0
                continue

            text = read_serial_text_line(ser)
            if text is None:
                continue

            log(f"rx: {text}")

            event = parse_json_object(text)
            if event is None:
                consecutive_nonjson_after_sync += 1
                log_err(
                    f"ignored non-json serial line after sync "
                    f"({consecutive_nonjson_after_sync}/{POST_SYNC_MAX_CONSECUTIVE_NONJSON})"
                )

                if consecutive_nonjson_after_sync >= POST_SYNC_MAX_CONSECUTIVE_NONJSON:
                    raise SerialResyncRequired(
                        f"too many consecutive non-json lines after sync: {consecutive_nonjson_after_sync}"
                    )
                continue

            consecutive_nonjson_after_sync = 0

            intent_obj = map_event_to_intent(event)
            if intent_obj is None:
                continue

            publish_intent(r, intent_obj)

        except KeyboardInterrupt:
            break
        except SerialResyncRequired as e:
            log_err(f"serial resync required: {e}")
            safe_close_serial(ser)
            ser = None
            synced = False
            consecutive_nonjson_after_sync = 0
            time.sleep(RETRY_SEC)
        except (RedisError, SerialException, OSError) as e:
            log_err(f"bridge error: {type(e).__name__}: {e}")
            safe_close_serial(ser)
            ser = None
            r = None
            synced = False
            consecutive_nonjson_after_sync = 0
            time.sleep(RETRY_SEC)
        except Exception as e:
            log_err(f"unexpected error: {type(e).__name__}: {e}")
            safe_close_serial(ser)
            ser = None
            synced = False
            consecutive_nonjson_after_sync = 0
            time.sleep(RETRY_SEC)

    safe_close_serial(ser)
    log("panel_input_bridge stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())