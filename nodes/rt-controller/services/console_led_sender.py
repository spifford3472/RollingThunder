#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import sys
import time
from typing import Any

import serial
from serial import SerialException

# -----------------------------------------------------------------------------
# Stage purpose
# -----------------------------------------------------------------------------
# This is the minimal long-running sender skeleton for the RollingThunder
# console LED path.
#
# Current scope:
# - bind to the console via stable /dev/serial/by-id path
# - send reset_leds + snapshot on connect
# - remain alive
# - reconnect after unplug/replug
#
# Intentionally NOT implemented yet:
# - Redis-derived LED meaning
# - incremental set updates
# - show_push helper wiring
# - state change detection
#
# -----------------------------------------------------------------------------
# Stable console device
# -----------------------------------------------------------------------------
CONSOLE_PORT = (
    "/dev/serial/by-id/"
    "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
)

BAUD = 115200
OPEN_SETTLE_SEC = 1.5
POST_RESET_DELAY_SEC = 0.2
POST_SNAPSHOT_DELAY_SEC = 0.2
RETRY_SEC = 2.0
LOOP_SLEEP_SEC = 0.25

# -----------------------------------------------------------------------------
# Fixed development snapshot
# -----------------------------------------------------------------------------
SNAPSHOT: dict[str, Any] = {
    "schema": 1,
    "type": "led",
    "cmd": "snapshot",
    "leds": {
        "back": {"mode": "off"},
        "page": {"mode": "pulse", "period_ms": 900},
        "primary": {"mode": "on"},
        "cancel": {"mode": "off"},
        "mode": {"mode": "blink", "period_ms": 400},
        "info": {"mode": "on"},
    },
}

RESET_LEDS: dict[str, Any] = {
    "schema": 1,
    "type": "led",
    "cmd": "reset_leds",
}

_running = True


def _handle_signal(signum: int, frame: Any) -> None:
    global _running
    _running = False


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


def sync_console(ser: serial.Serial) -> None:
    send_line(ser, RESET_LEDS)
    time.sleep(POST_RESET_DELAY_SEC)

    send_line(ser, SNAPSHOT)
    time.sleep(POST_SNAPSHOT_DELAY_SEC)

    log("synced: reset_leds + snapshot")


def close_quietly(ser: serial.Serial | None) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    ser: serial.Serial | None = None

    while _running:
        if ser is None:
            try:
                ser = open_console()
                log("connected")
                sync_console(ser)
            except Exception as e:
                log_err(f"open/sync failed: {type(e).__name__}: {e}")
                close_quietly(ser)
                ser = None
                time.sleep(RETRY_SEC)
                continue

        try:
            if not ser.is_open:
                raise SerialException("serial port closed")

            # Light-touch liveness check.
            # We do not consume protocol traffic here.
            time.sleep(LOOP_SLEEP_SEC)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log_err(f"connection lost: {type(e).__name__}: {e}")
            close_quietly(ser)
            ser = None
            time.sleep(RETRY_SEC)

    close_quietly(ser)
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())