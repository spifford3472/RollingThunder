#!/usr/bin/env python3
"""
RollingThunder VHF Side B manager.

Phase 5 scope:
- Publish controller-owned desired Side B monitor state to Redis.
- Do not program the radio.
- Do not tune the radio.
- Do not scan.
- Do not read SQLite.
- Do not write rt:ui:bus.

Output:
  rt:vhf:side_b

Events:
  state.changed on rt:system:bus when rt:vhf:side_b changes
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

OUTPUT_KEY = "rt:vhf:side_b"
SYSTEM_BUS = "rt:system:bus"
SOURCE = "vhf_side_b_manager"

DEFAULT_ENABLED = True
DEFAULT_FREQUENCY_MHZ = 146.52
DEFAULT_MODE = "FM"
DEFAULT_LABEL = "2m Calling"
DEFAULT_RECEIVE_ONLY = False
DEFAULT_PUBLISH_INTERVAL_SECONDS = 30.0
DEFAULT_FORCE_PUBLISH_SECONDS = 300.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"{utc_now()} {SOURCE}: {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{utc_now()} {SOURCE}: WARN: {msg}", file=sys.stderr, flush=True)


def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            warn(f"missing config file: {path}")
            return {}
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        warn(f"config file is not a JSON object: {path}")
        return {}
    except Exception as exc:
        warn(f"could not read config {path}: {exc}")
        return {}


def deep_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    """
    Supports both flat and nested config styles.

    Examples:
      {"vhf.repeater_radius_miles": 25}
      {"vhf": {"repeater_radius_miles": 25}}
    """
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
        val = raw.strip().lower()
        if val in ("1", "true", "yes", "y", "on", "enabled"):
            return True
        if val in ("0", "false", "no", "n", "off", "disabled"):
            return False

    warn(f"invalid boolean config {dotted}={raw!r}; using {default!r}")
    return default


def cfg_float(config: Dict[str, Any], dotted: str, env_name: str, default: float, minimum: Optional[float] = None) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    try:
        value = float(raw)
        if minimum is not None and value < minimum:
            raise ValueError(f"value below minimum {minimum}")
        return value
    except Exception:
        warn(f"invalid numeric config {dotted}={raw!r}; using {default!r}")
        return default


def cfg_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    if raw is None:
        return default

    value = str(raw).strip()
    if not value:
        return default
    return value


class RedisCli:
    """
    Small Redis wrapper using redis-cli.

    This matches the Phase 2 repeater lookup style and avoids requiring
    Python redis module behavior to be consistent everywhere.

    Honors:
      REDIS_CLI
      REDIS_AUTH_ARGS
      REDIS_HOST / RT_REDIS_HOST
      REDIS_PORT / RT_REDIS_PORT
      REDIS_DB / RT_REDIS_DB
    """

    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args = [self.redis_cli]

        host = os.environ.get("RT_REDIS_HOST") or os.environ.get("REDIS_HOST")
        port = os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT")
        db = os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB")

        if host:
            self.base_args += ["-h", host]
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


def config_has_side_b(config: Dict[str, Any]) -> bool:
    vhf = config.get("vhf")
    if isinstance(vhf, dict) and isinstance(vhf.get("side_b"), dict):
        return True

    for key in (
        "vhf.side_b.enabled",
        "vhf.side_b.default_frequency_mhz",
        "vhf.side_b.default_mode",
        "vhf.side_b.label",
        "vhf.side_b.receive_only",
        "vhf.side_b.publish_interval_seconds",
    ):
        if key in config:
            return True

    return False


def build_model(config: Dict[str, Any]) -> tuple[Dict[str, Any], float]:
    has_side_b_config = config_has_side_b(config)

    enabled = cfg_bool(config, "vhf.side_b.enabled", "RT_VHF_SIDE_B_ENABLED", DEFAULT_ENABLED)
    frequency_mhz = cfg_float(
        config,
        "vhf.side_b.default_frequency_mhz",
        "RT_VHF_SIDE_B_DEFAULT_FREQUENCY_MHZ",
        DEFAULT_FREQUENCY_MHZ,
        minimum=0.0,
    )
    mode = cfg_str(config, "vhf.side_b.default_mode", "RT_VHF_SIDE_B_DEFAULT_MODE", DEFAULT_MODE).upper()
    label = cfg_str(config, "vhf.side_b.label", "RT_VHF_SIDE_B_LABEL", DEFAULT_LABEL)
    receive_only = cfg_bool(
        config,
        "vhf.side_b.receive_only",
        "RT_VHF_SIDE_B_RECEIVE_ONLY",
        DEFAULT_RECEIVE_ONLY,
    )
    interval = cfg_float(
        config,
        "vhf.side_b.publish_interval_seconds",
        "RT_VHF_SIDE_B_PUBLISH_INTERVAL_SECONDS",
        DEFAULT_PUBLISH_INTERVAL_SECONDS,
        minimum=1.0,
    )

    if not enabled:
        status = "unavailable"
        reason = "Side B monitor target is disabled in config."
    elif has_side_b_config:
        status = "pending"
        reason = "Default Side B monitor target configured; radio control path not active in this phase."
    else:
        status = "pending"
        reason = "Default Side B monitor target configured from built-in defaults; radio control path not active in this phase."

    model = {
        "status": status,
        "label": label,
        "frequency_mhz": round(float(frequency_mhz), 5),
        "mode": mode,
        "receive_only": bool(receive_only),
        "source": SOURCE,
        "reason": reason,
        "updated_utc": utc_now(),
    }

    return model, interval


def comparable_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ignore updated_utc when deciding if semantic content changed.
    This prevents unnecessary writes and projector churn.
    """
    reduced = dict(model)
    reduced.pop("updated_utc", None)
    return reduced


def decode_existing(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def publish_model(redis: RedisCli, model: Dict[str, Any]) -> None:
    payload = json.dumps(model, separators=(",", ":"), sort_keys=True)
    redis.set(OUTPUT_KEY, payload)

    event = {
        "type": "state.changed",
        "source": SOURCE,
        "keys": [OUTPUT_KEY],
        "changed_keys": [OUTPUT_KEY],
        "updated_utc": model.get("updated_utc") or utc_now(),
    }
    redis.publish_json(SYSTEM_BUS, event)


def main() -> int:
    log("starting")
    redis = RedisCli()
    last_force_publish = 0.0

    while True:
        try:
            config = load_config()
            model, interval = build_model(config)

            previous_raw = redis.get(OUTPUT_KEY)
            previous_model = decode_existing(previous_raw)

            now_monotonic = time.monotonic()
            force_due = (now_monotonic - last_force_publish) >= DEFAULT_FORCE_PUBLISH_SECONDS

            changed = previous_model is None or comparable_model(previous_model) != comparable_model(model)

            if changed or force_due:
                publish_model(redis, model)
                last_force_publish = now_monotonic
                if changed:
                    log(f"published {OUTPUT_KEY}: status={model['status']} frequency_mhz={model['frequency_mhz']} mode={model['mode']}")
                else:
                    log(f"heartbeat published {OUTPUT_KEY}: status={model['status']}")

            time.sleep(interval)

        except KeyboardInterrupt:
            log("stopping")
            return 0
        except Exception as exc:
            warn(f"loop error: {exc}")
            time.sleep(5.0)


if __name__ == "__main__":
    raise SystemExit(main())