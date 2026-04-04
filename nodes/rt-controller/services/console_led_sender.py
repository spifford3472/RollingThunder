#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time

import serial
from serial import SerialException

PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
BAUD = 115200
OPEN_SETTLE_SEC = 1.5
RETRY_SEC = 2.0
LOOP_SLEEP_SEC = 0.25

SNAPSHOT = {
    "schema": 1,
    "type": "led",
    "cmd": "snapshot",
    "leds": {
        "back":    {"mode": "off"},
        "page":    {"mode": "pulse", "period_ms": 900},
        "primary": {"mode": "on"},
        "cancel":  {"mode": "off"},
        "mode":    {"mode": "blink", "period_ms": 400},
        "info":    {"mode": "on"},
    },
}

RESET = {
    "schema": 1,
    "type": "led",
    "cmd": "reset_leds",
}


def send_line(ser: serial.Serial, obj: dict) -> None:
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    print("TX:", line.strip())
    ser.write(line.encode("utf-8"))
    ser.flush()


def open_console() -> serial.Serial:
    print(f"opening {PORT} at {BAUD}")
    ser = serial.Serial(
        PORT,
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
    send_line(ser, RESET)
    time.sleep(0.2)
    send_line(ser, SNAPSHOT)
    time.sleep(0.2)


def main() -> int:
    ser: serial.Serial | None = None

    while True:
        if ser is None:
            try:
                ser = open_console()
                print("connected")
                sync_console(ser)
                print("synced")
            except Exception as e:
                print(f"open/sync failed: {e}", file=sys.stderr)
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                time.sleep(RETRY_SEC)
                continue

        try:
            if not ser.is_open:
                raise SerialException("serial port closed")
            time.sleep(LOOP_SLEEP_SEC)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"connection lost: {e}", file=sys.stderr)
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(RETRY_SEC)

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())