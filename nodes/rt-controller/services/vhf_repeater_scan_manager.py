#!/usr/bin/env python3
"""
RollingThunder VHF repeater scan manager.

Phase 6 scope:
- Own and publish controller-side VHF repeater scan state.
- Read requested scan state from rt:vhf:scan:request.
- Read VHF radio availability from rt:vhf:radio.
- Read nearby repeater model from rt:vhf:repeaters:nearby.
- Publish rt:vhf:scan.
- Publish state.changed to rt:system:bus when rt:vhf:scan changes.

Does NOT:
- write rt:ui:bus
- read SQLite
- calculate repeater distance
- filter repeaters by distance
- program radio memories
- choose memory channels
- start or stop scanning
- call serial, Hamlib, CI-V, rigctld, or radio APIs
- expose transmit or PTT controls
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

SOURCE = "vhf_repeater_scan_manager"

KEY_SCAN = "rt:vhf:scan"
KEY_SCAN_REQUEST = "rt:vhf:scan:request"
KEY_VHF_RADIO = "rt:vhf:radio"
KEY_VHF_ADAPTER = "rt:vhf:adapter"
KEY_NEARBY = "rt:vhf:repeaters:nearby"

SYSTEM_BUS = "rt:system:bus"

DEFAULT_ENABLED = False
DEFAULT_RADIUS_MILES = 25.0
DEFAULT_RELOAD_DISTANCE_MILES = 20.0
DEFAULT_PUBLISH_INTERVAL_SECONDS = 30.0
DEFAULT_FORCE_PUBLISH_SECONDS = 300.0
DEFAULT_NEXT_GROUP = "C"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"{utc_now()} {SOURCE}: {message}", flush=True)


def warn(message: str) -> None:
    print(f"{utc_now()} {SOURCE}: WARN: {message}", file=sys.stderr, flush=True)


def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            warn(f"missing config file: {path}")
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        warn(f"config file is not a JSON object: {path}")
    except Exception as exc:
        warn(f"could not read config {path}: {exc}")
    return {}


def deep_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    if dotted in config:
        return config.get(dotted, default)

    cur: Any = config
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def cfg_bool(config: Dict[str, Any], dotted: str, env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    if isinstance(raw, bool):
        return raw

    if isinstance(raw, (int, float)):
        return bool(raw)

    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if value in {"0", "false", "no", "n", "off", "disabled"}:
            return False

    warn(f"invalid boolean config {dotted}={raw!r}; using {default!r}")
    return default


def cfg_float(
    config: Dict[str, Any],
    dotted: str,
    env_name: str,
    default: float,
    minimum: Optional[float] = None,
) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("not finite")
        if minimum is not None and value < minimum:
            raise ValueError(f"value below minimum {minimum}")
        return value
    except Exception:
        warn(f"invalid numeric config {dotted}={raw!r}; using {default!r}")
        return float(default)


def cfg_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    if raw is None:
        return default

    value = str(raw).strip()
    return value or default


class RedisCli:
    """
    Small Redis wrapper using redis-cli.

    Honors:
      REDIS_CLI
      REDIS_AUTH_ARGS
      RT_REDIS_HOST / REDIS_HOST
      RT_REDIS_PORT / REDIS_PORT
      RT_REDIS_DB / REDIS_DB
    """

    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args = [self.redis_cli]

        host = os.environ.get("RT_REDIS_HOST") or os.environ.get("REDIS_HOST")
        port = os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT")
        db = os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB")

        if host:
            self.base_args += ["-h", str(host)]
        if port:
            self.base_args += ["-p", str(port)]
        if db:
            self.base_args += ["-n", str(db)]

        auth_args = os.environ.get("REDIS_AUTH_ARGS", "").strip()
        if auth_args:
            self.base_args += auth_args.split()

    def _run(self, args: list[str], input_text: Optional[str] = None) -> str:
        proc = subprocess.run(
            self.base_args + args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"redis-cli failed rc={proc.returncode}: {proc.stderr.strip()}")
        return proc.stdout

    def get(self, key: str) -> Optional[str]:
        out = self._run(["--raw", "GET", key])
        if out == "":
            return None
        return out.rstrip("\n")

    def set(self, key: str, value: str) -> None:
        self._run(["SET", key, value])

    def publish_json(self, channel: str, payload: Dict[str, Any]) -> None:
        self._run(["PUBLISH", channel, json.dumps(payload, separators=(",", ":"), sort_keys=True)])


def load_config() -> Dict[str, Any]:
    return load_json_file(APP_CONFIG_PATH)


def load_json_model(redis_client: RedisCli, key: str) -> Dict[str, Any]:
    try:
        raw = redis_client.get(key)
        if not raw:
            return {}
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        warn(f"could not read {key}: {exc}")
    return {}


def parse_requested(request_model: Dict[str, Any], config: Dict[str, Any]) -> bool:
    if "requested" in request_model:
        return boolish(request_model.get("requested"), DEFAULT_ENABLED)
    if "enabled" in request_model:
        return boolish(request_model.get("enabled"), DEFAULT_ENABLED)
    return cfg_bool(config, "vhf.scan.enabled_default", "RT_VHF_SCAN_ENABLED_DEFAULT", DEFAULT_ENABLED)


def boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        val = value.strip().lower()
        if val in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if val in {"0", "false", "no", "n", "off", "disabled"}:
            return False

    return default


def count_nearby(nearby: Dict[str, Any]) -> int:
    for key in ("count", "eligible_count", "total"):
        try:
            value = int(nearby.get(key))
            if value >= 0:
                return value
        except Exception:
            pass

    for key in ("items", "repeaters", "rows", "nearby"):
        value = nearby.get(key)
        if isinstance(value, list):
            return len(value)

    return 0


def nearby_available(nearby: Dict[str, Any]) -> bool:
    if not nearby:
        return False

    status = str(nearby.get("status", "")).strip().lower()
    if status not in {"ok", "ready", "available"}:
        return False

    gps_status = str(nearby.get("gps_status", "")).strip().lower()
    if gps_status and gps_status not in {"ok", "valid", "ready"}:
        return False

    return True


def vhf_radio_available(radio: Dict[str, Any]) -> bool:
    if not radio:
        return False

    if radio.get("available") is True:
        return True

    status = str(radio.get("status", "")).strip().lower()
    if status in {"online", "available", "ready"}:
        return True

    return False

def adapter_status(adapter: Dict[str, Any]) -> str:
    return str(adapter.get("status", "")).strip().lower()


def adapter_available(adapter: Dict[str, Any]) -> bool:
    if not adapter:
        return False

    if adapter.get("available") is True:
        return True

    status = adapter_status(adapter)
    if status in {"dry_run", "detected", "available", "ready"}:
        return True

    return False


def adapter_in_dry_run(adapter: Dict[str, Any]) -> bool:
    return adapter_status(adapter) == "dry_run"


def adapter_detected(adapter: Dict[str, Any]) -> bool:
    return adapter_status(adapter) in {"detected", "available", "ready"}


def adapter_memory_programming_enabled(adapter: Dict[str, Any]) -> bool:
    return boolish(adapter.get("memory_programming_enabled"), False)


def adapter_scan_control_enabled(adapter: Dict[str, Any]) -> bool:
    return boolish(adapter.get("scan_control_enabled"), False)

def build_model(config: Dict[str, Any], request: Dict[str, Any], adapter: Dict[str, Any], nearby: Dict[str, Any]) -> Dict[str, Any]:
    requested = parse_requested(request, config)

    radius_miles = cfg_float(
        config,
        "vhf.repeater_radius_miles",
        "RT_VHF_REPEATER_RADIUS_MILES",
        DEFAULT_RADIUS_MILES,
        minimum=0.0,
    )

    reload_distance_miles = cfg_float(
        config,
        "vhf.scan.reload_distance_miles",
        "RT_VHF_SCAN_RELOAD_DISTANCE_MILES",
        DEFAULT_RELOAD_DISTANCE_MILES,
        minimum=0.0,
    )

    next_group = cfg_str(
        config,
        "vhf.scan.next_group",
        "RT_VHF_SCAN_NEXT_GROUP",
        DEFAULT_NEXT_GROUP,
    ).upper()

    nearby_count = count_nearby(nearby)
    eligible_count = nearby_count

    adapter_status_value = adapter_status(adapter)
    adapter_reason = str(adapter.get("reason", "")).strip()

    if not requested:
        status = "disabled"
        reason = "Repeater scanning disabled."
        actual_scan_state = "not_scanning"
        enabled = False

    elif not adapter:
        status = "unavailable"
        reason = "IC-2730A adapter status unavailable."
        actual_scan_state = "unknown"
        enabled = False

    elif not adapter_available(adapter):
        status = "unavailable"
        reason = adapter_reason or "IC-2730A adapter/control path unavailable."
        actual_scan_state = "unknown"
        enabled = False

    elif adapter_in_dry_run(adapter):
        status = "dry_run"
        reason = "IC-2730A adapter is in dry-run mode; no radio programming or scan start performed."
        actual_scan_state = "not_scanning"
        enabled = False

    elif adapter_detected(adapter) and (
        not adapter_memory_programming_enabled(adapter)
        or not adapter_scan_control_enabled(adapter)
    ):
        status = "pending"
        reason = "IC-2730A detected, but memory programming and scan control are disabled."
        actual_scan_state = "unknown"
        enabled = False

    elif not nearby_available(nearby):
        status = "unavailable"
        reason = "Nearby repeater model unavailable."
        actual_scan_state = "unknown"
        enabled = False

    else:
        status = "pending"
        reason = "Repeater scanning requested; Phase 7B does not start scans or program memories."
        actual_scan_state = "unknown"
        enabled = False

    return {
        "enabled": enabled,
        "requested": requested,
        "actual_scan_state": actual_scan_state,
        "status": status,
        "reason": reason,
        "active_group": None,
        "next_group": next_group,
        "radius_miles": int(radius_miles) if radius_miles.is_integer() else round(radius_miles, 2),
        "reload_distance_miles": int(reload_distance_miles) if reload_distance_miles.is_integer() else round(reload_distance_miles, 2),
        "distance_since_reload_miles": 0,
        "nearby_count": nearby_count,
        "eligible_count": eligible_count,
        "adapter_status": adapter_status_value or None,
        "adapter_available": bool(adapter_available(adapter)),
        "adapter_control_mode": adapter.get("control_mode") if isinstance(adapter, dict) else None,
        "adapter_writes_enabled": boolish(adapter.get("writes_enabled"), False) if isinstance(adapter, dict) else False,
        "adapter_memory_programming_enabled": boolish(adapter.get("memory_programming_enabled"), False) if isinstance(adapter, dict) else False,
        "adapter_scan_control_enabled": boolish(adapter.get("scan_control_enabled"), False) if isinstance(adapter, dict) else False,
        "source": SOURCE,
        "updated_utc": utc_now(),
    }


def comparable_model(model: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in model.items() if k != "updated_utc"}


def publish_state_changed(redis_client: RedisCli, reason: str) -> None:
    event = {
        "topic": "state.changed",
        "type": "state.changed",
        "source": SOURCE,
        "keys": [KEY_SCAN],
        "changed_keys": [KEY_SCAN],
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now(),
        "host": socket.gethostname(),
    }
    redis_client.publish_json(SYSTEM_BUS, event)


def publish_if_changed(redis_client: RedisCli, model: Dict[str, Any], force: bool = False) -> bool:
    old = load_json_model(redis_client, KEY_SCAN)

    if not force and comparable_model(old) == comparable_model(model):
        return False

    redis_client.set(KEY_SCAN, json.dumps(model, separators=(",", ":"), sort_keys=True))
    publish_state_changed(redis_client, "scan_model_changed" if not force else "scan_model_heartbeat")
    return True


def main() -> int:
    log("starting")

    redis_client: Optional[RedisCli] = None
    last_force_publish = 0.0

    while True:
        interval = DEFAULT_PUBLISH_INTERVAL_SECONDS

        try:
            config = load_config()

            interval = cfg_float(
                config,
                "vhf.scan.publish_interval_seconds",
                "RT_VHF_SCAN_PUBLISH_INTERVAL_SECONDS",
                DEFAULT_PUBLISH_INTERVAL_SECONDS,
                minimum=1.0,
            )

            force_publish_seconds = cfg_float(
                config,
                "vhf.scan.force_publish_seconds",
                "RT_VHF_SCAN_FORCE_PUBLISH_SECONDS",
                DEFAULT_FORCE_PUBLISH_SECONDS,
                minimum=30.0,
            )

            if redis_client is None:
                redis_client = RedisCli()

            request = load_json_model(redis_client, KEY_SCAN_REQUEST)
            adapter = load_json_model(redis_client, KEY_VHF_ADAPTER)
            nearby = load_json_model(redis_client, KEY_NEARBY)

            model = build_model(config, request, adapter, nearby)

            force = (time.monotonic() - last_force_publish) >= force_publish_seconds
            wrote = publish_if_changed(redis_client, model, force=force)

            if wrote:
                last_force_publish = time.monotonic()
                log(
                    f"published requested={model['requested']} "
                    f"enabled={model['enabled']} "
                    f"status={model['status']} "
                    f"actual_scan_state={model['actual_scan_state']} "
                    f"nearby_count={model['nearby_count']}"
                )

        except KeyboardInterrupt:
            log("stopping")
            return 0
        except Exception as exc:
            redis_client = None
            warn(f"cycle failed: {exc}")

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())