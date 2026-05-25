#!/usr/bin/env python3
"""
RollingThunder IC-2730A adapter status publisher.

Phase 7B-2 scope:
- Run on rt-radio, where the IC-2730A is physically connected.
- Instantiate the safe IC2730AAdapter boundary.
- Publish structured adapter status to Redis key rt:vhf:adapter.
- Publish state.changed to rt:system:bus.
- Avoid excessive Redis writes.
- Do not write rt:ui:bus.
- Do not program radio memories.
- Do not clear memories.
- Do not start or stop scan.
- Do not program Side B.
- Do not expose PTT/transmit controls.

This service depends on:
  nodes/rt-radio/services/ic2730a_adapter.py

The adapter file is the only place that may import Python Hamlib or know
IC-2730A/Hamlib details.
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

from ic2730a_adapter import IC2730AAdapter, IC2730AConfig


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

KEY_ADAPTER = "rt:vhf:adapter"
BUS_SYSTEM = "rt:system:bus"
SOURCE = "vhf_ic2730a_adapter_status"

DEFAULT_PUBLISH_INTERVAL_SECONDS = 30
DEFAULT_FORCE_PUBLISH_SECONDS = 300

_running = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stop_handler(signum: int, frame: Any) -> None:
    global _running
    _running = False


def log(message: str) -> None:
    print(f"{utc_now_iso()} {SOURCE}: {message}", flush=True)


def warn(message: str) -> None:
    print(f"{utc_now_iso()} {SOURCE}: WARN: {message}", file=sys.stderr, flush=True)


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


def get_vhf_config(app: Dict[str, Any]) -> Dict[str, Any]:
    raw = app.get("vhf", {})
    return raw if isinstance(raw, dict) else {}


def get_ic2730a_config(app: Dict[str, Any]) -> Dict[str, Any]:
    vhf = get_vhf_config(app)
    raw = vhf.get("ic2730a", {})
    return raw if isinstance(raw, dict) else {}


def get_publish_config(app: Dict[str, Any]) -> Dict[str, Any]:
    ic = get_ic2730a_config(app)

    return {
        "publish_adapter_status": boolish(ic.get("publish_adapter_status"), True),
        "publish_interval_seconds": intish(
            os.environ.get("RT_VHF_ADAPTER_PUBLISH_INTERVAL_SECONDS")
            or ic.get("publish_interval_seconds"),
            DEFAULT_PUBLISH_INTERVAL_SECONDS,
            1,
        ),
        "force_publish_seconds": intish(
            os.environ.get("RT_VHF_ADAPTER_FORCE_PUBLISH_SECONDS")
            or ic.get("force_publish_seconds"),
            DEFAULT_FORCE_PUBLISH_SECONDS,
            30,
        ),
    }


def get_redis_client(app: Dict[str, Any]) -> "redis.Redis":
    """
    Follow the existing RollingThunder Redis environment style.

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
        os.environ.get("RT_REDIS_PORT")
        or os.environ.get("REDIS_PORT")
        or redis_cfg.get("port"),
        6379,
        1,
    )
    db = intish(
        os.environ.get("RT_REDIS_DB")
        or os.environ.get("REDIS_DB")
        or redis_cfg.get("db"),
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


def normalize_adapter_status(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize adapter output into the rt:vhf:adapter Redis model.

    The IC2730AAdapter already returns safe fields. This layer makes sure
    publish-time fields are present and risky capabilities remain false.
    """

    model = dict(raw) if isinstance(raw, dict) else {}

    model.setdefault("status", "error")
    model.setdefault("available", False)
    model.setdefault("radio", "Icom IC-2730A")
    model.setdefault("control_mode", "dry_run")
    model.setdefault("hamlib_model", 3085)
    model.setdefault("serial_port", "/dev/ic2730a")
    model.setdefault("reason", "IC-2730A adapter status unavailable.")
    model.setdefault("source", "ic2730a_adapter")

    # Phase 7B safety gates: keep all real write/control flags false here even
    # if a bad config accidentally says otherwise.
    model["writes_enabled"] = False
    model["scan_control_enabled"] = False
    model["side_b_programming_enabled"] = False
    model["memory_programming_enabled"] = False

    model["updated_utc"] = utc_now_iso()

    return model


def stable_part(model: Dict[str, Any]) -> Dict[str, Any]:
    ignored = {
        "updated_utc",
        "detail",
    }
    return {k: v for k, v in model.items() if k not in ignored}


def read_existing(client: "redis.Redis") -> Optional[Dict[str, Any]]:
    try:
        raw = client.get(KEY_ADAPTER)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        warn(f"unable to read existing {KEY_ADAPTER}: {exc}")
        return None


def should_publish(
    new_model: Dict[str, Any],
    old_model: Optional[Dict[str, Any]],
    last_publish_mono: float,
    force_publish_seconds: int,
) -> Tuple[bool, str]:
    if old_model is None:
        return True, "initial_publish"

    if stable_part(new_model) != stable_part(old_model):
        return True, "model_changed"

    if time.monotonic() - last_publish_mono >= force_publish_seconds:
        return True, "force_publish"

    return False, "unchanged"


def publish(client: "redis.Redis", model: Dict[str, Any], reason: str) -> None:
    client.set(KEY_ADAPTER, json.dumps(model, sort_keys=True, separators=(",", ":")))

    event = {
        "type": "state.changed",
        "topic": "state.changed",
        "source": SOURCE,
        "keys": [KEY_ADAPTER],
        "changed_keys": [KEY_ADAPTER],
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now_iso(),
        "host": socket.gethostname(),
    }
    client.publish(BUS_SYSTEM, json.dumps(event, sort_keys=True, separators=(",", ":")))


def build_adapter_status(app: Dict[str, Any]) -> Dict[str, Any]:
    config = IC2730AConfig.from_app_config(app)
    adapter = IC2730AAdapter(config)
    return normalize_adapter_status(adapter.get_status())


def main() -> int:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    client: Optional["redis.Redis"] = None
    last_publish_mono = 0.0

    log("starting")

    while _running:
        interval_sec = DEFAULT_PUBLISH_INTERVAL_SECONDS

        try:
            app = load_app_config()
            publish_cfg = get_publish_config(app)

            interval_sec = int(publish_cfg["publish_interval_seconds"])
            force_publish_seconds = int(publish_cfg["force_publish_seconds"])

            if not publish_cfg["publish_adapter_status"]:
                log("publish_adapter_status=false; sleeping")
                slept = 0
                while _running and slept < interval_sec:
                    time.sleep(1)
                    slept += 1
                continue

            if client is None:
                client = get_redis_client(app)

            model = build_adapter_status(app)
            old_model = read_existing(client)

            do_publish, publish_reason = should_publish(
                model,
                old_model,
                last_publish_mono,
                force_publish_seconds,
            )

            if do_publish:
                publish(client, model, publish_reason)
                last_publish_mono = time.monotonic()
                log(
                    f"published status={model.get('status')} "
                    f"available={model.get('available')} "
                    f"mode={model.get('control_mode')} "
                    f"reason={model.get('reason')} "
                    f"publish_reason={publish_reason}"
                )

        except Exception as exc:
            client = None
            warn(f"cycle failed: {exc}")

        slept = 0
        while _running and slept < interval_sec:
            time.sleep(1)
            slept += 1

    log("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())