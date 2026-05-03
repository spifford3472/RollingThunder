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

KEY_UI_LED_SNAPSHOT = "rt:ui:led_snapshot"

BLINK_SLOW_MS = 900
BLINK_FAST_MS = 400
PULSE_MS = 900

RESET_LEDS = {
    "schema": 1,
    "type": "led",
    "cmd": "reset_leds",
}

CONTROL_NAMES = ("back", "page", "primary", "cancel", "mode", "info")

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
# Redis reads
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


def read_led_snapshot(r: redis.Redis) -> dict[str, Any]:
    obj = redis_get_obj(r, KEY_UI_LED_SNAPSHOT)
    snapshot = _as_dict(obj)

    if not snapshot:
        return {
            "schema": 1,
            "type": "led_snapshot",
            "ts_ms": int(time.time() * 1000),
            "leds": {name: {"mode": "off"} for name in CONTROL_NAMES},
            "show_push": None,
        }

    leds = _as_dict(snapshot.get("leds"))
    normalized_leds: dict[str, dict[str, Any]] = {}

    for name in CONTROL_NAMES:
        entry = _as_dict(leds.get(name))
        mode = _string_or_none(entry.get("mode")) or "off"
        period_ms = entry.get("period_ms")

        normalized: dict[str, Any] = {"mode": mode}
        if period_ms is not None:
            try:
                normalized["period_ms"] = int(period_ms)
            except Exception:
                pass

        normalized_leds[name] = normalized

    show_push = snapshot.get("show_push")
    if not isinstance(show_push, dict):
        show_push = None

    result = {
        "schema": 1,
        "type": "led_snapshot",
        "ts_ms": snapshot.get("ts_ms"),
        "leds": normalized_leds,
        "show_push": show_push,
    }

    log(
        "LED snapshot "
        + " ".join(
            f"{name}={normalized_leds[name].get('mode')}"
            for name in CONTROL_NAMES
        )
    )

    return result


# -----------------------------------------------------------------------------
# Snapshot translation
# -----------------------------------------------------------------------------
def semantic_entry_to_transport(entry: dict[str, Any]) -> dict[str, Any]:
    mode = _string_or_none(entry.get("mode")) or "off"
    period_ms = entry.get("period_ms")

    # IMPORTANT:
    # The physical console must consume the same semantic LED modes as the
    # virtual panel. Do not translate blink_slow/blink_fast into generic blink,
    # or the two panels can disagree.
    if mode not in {"off", "on", "blink_slow", "blink_fast", "pulse"}:
        mode = "off"

    out: dict[str, Any] = {"mode": mode}

    if period_ms is not None:
        try:
            out["period_ms"] = int(period_ms)
        except Exception:
            pass

    return out


def build_transport_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    leds_in = _as_dict(snapshot.get("leds"))
    leds_out: dict[str, dict[str, Any]] = {}

    for name in CONTROL_NAMES:
        leds_out[name] = semantic_entry_to_transport(_as_dict(leds_in.get(name)))

    return {
        "schema": 1,
        "type": "led",
        "cmd": "snapshot",
        "leds": leds_out,
    }


def build_show_push(button: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "type": "led",
        "cmd": "show_push",
        "button": button,
    }


def show_push_token(snapshot: dict[str, Any]) -> str | None:
    show_push = _as_dict(snapshot.get("show_push"))
    token = _string_or_none(show_push.get("token"))
    button = _string_or_none(show_push.get("button"))
    if not token or not button:
        return None
    return f"{token}:{button}"


# -----------------------------------------------------------------------------
# Comparison helpers
# -----------------------------------------------------------------------------
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


def send_show_push_if_needed(
    ser: serial.Serial,
    snapshot: dict[str, Any],
    last_push_token: str | None,
) -> str | None:
    show_push = _as_dict(snapshot.get("show_push"))
    button = _string_or_none(show_push.get("button"))
    token = show_push_token(snapshot)

    if not token or not button:
        return last_push_token

    if token == last_push_token:
        return last_push_token

    send_line(ser, build_show_push(button))
    log(f"show_push sent button={button} token={token}")
    return token


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
    last_push_token: str | None = None

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
                semantic_snapshot = read_led_snapshot(r)
                transport_snapshot = build_transport_snapshot(semantic_snapshot)
                last_push_token = show_push_token(semantic_snapshot)

                ser = open_console()
                log("console connected")
                sync_console(ser, transport_snapshot)
                last_snapshot = transport_snapshot
            except Exception as e:
                log_err(f"console open/sync failed: {type(e).__name__}: {e}")
                close_quietly(ser)
                ser = None
                time.sleep(RETRY_SEC)
                continue

        try:
            if not ser.is_open:
                raise SerialException("serial port closed")

            semantic_snapshot = read_led_snapshot(r)
            transport_snapshot = build_transport_snapshot(semantic_snapshot)
            last_snapshot = send_snapshot_if_changed(ser, transport_snapshot, last_snapshot)
            last_push_token = send_show_push_if_needed(ser, semantic_snapshot, last_push_token)

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