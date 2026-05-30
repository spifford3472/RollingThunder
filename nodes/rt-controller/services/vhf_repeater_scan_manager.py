#!/usr/bin/env python3
"""
RollingThunder VHF repeater scan manager.

Phase 8A scope:
- Own and publish controller-side VHF repeater scan state.
- Read requested scan state from rt:vhf:scan:request.
- Read IC-2730A adapter state from rt:vhf:adapter.
- Read nearby repeater model from rt:vhf:repeaters:nearby.
- Read GPS position from rt:gps:pos for controller-side movement tracking.
- Publish rt:vhf:scan.
- Publish rt:vhf:repeaters:planned_memory in dry-run only.
- Publish state.changed to rt:system:bus when state changes.

Safety boundary:
- This service never imports or calls radio adapter code.
- This service never opens serial devices.
- This service never shells out to radio-control utilities.
- This service never clears, writes, selects, starts, stops, transmits, or keys anything.
- The UI remains a renderer-only dumb terminal.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

SOURCE = "vhf_repeater_scan_manager"

KEY_SCAN = "rt:vhf:scan"
KEY_SCAN_REQUEST = "rt:vhf:scan:request"
KEY_VHF_RADIO = "rt:vhf:radio"
KEY_NEARBY = "rt:vhf:repeaters:nearby"
KEY_GPS_POS = "rt:gps:pos"
KEY_PLANNED_MEMORY = "rt:vhf:repeaters:planned_memory"
KEY_ADAPTER_REQUEST = "rt:vhf:adapter:request"

SYSTEM_BUS = "rt:system:bus"

DEFAULT_ENABLED = False
DEFAULT_RADIUS_MILES = 40.0
DEFAULT_RELOAD_DISTANCE_MILES = 20.0
DEFAULT_MOVEMENT_MIN_DELTA_MILES = 0.05
DEFAULT_MOVEMENT_JUMP_REJECT_MILES = 5.0
DEFAULT_MAX_MEMORY_CHANNELS_PER_GROUP = 50
DEFAULT_DRY_RUN_RELOAD_ONLY = True
DEFAULT_PUBLISH_INTERVAL_SECONDS = 30.0
DEFAULT_FORCE_PUBLISH_SECONDS = 300.0
DEFAULT_NEXT_GROUP = "C"
VALID_GROUPS = {"C", "D"}

def radio_status(radio: Dict[str, Any]) -> str:
    return str(radio.get("status", "")).strip().lower()


def radio_available(radio: Dict[str, Any]) -> bool:
    if not radio:
        return False
    status = radio_status(radio)
    return boolish(radio.get("available"), False) and status in {"available", "ready"}


def radio_in_dry_run(radio: Dict[str, Any]) -> bool:
    return radio_status(radio) == "dry_run"


def radio_disabled(radio: Dict[str, Any]) -> bool:
    return radio_status(radio) == "disabled"

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


def cfg_bool(config: Dict[str, Any], dotted: str, env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    return boolish(raw, default)


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


def cfg_int(
    config: Dict[str, Any],
    dotted: str,
    env_name: str,
    default: int,
    minimum: Optional[int] = None,
) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    try:
        value = int(raw)
        if minimum is not None and value < minimum:
            raise ValueError(f"value below minimum {minimum}")
        return value
    except Exception:
        warn(f"invalid integer config {dotted}={raw!r}; using {default!r}")
        return int(default)


def cfg_str(config: Dict[str, Any], dotted: str, env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)
    if raw is None:
        return default
    value = str(raw).strip()
    return value or default


def rounded_miles(value: float) -> float:
    return round(float(value), 3)


def display_number(value: float) -> float | int:
    v = float(value)
    return int(v) if v.is_integer() else round(v, 3)


class RedisCli:
    """Small Redis wrapper using redis-cli, following existing project style."""

    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args: List[str] = [self.redis_cli]

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
            self.base_args += shlex.split(auth_args)

    def _run(self, args: Sequence[str], input_text: Optional[str] = None) -> str:
        proc = subprocess.run(
            self.base_args + list(args),
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

    def hgetall(self, key: str) -> Dict[str, str]:
        out = self._run(["--raw", "HGETALL", key])
        lines = out.splitlines()
        result: Dict[str, str] = {}
        for i in range(0, len(lines) - 1, 2):
            result[lines[i]] = lines[i + 1]
        return result

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


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


def first_present_float(data: Dict[str, Any], names: Iterable[str]) -> Optional[float]:
    lower = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            val = parse_float(data.get(name))
            if val is not None:
                return val
        lname = name.lower()
        if lname in lower:
            val = parse_float(lower.get(lname))
            if val is not None:
                return val
    return None


def read_gps(redis_client: RedisCli) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read rt:gps:pos as either a Redis hash or JSON string."""
    gps: Dict[str, Any] = {}

    try:
        gps = dict(redis_client.hgetall(KEY_GPS_POS))
    except Exception:
        gps = {}

    if not gps:
        raw_model = load_json_model(redis_client, KEY_GPS_POS)
        gps = raw_model if isinstance(raw_model, dict) else {}

    if not gps:
        return None, "missing_gps"

    status = str(gps.get("status") or gps.get("gps_status") or gps.get("fix_status") or "").strip().lower()
    if status and status not in {"ok", "valid", "ready", "fix", "fixed", "3d", "2d"}:
        return None, "invalid_gps"

    fix = str(gps.get("fix") or gps.get("mode") or gps.get("quality") or "").strip().lower()
    if fix in {"0", "none", "no", "nofix", "invalid"}:
        return None, "invalid_gps"

    lat = first_present_float(
        gps,
        (
            "lat",
            "latitude",
            "gps_lat",
            "gps_latitude",
            "fix_lat",
            "position_lat",
        ),
    )
    lon = first_present_float(
        gps,
        (
            "lon",
            "lng",
            "longitude",
            "gps_lon",
            "gps_lng",
            "gps_longitude",
            "fix_lon",
            "position_lon",
        ),
    )

    if lat is None or lon is None:
        return None, "missing_gps"

    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return None, "invalid_gps"

    timestamp = str(
        gps.get("updated_utc")
        or gps.get("timestamp_utc")
        or gps.get("utc")
        or gps.get("time_utc")
        or utc_now()
    )

    return {"lat": round(lat, 6), "lon": round(lon, 6), "updated_utc": timestamp}, "ok"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * radius_miles * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


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


def nearby_items(nearby: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "repeaters", "rows", "nearby"):
        value = nearby.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


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


def adapter_status(adapter: Dict[str, Any]) -> str:
    return str(adapter.get("status", "")).strip().lower()


def adapter_available(adapter: Dict[str, Any]) -> bool:
    if not adapter:
        return False
    if adapter.get("available") is True:
        return True
    status = adapter_status(adapter)
    return status in {"dry_run", "detected", "available", "ready"}


def adapter_in_dry_run(adapter: Dict[str, Any]) -> bool:
    return adapter_status(adapter) == "dry_run" or str(adapter.get("control_mode", "")).strip().lower() == "dry_run"


def adapter_detected(adapter: Dict[str, Any]) -> bool:
    return adapter_status(adapter) in {"detected", "available", "ready"}


def adapter_memory_programming_enabled(adapter: Dict[str, Any]) -> bool:
    return boolish(adapter.get("memory_programming_enabled"), False)


def adapter_scan_control_enabled(adapter: Dict[str, Any]) -> bool:
    return boolish(adapter.get("scan_control_enabled"), False)


def adapter_writes_enabled(adapter: Dict[str, Any]) -> bool:
    return boolish(adapter.get("writes_enabled"), False)


def normalize_group(value: Any, default: str = DEFAULT_NEXT_GROUP) -> str:
    text = str(value or default).strip().upper()
    return text if text in VALID_GROUPS else default


def other_group(group: str) -> str:
    return "D" if group == "C" else "C"

def group_memory_start(group: str) -> int:
    """IC-2730A C/D memory-bank absolute channel start."""
    group = normalize_group(group)
    return 100 if group == "C" else 150


def group_memory_range(group: str) -> Tuple[int, int]:
    start = group_memory_start(group)
    return start, start + 49


def normalize_repeater_duplex(item: Dict[str, Any], offset_mhz: Optional[float]) -> str:
    raw = str(item.get("duplex") or item.get("offset_direction") or "").strip().lower()

    if raw in {"+", "plus", "positive", "up"}:
        return "+"
    if raw in {"-", "minus", "negative", "down"}:
        return "-"
    if raw in {"simplex", "off", "none", "0", "0.0"}:
        return "simplex"

    if offset_mhz is not None:
        if offset_mhz > 0:
            return "+"
        if offset_mhz < 0:
            return "-"

    return "simplex"

def normalize_repeater_tone_mode(item: Dict[str, Any], tone_hz: Optional[float]) -> str:
    raw = str(
        item.get("tone_mode")
        or item.get("tone_type")
        or item.get("ctcss_mode")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")

    if raw in {"", "none", "off", "no", "false", "0"}:
        return "tone" if tone_hz is not None else "off"

    if raw in {
        "tone",
        "encode",
        "encode_only",
        "tx_tone",
        "tone_encode",
        "ctcss_encode",
        "repeater_tone",
    }:
        return "tone"

    if raw in {
        "ctcss",
    }:
        return "ctcss"

    if raw in {
        "tsql",
        "tone_sql",
        "tone_squelch",
        "tonesquelch",
        "decode",
        "encode_decode",
        "encode_and_decode",
        "encode_and_tsql",
        "tone_and_tsql",
        "ctcss_sql",
        "ctcss_squelch",
    }:
        return "tsql"

    return "tone" if tone_hz is not None else "off"

def source_item_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("id", "source_id", "repeater_id", "station_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None

def numeric_tone(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper in {"NONE", "OFF", "N/A", "NA", "NULL", "-"}:
        return None
    val = parse_float(text.replace("Hz", "").replace("HZ", "").strip())
    if val is None or val <= 0:
        return None
    return round(val, 1)


def item_frequency(item: Dict[str, Any]) -> Optional[float]:
    for key in ("rx_frequency_mhz", "frequency_mhz", "output_mhz", "receive_frequency_mhz"):
        val = parse_float(item.get(key))
        if val is not None and 100.0 <= val <= 999.999:
            return round(val, 6)
    return None


def item_tx_frequency(item: Dict[str, Any]) -> Optional[float]:
    for key in ("tx_frequency_mhz", "input_mhz", "transmit_frequency_mhz"):
        val = parse_float(item.get(key))
        if val is not None and 100.0 <= val <= 999.999:
            return round(val, 6)
    return None


def is_transmit_prohibited(item: Dict[str, Any]) -> bool:
    for key in ("transmit_prohibited", "tx_prohibited", "receive_only", "rx_only", "no_tx"):
        if key in item and boolish(item.get(key), False):
            return True
    text = " ".join(str(item.get(k, "")) for k in ("special", "notes", "tags", "category")).lower()
    return any(token in text for token in ("receive only", "rx only", "no transmit", "tx prohibited"))


def is_analog_fm(item: Dict[str, Any]) -> bool:
    mode = str(item.get("mode") or item.get("fm_mode") or "FM").strip().upper()
    if not mode:
        return True
    if mode in {"FM", "NFM", "WFM"}:
        return True
    text = " ".join(str(item.get(k, "")) for k in ("mode", "type", "special", "notes", "tags", "category")).lower()
    digital_markers = ("dmr", "d-star", "dstar", "fusion", "ysf", "p25", "nxdn", "m17", "aprs")
    return not any(marker in text for marker in digital_markers)


def repeater_name(item: Dict[str, Any]) -> str:
    for key in ("channel_name", "name", "callsign", "call_sign", "repeater_name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:32]
    return "REPEATER"


def repeater_callsign(item: Dict[str, Any]) -> str:
    for key in ("callsign", "call_sign", "station", "owner_call"):
        value = str(item.get(key) or "").strip().upper()
        if value:
            return value[:16]
    return ""


def build_planned_memory_model(
    nearby: Dict[str, Any],
    target_group: str,
    source_group: Optional[str],
    radius_miles: float,
    max_memory_channels: int,
) -> Dict[str, Any]:
    target_group = normalize_group(target_group)
    memory_start, memory_end = group_memory_range(target_group)
    max_channels = max(1, min(int(max_memory_channels), memory_end - memory_start + 1))

    items: List[Dict[str, Any]] = []
    skipped_invalid = 0
    skipped_digital = 0
    skipped_tx_prohibited = 0

    for raw_item in nearby_items(nearby):
        freq = item_frequency(raw_item)
        if freq is None:
            skipped_invalid += 1
            continue

        distance = parse_float(raw_item.get("distance_miles"))
        if distance is not None and distance > radius_miles:
            continue

        if not is_analog_fm(raw_item):
            skipped_digital += 1
            continue

        if is_transmit_prohibited(raw_item):
            skipped_tx_prohibited += 1
            continue

        tx_freq = item_tx_frequency(raw_item)
        offset: Optional[float] = None
        if tx_freq is not None:
            offset = abs(round(tx_freq - freq, 6))
        else:
            offset_val = parse_float(raw_item.get("offset_mhz"))
            if offset_val is not None:
                offset = abs(round(offset_val, 6))

        tone = numeric_tone(raw_item.get("tx_tone"))
        if tone is None:
            tone = numeric_tone(raw_item.get("rx_tone"))
        if tone is None:
            tone = numeric_tone(raw_item.get("tone_hz"))

        tone_mode = normalize_repeater_tone_mode(raw_item, tone)

        channel_index = len(items)
        memory_channel = memory_start + channel_index

        planned: Dict[str, Any] = {
            "channel_index": channel_index,
            "channel": channel_index + 1,
            "memory_channel": memory_channel,
            "name": repeater_name(raw_item),
            "callsign": repeater_callsign(raw_item),
            "frequency_mhz": freq,
            "mode": "FM",
            "duplex": normalize_repeater_duplex(raw_item, offset),
            "offset_mhz": offset,
            "tone_hz": tone,
            "tone_mode": tone_mode,
            "distance_miles": round(distance, 1) if distance is not None else None,
            "bearing_degrees": raw_item.get("bearing_degrees"),
            "skywarn": boolish(raw_item.get("skywarn"), False),
            "ares": boolish(raw_item.get("ares"), False),
            "selected": boolish(raw_item.get("selected"), channel_index == 0),
        }

        item_id = source_item_id(raw_item)
        if item_id is not None:
            planned["source_id"] = item_id

        for key in ("repeater_id", "state", "special", "type"):
            if key in raw_item and raw_item.get(key) not in (None, ""):
                planned[key] = raw_item.get(key)

        items.append(planned)
        if len(items) >= max_channels:
            break

    now = utc_now()
    status = "dry_run" if items else "unavailable"
    reason = (
        "Memory reload plan generated; no radio programming performed."
        if items
        else "No eligible analog FM repeaters available for memory reload plan."
    )

    return {
        "status": status,
        "target_group": target_group,
        "source_group": source_group,
        "memory_channel_start": memory_start,
        "memory_channel_end": memory_end,
        "repeater_count": count_nearby(nearby),
        "memory_count": len(items),
        "radius_miles": display_number(radius_miles),
        "nearby_model_radius_miles": nearby.get("radius_miles"),
        "max_memory_channels": max_channels,
        "skipped_invalid_frequency": skipped_invalid,
        "skipped_non_fm": skipped_digital,
        "skipped_transmit_prohibited": skipped_tx_prohibited,
        "operation_performed": False,
        "reason": reason,
        "items": items,
        "repeaters": items,
        "source": SOURCE,
        "updated_utc": now,
    }

def build_adapter_cd_reload_plan_request(
    planned_memory: Dict[str, Any],
    state: Dict[str, Any],
    *,
    radius_miles: float,
    reload_distance_miles: float,
) -> Dict[str, Any]:
    target_group = normalize_group(planned_memory.get("target_group"))
    last_movement_utc = str(state.get("last_movement_utc") or "unknown").replace(":", "").replace("-", "")
    request_id = f"vhf-cd-reload-plan-{target_group}-{last_movement_utc}"

    origin: Dict[str, Any] = {}
    last_location = state.get("last_movement_location")
    if isinstance(last_location, dict):
        origin = {
            "lat": last_location.get("lat"),
            "lon": last_location.get("lon"),
            "updated_utc": state.get("last_movement_utc"),
        }

    return {
        "request_id": request_id,
        "action": "plan_cd_bank_reload",
        "source": SOURCE,
        "target_group": target_group,
        "inactive_group": target_group,
        "source_group": planned_memory.get("source_group"),
        "start_scan_after": False,
        "dry_run": True,
        "operation_requested": "plan_only",
        "operation_performed": False,
        "reason": "reload_distance_reached_plan_only",
        "origin": origin,
        "reload": {
            "radius_miles": display_number(radius_miles),
            "reload_distance_miles": display_number(reload_distance_miles),
            "distance_since_reload_miles": rounded_miles(
                parse_float(state.get("distance_since_reload_miles")) or 0.0
            ),
            "max_channels": planned_memory.get("max_memory_channels"),
            "memory_channel_start": planned_memory.get("memory_channel_start"),
            "memory_channel_end": planned_memory.get("memory_channel_end"),
        },
        "repeaters": planned_memory.get("items") if isinstance(planned_memory.get("items"), list) else [],
    }

def initial_state_from_previous(previous: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    configured_next = cfg_str(config, "vhf.scan.next_group", "RT_VHF_SCAN_NEXT_GROUP", DEFAULT_NEXT_GROUP)
    return {
        "active_group": previous.get("active_group"),
        "next_group": normalize_group(previous.get("next_group") or configured_next),
        "distance_since_reload_miles": parse_float(previous.get("distance_since_reload_miles")) or 0.0,
        "last_movement_location": previous.get("last_movement_location"),
        "last_movement_utc": previous.get("last_movement_utc"),
        "last_reload_utc": previous.get("last_reload_utc"),
        "last_reload_location": previous.get("last_reload_location"),
        "last_successful_group": previous.get("last_successful_group"),
        "last_reload_status": previous.get("last_reload_status"),
        "last_reload_reason": previous.get("last_reload_reason"),
    }


def apply_movement_tracking(
    redis_client: RedisCli,
    state: Dict[str, Any],
    movement_min_delta_miles: float,
    movement_jump_reject_miles: float,
) -> Tuple[Dict[str, Any], str]:
    gps, gps_status = read_gps(redis_client)
    if gps_status != "ok" or gps is None:
        return state, gps_status

    last = state.get("last_movement_location")
    if not isinstance(last, dict):
        state["last_movement_location"] = {"lat": gps["lat"], "lon": gps["lon"]}
        state["last_movement_utc"] = gps.get("updated_utc") or utc_now()
        return state, "ok_initialized"

    old_lat = parse_float(last.get("lat"))
    old_lon = parse_float(last.get("lon"))
    if old_lat is None or old_lon is None:
        state["last_movement_location"] = {"lat": gps["lat"], "lon": gps["lon"]}
        state["last_movement_utc"] = gps.get("updated_utc") or utc_now()
        return state, "ok_initialized"

    delta = haversine_miles(old_lat, old_lon, float(gps["lat"]), float(gps["lon"]))
    if delta < movement_min_delta_miles:
        return state, "ok_ignored_jitter"

    if delta > movement_jump_reject_miles:
        state["last_movement_location"] = {"lat": gps["lat"], "lon": gps["lon"]}
        state["last_movement_utc"] = gps.get("updated_utc") or utc_now()
        return state, "ok_rejected_jump"

    state["distance_since_reload_miles"] = rounded_miles(
        (parse_float(state.get("distance_since_reload_miles")) or 0.0) + delta
    )
    state["last_movement_location"] = {"lat": gps["lat"], "lon": gps["lon"]}
    state["last_movement_utc"] = gps.get("updated_utc") or utc_now()
    return state, "ok_added"


def build_model(
    redis_client: RedisCli,
    config: Dict[str, Any],
    request: Dict[str, Any],
    radio: Dict[str, Any],
    nearby: Dict[str, Any],
    previous: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    requested = parse_requested(request, config)

    radius_miles = cfg_float(
        config,
        "vhf.scan.reload_radius_miles",
        "RT_VHF_SCAN_RELOAD_RADIUS_MILES",
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
    movement_min_delta_miles = cfg_float(
        config,
        "vhf.scan.movement_min_delta_miles",
        "RT_VHF_SCAN_MOVEMENT_MIN_DELTA_MILES",
        DEFAULT_MOVEMENT_MIN_DELTA_MILES,
        minimum=0.0,
    )
    movement_jump_reject_miles = cfg_float(
        config,
        "vhf.scan.movement_jump_reject_miles",
        "RT_VHF_SCAN_MOVEMENT_JUMP_REJECT_MILES",
        DEFAULT_MOVEMENT_JUMP_REJECT_MILES,
        minimum=0.0,
    )
    max_memory_channels = cfg_int(
        config,
        "vhf.scan.max_memory_channels_per_group",
        "RT_VHF_SCAN_MAX_MEMORY_CHANNELS_PER_GROUP",
        DEFAULT_MAX_MEMORY_CHANNELS_PER_GROUP,
        minimum=1,
    )
    dry_run_reload_only = cfg_bool(
        config,
        "vhf.scan.dry_run_reload_only",
        "RT_VHF_SCAN_DRY_RUN_RELOAD_ONLY",
        DEFAULT_DRY_RUN_RELOAD_ONLY,
    )

    state = initial_state_from_previous(previous, config)
    planned_memory: Optional[Dict[str, Any]] = None
    adapter_request: Optional[Dict[str, Any]] = None
    movement_status: Optional[str] = None

    nearby_count = count_nearby(nearby)
    eligible_count = nearby_count
    radio_status_value = radio_status(radio)
    radio_reason = str(radio.get("reason", "")).strip()

    reload_pending = False
    reload_in_progress = False

    if not requested:
        status = "disabled"
        reason = "Repeater scanning disabled."
        actual_scan_state = "not_scanning"
        enabled = False
        reload_pending = False
        reload_in_progress = False

    elif not radio:
        status = "unavailable"
        reason = "VHF radio availability model unavailable."
        actual_scan_state = "unknown"
        enabled = False

    elif radio_disabled(radio):
        status = "disabled"
        reason = radio_reason or "VHF radio/control path disabled."
        actual_scan_state = "not_scanning"
        enabled = False

    elif radio_in_dry_run(radio):
        status = "dry_run"
        reason = "VHF radio is in dry-run mode; dry-run planning only."
        actual_scan_state = "not_scanning"
        enabled = False

        if dry_run_reload_only:
            state, movement_status = apply_movement_tracking(
                redis_client,
                state,
                movement_min_delta_miles,
                movement_jump_reject_miles,
            )

            current_distance = parse_float(state.get("distance_since_reload_miles")) or 0.0
            if current_distance >= reload_distance_miles:
                reload_pending = True
                target_group = normalize_group(state.get("next_group"))
                source_group = state.get("active_group") or state.get("last_successful_group") or other_group(target_group)
                planned_memory = build_planned_memory_model(
                    nearby,
                    target_group=target_group,
                    source_group=str(source_group) if source_group else None,
                    radius_miles=radius_miles,
                    max_memory_channels=max_memory_channels,
                )
                if planned_memory.get("status") == "dry_run" and planned_memory.get("memory_count", 0) > 0:
                    now = utc_now()
                    state["last_reload_status"] = "dry_run"
                    state["last_reload_reason"] = "Dry-run reload plan generated; no radio programming performed."
                    state["last_reload_utc"] = now
                    state["last_reload_location"] = state.get("last_movement_location")
                    state["last_successful_group"] = target_group
                    state["next_group"] = other_group(target_group)
                    state["distance_since_reload_miles"] = 0.0
                    reload_pending = False
                    reason = "Dry-run reload plan generated; no radio programming performed."
                else:
                    state["last_reload_status"] = "unavailable"
                    state["last_reload_reason"] = planned_memory.get("reason") if planned_memory else "Dry-run reload plan failed."
                    reload_pending = True
                    reason = str(state["last_reload_reason"])
        else:
            reason = "Dry-run reload planning disabled by config."

    elif not radio_available(radio):
        status = "unavailable"
        reason = radio_reason or "VHF radio/control path unavailable."
        actual_scan_state = "unknown"
        enabled = False

    elif not nearby_available(nearby):
        status = "unavailable"
        reason = "Nearby repeater model unavailable."
        actual_scan_state = "unknown"
        enabled = False

    else:
        status = "available"
        reason = "VHF radio is available; waiting for reload distance before planning C/D reload."
        actual_scan_state = "unknown"
        enabled = False

        state, movement_status = apply_movement_tracking(
            redis_client,
            state,
            movement_min_delta_miles,
            movement_jump_reject_miles,
        )

        current_distance = parse_float(state.get("distance_since_reload_miles")) or 0.0
        if current_distance >= reload_distance_miles:
            reload_pending = True
            target_group = normalize_group(state.get("next_group"))
            source_group = state.get("active_group") or state.get("last_successful_group") or other_group(target_group)

            planned_memory = build_planned_memory_model(
                nearby,
                target_group=target_group,
                source_group=str(source_group) if source_group else None,
                radius_miles=radius_miles,
                max_memory_channels=max_memory_channels,
            )

            if planned_memory.get("status") == "dry_run" and planned_memory.get("memory_count", 0) > 0:
                adapter_request = build_adapter_cd_reload_plan_request(
                    planned_memory,
                    state,
                    radius_miles=radius_miles,
                    reload_distance_miles=reload_distance_miles,
                )
                state["last_reload_status"] = "plan_requested"
                state["last_reload_reason"] = "Planning-only C/D reload request published for adapter validation."
                state["last_reload_utc"] = utc_now()
                state["last_reload_location"] = state.get("last_movement_location")
                reload_pending = True
                reason = "Planning-only C/D reload request ready for adapter validation; no radio programming performed."
            else:
                state["last_reload_status"] = "unavailable"
                state["last_reload_reason"] = planned_memory.get("reason") if planned_memory else "Memory reload plan failed."
                reload_pending = True
                reason = str(state["last_reload_reason"])

    distance = parse_float(state.get("distance_since_reload_miles")) or 0.0

    model: Dict[str, Any] = {
        "enabled": enabled,
        "requested": requested,
        "actual_scan_state": actual_scan_state,
        "status": status,
        "reason": reason,
        "active_group": state.get("active_group"),
        "next_group": normalize_group(state.get("next_group")),
        "distance_since_reload_miles": rounded_miles(distance),
        "last_movement_location": state.get("last_movement_location"),
        "last_movement_utc": state.get("last_movement_utc"),
        "last_reload_utc": state.get("last_reload_utc"),
        "last_reload_location": state.get("last_reload_location"),
        "last_successful_group": state.get("last_successful_group"),
        "last_reload_status": state.get("last_reload_status"),
        "last_reload_reason": state.get("last_reload_reason"),
        "reload_pending": reload_pending,
        "reload_in_progress": reload_in_progress,
        "reload_distance_miles": display_number(reload_distance_miles),
        "radius_miles": display_number(radius_miles),
        "movement_min_delta_miles": display_number(movement_min_delta_miles),
        "movement_jump_reject_miles": display_number(movement_jump_reject_miles),
        "movement_status": movement_status,
        "nearby_count": nearby_count,
        "eligible_count": eligible_count,
        "nearby_model_radius_miles": nearby.get("radius_miles"),
        "vhf_radio_status": radio_status_value or None,
        "vhf_radio_available": bool(radio_available(radio)),
        "vhf_radio_reason": radio_reason or None,

        # Compatibility aliases for existing display/debug panels.
        "adapter_status": radio_status_value or None,
        "adapter_available": bool(radio_available(radio)),
        "adapter_control_mode": radio.get("adapter_control_mode") if isinstance(radio, dict) else None,
        "adapter_writes_enabled": False,
        "adapter_memory_programming_enabled": False,
        "adapter_scan_control_enabled": False,
        "source": SOURCE,
        "updated_utc": utc_now(),
    }

    return model, planned_memory, adapter_request


def comparable_model(model: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in model.items() if k != "updated_utc"}


def publish_state_changed(redis_client: RedisCli, keys: List[str], reason: str) -> None:
    event = {
        "topic": "state.changed",
        "type": "state.changed",
        "source": SOURCE,
        "keys": keys,
        "changed_keys": keys,
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now(),
        "host": socket.gethostname(),
    }
    redis_client.publish_json(SYSTEM_BUS, event)


def publish_json_if_changed(
    redis_client: RedisCli,
    key: str,
    model: Dict[str, Any],
    force: bool = False,
    reason: str = "model_changed",
) -> bool:
    old = load_json_model(redis_client, key)

    if not force and comparable_model(old) == comparable_model(model):
        return False

    redis_client.set(key, json.dumps(model, separators=(",", ":"), sort_keys=True))
    publish_state_changed(redis_client, [key], reason if not force else f"{reason}_heartbeat")
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
            radio = load_json_model(redis_client, KEY_VHF_RADIO)
            nearby = load_json_model(redis_client, KEY_NEARBY)
            previous = load_json_model(redis_client, KEY_SCAN)

            model, planned_memory, adapter_request = build_model(redis_client, config, request, radio, nearby, previous)
            force = (time.monotonic() - last_force_publish) >= force_publish_seconds

            planned_wrote = False
            if planned_memory is not None:
                planned_wrote = publish_json_if_changed(
                    redis_client,
                    KEY_PLANNED_MEMORY,
                    planned_memory,
                    force=False,
                    reason="vhf_planned_memory_changed",
                )

            adapter_request_wrote = False
            if adapter_request is not None:
                adapter_request_wrote = publish_json_if_changed(
                    redis_client,
                    KEY_ADAPTER_REQUEST,
                    adapter_request,
                    force=False,
                    reason="vhf_adapter_plan_request_changed",
                )

            wrote = publish_json_if_changed(
                redis_client,
                KEY_SCAN,
                model,
                force=force,
                reason="vhf_scan_model_changed",
            )

            if wrote or planned_wrote or adapter_request_wrote:
                last_force_publish = time.monotonic()
                log(
                    f"published requested={model['requested']} "
                    f"enabled={model['enabled']} "
                    f"status={model['status']} "
                    f"actual_scan_state={model['actual_scan_state']} "
                    f"distance_since_reload_miles={model['distance_since_reload_miles']} "
                    f"next_group={model['next_group']} "
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
