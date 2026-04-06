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

_running = True


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
    return serial.Serial(
        SERIAL_PORT,
        SERIAL_BAUD,
        timeout=SERIAL_TIMEOUT,
        write_timeout=1.0,
        dsrdtr=False,
        rtscts=False,
    )


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
        try:
            delta = int(event.get("value", 0))
        except Exception:
            return None

        if delta == 0:
            return None

        return {"intent": "ui.browse.delta", "params": {"delta": delta}}

    if control == "enc_main" and etype == "press":
        return {"intent": "ui.ok", "params": {}}

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


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    r: redis.Redis | None = None
    ser: serial.Serial | None = None

    while _running:
        try:
            if r is None:
                r = redis_client()
                log("redis connected")

            if ser is None:
                ser = open_serial()
                log("serial connected")

            line = ser.readline()
            if not line:
                continue

            try:
                text = line.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            if not text:
                continue

            log(f"rx: {text}")

            try:
                event = json.loads(text)
            except Exception:
                log_err("ignored non-json serial line")
                continue

            if not isinstance(event, dict):
                continue

            intent_obj = map_event_to_intent(event)
            if intent_obj is None:
                continue

            publish_intent(r, intent_obj)

        except KeyboardInterrupt:
            break
        except (RedisError, SerialException, OSError) as e:
            log_err(f"bridge error: {type(e).__name__}: {e}")
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            ser = None
            r = None
            time.sleep(RETRY_SEC)
        except Exception as e:
            log_err(f"unexpected error: {type(e).__name__}: {e}")
            time.sleep(RETRY_SEC)

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass

    log("panel_input_bridge stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())