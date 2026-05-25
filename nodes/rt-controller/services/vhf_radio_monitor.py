#!/usr/bin/env python3
"""
RollingThunder VHF radio availability monitor.

Phase 3 scope:
- Publishes calm controller-owned VHF availability state to rt:vhf:radio.
- Publishes state.changed to rt:system:bus when the model is written.
- Does not write rt:ui:bus.
- Does not scan Redis.
- Does not program, tune, scan, or otherwise control the radio.
- Stub mode intentionally reports unknown until a real safe control path is added later.
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

KEY_VHF_RADIO = "rt:vhf:radio"
BUS_SYSTEM = "rt:system:bus"
SOURCE = "vhf_radio_monitor"

_running = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stop_handler(signum: int, frame: Any) -> None:
    global _running
    _running = False


def load_app_config() -> Dict[str, Any]:
    try:
        with APP_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        print(f"WARN: missing config {APP_CONFIG_PATH}", file=sys.stderr)
    except json.JSONDecodeError as exc:
        print(f"WARN: invalid JSON in {APP_CONFIG_PATH}: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: unable to read {APP_CONFIG_PATH}: {exc}", file=sys.stderr)
    return {}


def boolish(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def intish(value: Any, default: int, minimum: int) -> int:
    try:
        return max(int(value), minimum)
    except Exception:
        return default


def get_vhf_config(app: Dict[str, Any]) -> Dict[str, Any]:
    raw = app.get("vhf", {})
    if not isinstance(raw, dict):
        raw = {}

    return {
        "radio_name": str(raw.get("radio_name", "Icom IC-2730A")),
        "radio_monitor_enabled": boolish(raw.get("radio_monitor_enabled", True), True),
        "radio_control_mode": str(raw.get("radio_control_mode", "stub")).strip().lower(),
        "radio_monitor_interval_sec": intish(raw.get("radio_monitor_interval_sec", 30), 30, 5),
        "radio_force_publish_sec": intish(raw.get("radio_force_publish_sec", 300), 300, 30),
    }


def get_redis_client(app: Dict[str, Any]) -> "redis.Redis":
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

    host = os.environ.get("RT_REDIS_HOST") or os.environ.get("REDIS_HOST") or redis_cfg.get("host") or "127.0.0.1"
    port = intish(os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT") or redis_cfg.get("port"), 6379, 1)
    db = intish(os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB") or redis_cfg.get("db"), 0, 0)

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


def build_model(cfg: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(cfg["radio_monitor_enabled"])
    mode = str(cfg["radio_control_mode"]).strip().lower()

    if not enabled:
        status = "unknown"
        available = False
        reason = "VHF radio monitor disabled"
    elif mode in {"", "stub", "none", "disabled"}:
        status = "unknown"
        available = False
        reason = "VHF control path not configured"
    else:
        status = "unknown"
        available = False
        reason = f"Unsupported VHF control mode: {mode}"

    return {
        "status": status,
        "available": available,
        "radio": str(cfg["radio_name"]),
        "source": SOURCE,
        "reason": reason,
        "updated_utc": utc_now_iso(),
    }


def stable_part(model: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": model.get("status"),
        "available": model.get("available"),
        "radio": model.get("radio"),
        "source": model.get("source"),
        "reason": model.get("reason"),
    }


def read_existing(client: "redis.Redis") -> Optional[Dict[str, Any]]:
    try:
        raw = client.get(KEY_VHF_RADIO)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        print(f"WARN: unable to read existing {KEY_VHF_RADIO}: {exc}", file=sys.stderr)
        return None


def should_publish(
    new_model: Dict[str, Any],
    old_model: Optional[Dict[str, Any]],
    last_publish_mono: float,
    force_publish_sec: int,
) -> Tuple[bool, str]:
    if old_model is None:
        return True, "initial_publish"

    if stable_part(new_model) != stable_part(old_model):
        return True, "model_changed"

    if time.monotonic() - last_publish_mono >= force_publish_sec:
        return True, "force_publish"

    return False, "unchanged"


def publish(client: "redis.Redis", model: Dict[str, Any], reason: str) -> None:
    client.set(KEY_VHF_RADIO, json.dumps(model, sort_keys=True, separators=(",", ":")))

    event = {
        "type": "state.changed",
        "source": SOURCE,
        "keys": [KEY_VHF_RADIO],
        "changed_keys": [KEY_VHF_RADIO],
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now_iso(),
        "host": socket.gethostname(),
    }
    client.publish(BUS_SYSTEM, json.dumps(event, sort_keys=True, separators=(",", ":")))


def main() -> int:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    client: Optional["redis.Redis"] = None
    last_publish_mono = 0.0

    print(f"{SOURCE}: starting", flush=True)

    while _running:
        interval_sec = 30

        try:
            app = load_app_config()
            cfg = get_vhf_config(app)
            interval_sec = int(cfg["radio_monitor_interval_sec"])
            force_publish_sec = int(cfg["radio_force_publish_sec"])

            if client is None:
                client = get_redis_client(app)

            model = build_model(cfg)
            old_model = read_existing(client)

            do_publish, publish_reason = should_publish(
                model,
                old_model,
                last_publish_mono,
                force_publish_sec,
            )

            if do_publish:
                publish(client, model, publish_reason)
                last_publish_mono = time.monotonic()
                print(
                    f"{SOURCE}: published status={model['status']} "
                    f"available={model['available']} reason={model['reason']} "
                    f"publish_reason={publish_reason}",
                    flush=True,
                )

        except Exception as exc:
            client = None
            print(f"ERROR: {SOURCE}: cycle failed: {exc}", file=sys.stderr, flush=True)

        slept = 0
        while _running and slept < interval_sec:
            time.sleep(1)
            slept += 1

    print(f"{SOURCE}: stopping", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())