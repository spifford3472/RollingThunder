#!/usr/bin/env python3
"""
RollingThunder external fan runtime monitor.

Scope:
- Runs on rt-radio.
- Reads one Raspberry Pi GPIO as an input-only fan status signal.
- Publishes display-safe Redis state to rt:fan:external.
- Does not control a fan.
- Does not drive GPIO.
- Does not touch radio/adapter/rigctl/PTT/transmit logic.

Hardware default:
- DATA -> physical pin 11 / GPIO17
- GPIO is input only.
- Pi GPIO is 3.3V only; do not assume 5V tolerance.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


try:
    import redis  # type: ignore
except Exception as exc:
    print(f"ERROR: python redis module unavailable: {exc}", file=sys.stderr)
    sys.exit(2)


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

KEY_FAN = "rt:fan:external"
BUS_SYSTEM = "rt:system:bus"
SOURCE = "external_fan_monitor"

DEFAULT_ENABLED = True
DEFAULT_GPIO = 17
DEFAULT_PIN = 11
DEFAULT_ACTIVE_HIGH = True
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.2
DEFAULT_STABLE_SAMPLES = 3
DEFAULT_FORCE_PUBLISH_SECONDS = 30.0

_running = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def stop_handler(signum: int, frame: Any) -> None:
    global _running
    _running = False


def log(message: str) -> None:
    print(f"{utc_now_iso()} {SOURCE}: {message}", flush=True)


def warn(message: str) -> None:
    print(f"{utc_now_iso()} {SOURCE}: WARN: {message}", file=sys.stderr, flush=True)


def boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False

    return default


def intish(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= minimum else default
    except Exception:
        return default


def floatish(value: Any, default: float, minimum: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed >= minimum else default
    except Exception:
        return default


def load_app_config() -> Dict[str, Any]:
    try:
        with APP_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        warn(f"missing config {APP_CONFIG_PATH}")
    except json.JSONDecodeError as exc:
        warn(f"invalid JSON in {APP_CONFIG_PATH}: {exc}")
    except Exception as exc:
        warn(f"unable to read {APP_CONFIG_PATH}: {exc}")
    return {}


def get_external_fan_config(app: Dict[str, Any]) -> Dict[str, Any]:
    hardware = app.get("hardware", {})
    if not isinstance(hardware, dict):
        hardware = {}

    raw = hardware.get("external_fan", {})
    cfg = raw if isinstance(raw, dict) else {}

    return {
        "enabled": boolish(
            os.environ.get("RT_EXTERNAL_FAN_ENABLED") or cfg.get("enabled"),
            DEFAULT_ENABLED,
        ),
        "gpio": intish(
            os.environ.get("RT_EXTERNAL_FAN_GPIO") or cfg.get("gpio"),
            DEFAULT_GPIO,
            0,
        ),
        "pin": intish(
            os.environ.get("RT_EXTERNAL_FAN_PIN") or cfg.get("pin"),
            DEFAULT_PIN,
            1,
        ),
        "active_high": boolish(
            os.environ.get("RT_EXTERNAL_FAN_ACTIVE_HIGH") or cfg.get("active_high"),
            DEFAULT_ACTIVE_HIGH,
        ),
        "sample_interval_seconds": floatish(
            os.environ.get("RT_EXTERNAL_FAN_SAMPLE_INTERVAL_SECONDS")
            or cfg.get("sample_interval_seconds"),
            DEFAULT_SAMPLE_INTERVAL_SECONDS,
            0.05,
        ),
        "stable_samples": intish(
            os.environ.get("RT_EXTERNAL_FAN_STABLE_SAMPLES") or cfg.get("stable_samples"),
            DEFAULT_STABLE_SAMPLES,
            1,
        ),
        "force_publish_seconds": floatish(
            os.environ.get("RT_EXTERNAL_FAN_FORCE_PUBLISH_SECONDS")
            or cfg.get("force_publish_seconds"),
            DEFAULT_FORCE_PUBLISH_SECONDS,
            5.0,
        ),
    }


def get_redis_client(app: Dict[str, Any]) -> "redis.Redis":
    """
    Follow existing RollingThunder Redis environment style.

    Preferred for rt-radio systemd:
      EnvironmentFile=/etc/rollingthunder/redis.env

    Supported:
      RT_REDIS_URL / REDIS_URL
      RT_REDIS_HOST / REDIS_HOST
      RT_REDIS_PORT / REDIS_PORT
      RT_REDIS_DB / REDIS_DB
      RT_REDIS_PASSWORD / REDIS_PASSWORD
    """

    redis_cfg = app.get("redis", {})
    if not isinstance(redis_cfg, dict):
        redis_cfg = {}

    url = os.environ.get("RT_REDIS_URL") or os.environ.get("REDIS_URL") or redis_cfg.get("url")
    if url:
        client = redis.Redis.from_url(
            str(url),
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        client.ping()
        return client

    password = os.environ.get("RT_REDIS_PASSWORD") or os.environ.get("REDIS_PASSWORD")
    password_env = redis_cfg.get("password_env")
    if not password and isinstance(password_env, str) and password_env:
        password = os.environ.get(password_env)

    host = (
        os.environ.get("RT_REDIS_HOST")
        or os.environ.get("REDIS_HOST")
        or redis_cfg.get("host")
        or "127.0.0.1"
    )
    port = intish(
        os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT") or redis_cfg.get("port"),
        6379,
        1,
    )
    db = intish(
        os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB") or redis_cfg.get("db"),
        0,
        0,
    )

    client = redis.Redis(
        host=str(host),
        port=port,
        db=db,
        password=password,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    client.ping()
    return client


class GpioReader:
    """
    Input-only GPIO reader.

    Preferred path:
      gpiozero DigitalInputDevice, which uses input mode and does not drive the pin.

    Fallback path:
      Linux sysfs GPIO direction=in and value read.

    Neither path writes output values.
    """

    def __init__(self, gpio: int) -> None:
        self.gpio = int(gpio)
        self.backend = "uninitialized"
        self._device: Any = None
        self._sysfs_value_path: Optional[Path] = None

        self._init_gpiozero_or_sysfs()

    def _init_gpiozero_or_sysfs(self) -> None:
        try:
            from gpiozero import Device, DigitalInputDevice  # type: ignore
            from gpiozero.pins.lgpio import LGPIOFactory  # type: ignore
            os.chdir("/tmp")
            Device.pin_factory = LGPIOFactory()

            # pull_up=None avoids enabling an internal pull resistor.
            # active_state is required when pull_up=None.
            # This remains strictly an input-only device.
            self._device = DigitalInputDevice(
                self.gpio,
                pull_up=None,
                active_state=True,
                pin_factory=Device.pin_factory,
            )
            self.backend = "gpiozero:lgpio"
            log(f"GPIO{self.gpio} opened input-only using gpiozero:lgpio")
            return
        except Exception as exc:
            raise RuntimeError(f"unable to open GPIO{self.gpio} input using gpiozero:lgpio: {exc}") from exc

    def _init_sysfs(self) -> None:
        gpio_dir = Path(f"/sys/class/gpio/gpio{self.gpio}")
        value_path = gpio_dir / "value"
        direction_path = gpio_dir / "direction"

        if not gpio_dir.exists():
            try:
                Path("/sys/class/gpio/export").write_text(str(self.gpio), encoding="utf-8")
                time.sleep(0.1)
            except Exception as exc:
                raise RuntimeError(f"unable to export GPIO{self.gpio} via sysfs: {exc}") from exc

        try:
            # Safe direction: input only. Never write output values.
            direction_path.write_text("in", encoding="utf-8")
        except Exception as exc:
            raise RuntimeError(f"unable to set GPIO{self.gpio} direction=input via sysfs: {exc}") from exc

        if not value_path.exists():
            raise RuntimeError(f"GPIO{self.gpio} sysfs value path missing after export")

        self._sysfs_value_path = value_path
        self.backend = "sysfs"
        log(f"GPIO{self.gpio} opened input-only using sysfs")

    def read_raw(self) -> bool:
        if self.backend.startswith("gpiozero") and self._device is not None:
            try:
                # pin.state is raw electrical state when available.
                return bool(int(self._device.pin.state))
            except Exception:
                return bool(int(self._device.value))

        if self.backend == "sysfs" and self._sysfs_value_path is not None:
            raw = self._sysfs_value_path.read_text(encoding="utf-8").strip()
            return raw == "1"

        raise RuntimeError("GPIO reader is not initialized")

    def close(self) -> None:
        try:
            if self._device is not None:
                self._device.close()
        except Exception:
            pass


def build_model(
    *,
    running: bool,
    raw_input: Optional[bool],
    cfg: Dict[str, Any],
    reason: str,
    error: Optional[str] = None,
    backend: Optional[str] = None,
) -> Dict[str, Any]:
    model: Dict[str, Any] = {
        "running": bool(running),
        "gpio": int(cfg["gpio"]),
        "pin": int(cfg["pin"]),
        "active_high": bool(cfg["active_high"]),
        "source": SOURCE,
        "updated_utc": utc_now_iso(),
        "last_update_ms": now_ms(),
        "reason": reason,
        "stale": False,
    }

    if raw_input is not None:
        model["raw_input"] = bool(raw_input)

    if backend:
        model["backend"] = backend

    if error:
        model["error"] = str(error)

    return model


def stable_part(model: Dict[str, Any]) -> Dict[str, Any]:
    ignored = {
        "updated_utc",
        "last_update_ms",
        "reason",
    }
    return {k: v for k, v in model.items() if k not in ignored}


def read_existing(client: "redis.Redis") -> Optional[Dict[str, Any]]:
    try:
        raw = client.get(KEY_FAN)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        warn(f"unable to read existing {KEY_FAN}: {exc}")
        return None


def should_publish(
    new_model: Dict[str, Any],
    old_model: Optional[Dict[str, Any]],
    last_publish_mono: float,
    force_publish_seconds: float,
) -> Tuple[bool, str]:
    if old_model is None:
        return True, "initial_publish"

    if stable_part(new_model) != stable_part(old_model):
        return True, "stable_changed"

    if time.monotonic() - last_publish_mono >= force_publish_seconds:
        return True, "force_publish"

    return False, "unchanged"


def publish_state_changed(client: "redis.Redis", keys: list[str], reason: str) -> None:
    event = {
        "type": "state.changed",
        "topic": "state.changed",
        "source": SOURCE,
        "keys": keys,
        "changed_keys": keys,
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now_iso(),
        "host": socket.gethostname(),
    }
    client.publish(BUS_SYSTEM, json.dumps(event, sort_keys=True, separators=(",", ":")))


def publish(client: "redis.Redis", model: Dict[str, Any], reason: str) -> None:
    client.set(KEY_FAN, json.dumps(model, sort_keys=True, separators=(",", ":")))
    publish_state_changed(client, [KEY_FAN], reason)


def publish_disabled_once(client: "redis.Redis", cfg: Dict[str, Any]) -> None:
    model = build_model(
        running=False,
        raw_input=None,
        cfg=cfg,
        reason="config_disabled",
        error=None,
        backend=None,
    )
    publish(client, model, "config_disabled")
    log("published config_disabled state")


def main() -> int:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    client: Optional["redis.Redis"] = None
    reader: Optional[GpioReader] = None

    last_publish_mono = 0.0
    stable_raw: Optional[bool] = None
    candidate_raw: Optional[bool] = None
    candidate_count = 0

    log("starting")

    while _running:
        app = load_app_config()
        cfg = get_external_fan_config(app)

        sleep_seconds = float(cfg["sample_interval_seconds"])
        force_publish_seconds = float(cfg["force_publish_seconds"])

        try:
            if client is None:
                client = get_redis_client(app)

            if not bool(cfg["enabled"]):
                publish_disabled_once(client, cfg)
                while _running:
                    time.sleep(5.0)
                break

            gpio = int(cfg["gpio"])
            if reader is None or reader.gpio != gpio:
                if reader is not None:
                    reader.close()
                reader = GpioReader(gpio)

            raw = reader.read_raw()

            if candidate_raw is None or raw != candidate_raw:
                candidate_raw = raw
                candidate_count = 1
            else:
                candidate_count += 1

            if stable_raw is None:
                if candidate_count >= int(cfg["stable_samples"]):
                    stable_raw = bool(candidate_raw)
            elif candidate_count >= int(cfg["stable_samples"]) and candidate_raw != stable_raw:
                stable_raw = bool(candidate_raw)

            if stable_raw is not None:
                active_high = bool(cfg["active_high"])
                running = bool(stable_raw) if active_high else not bool(stable_raw)

                model = build_model(
                    running=running,
                    raw_input=stable_raw,
                    cfg=cfg,
                    reason="sample_stable",
                    backend=reader.backend if reader else None,
                )

                old_model = read_existing(client)
                do_publish, publish_reason = should_publish(
                    model,
                    old_model,
                    last_publish_mono,
                    force_publish_seconds,
                )

                if do_publish:
                    model["reason"] = publish_reason
                    publish(client, model, publish_reason)
                    last_publish_mono = time.monotonic()
                    log(
                        f"published running={model.get('running')} "
                        f"raw_input={model.get('raw_input')} "
                        f"active_high={model.get('active_high')} "
                        f"backend={model.get('backend')} "
                        f"reason={publish_reason}"
                    )

        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            warn(f"cycle failed: {err}")

            try:
                if client is None:
                    client = get_redis_client(app)

                model = build_model(
                    running=False,
                    raw_input=None,
                    cfg=cfg,
                    reason="gpio_read_error",
                    error=err,
                    backend=reader.backend if reader else None,
                )

                old_model = read_existing(client)
                do_publish, publish_reason = should_publish(
                    model,
                    old_model,
                    last_publish_mono,
                    force_publish_seconds,
                )

                if do_publish:
                    model["reason"] = "gpio_read_error"
                    publish(client, model, "gpio_read_error")
                    last_publish_mono = time.monotonic()

            except Exception as publish_exc:
                warn(f"unable to publish error state: {publish_exc}")

            client = None
            if reader is not None:
                reader.close()
                reader = None

        sleep_until = time.monotonic() + sleep_seconds
        while _running and time.monotonic() < sleep_until:
            remaining = sleep_until - time.monotonic()
            time.sleep(min(0.05, max(0.0, remaining)))

    if reader is not None:
        reader.close()

    log("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())