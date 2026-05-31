#!/usr/bin/env python3
"""
RollingThunder VHF software repeater scan manager.

Phase 8.2A scope:
- Controller owns software scan loop and all scan decisions.
- Controller reads rt:vhf:scan:request, rt:vhf:radio, rt:vhf:repeaters:nearby.
- Controller asks the rt-radio adapter to tune/read status through Redis requests.
- Adapter owns all IC-2730A / CI-V / serial command behavior.
- No rigctl.
- No memory-bank loading.
- No memory clear/write.
- No memory-bank scan start.
- No PTT/transmit controls.
- UI remains renderer-only.
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

SOURCE = "vhf_repeater_scan_manager"

KEY_SCAN = "rt:vhf:scan"
KEY_SCAN_REQUEST = "rt:vhf:scan:request"
KEY_VHF_RADIO = "rt:vhf:radio"
KEY_NEARBY = "rt:vhf:repeaters:nearby"
KEY_ADAPTER_REQUEST = "rt:vhf:adapter:request"
KEY_ADAPTER_LAST_RESULT = "rt:vhf:adapter:last_result"
KEY_VHF_SELECT_REQUEST = "rt:vhf:select:request"
KEY_VHF_SELECT_STATE = "rt:vhf:select:state"

SYSTEM_BUS = "rt:system:bus"

DEFAULT_ENABLED = False
DEFAULT_DWELL_MS = 500
DEFAULT_CONFIRM_SQUELCH_SECONDS = 5.0
DEFAULT_RESUME_IDLE_SECONDS = 15.0
DEFAULT_IDLE_POLL_SECONDS = 1.0
DEFAULT_ADAPTER_RESULT_TIMEOUT_SECONDS = 3.0
DEFAULT_MANUAL_SELECT_ADAPTER_TIMEOUT_SECONDS = 60.0
DEFAULT_INITIAL_PRIME_SETTLE_SECONDS = 15.0
DEFAULT_SOFTWARE_SCAN_ENABLED = False
DEFAULT_MODE = "repeaters"
DEFAULT_REPEATER_RADIUS_MILES = 25
DEFAULT_MAP_RADIUS_MILES = 30
DEFAULT_GPS_RELOAD_DISTANCE_MILES = 5
DEFAULT_PTT_RELOAD_HOLDOFF_SECONDS = 180
DEFAULT_SQUELCH_RELOAD_HOLDOFF_SECONDS = 120



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
        return data if isinstance(data, dict) else {}
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
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
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


def scan_config_values(config: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(
        os.environ.get("RT_VHF_SCAN_MODE_DEFAULT")
        or deep_get(config, "vhf.scan.mode_default", DEFAULT_MODE)
        or DEFAULT_MODE
    ).strip().lower()

    if mode not in {"repeaters", "air", "news"}:
        warn(f"invalid vhf.scan.mode_default={mode!r}; using {DEFAULT_MODE!r}")
        mode = DEFAULT_MODE

    return {
        "mode": mode,
        "repeater_radius_miles": cfg_float(
            config,
            "vhf.scan.repeater_radius_miles",
            "RT_VHF_SCAN_REPEATER_RADIUS_MILES",
            DEFAULT_REPEATER_RADIUS_MILES,
            minimum=0.0,
        ),
        "map_radius_miles": cfg_float(
            config,
            "vhf.scan.map_radius_miles",
            "RT_VHF_SCAN_MAP_RADIUS_MILES",
            DEFAULT_MAP_RADIUS_MILES,
            minimum=0.0,
        ),
        "gps_reload_distance_miles": cfg_float(
            config,
            "vhf.scan.gps_reload_distance_miles",
            "RT_VHF_SCAN_GPS_RELOAD_DISTANCE_MILES",
            DEFAULT_GPS_RELOAD_DISTANCE_MILES,
            minimum=0.0,
        ),
        "ptt_reload_holdoff_seconds": cfg_float(
            config,
            "vhf.scan.ptt_reload_holdoff_seconds",
            "RT_VHF_SCAN_PTT_RELOAD_HOLDOFF_SECONDS",
            DEFAULT_PTT_RELOAD_HOLDOFF_SECONDS,
            minimum=0.0,
        ),
        "squelch_reload_holdoff_seconds": cfg_float(
            config,
            "vhf.scan.squelch_reload_holdoff_seconds",
            "RT_VHF_SCAN_SQUELCH_RELOAD_HOLDOFF_SECONDS",
            DEFAULT_SQUELCH_RELOAD_HOLDOFF_SECONDS,
            minimum=0.0,
        ),
    }

def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


class RedisCli:
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

    def set(self, key: str, value: str) -> None:
        self._run(["SET", key, value])

    def publish_json(self, channel: str, payload: Dict[str, Any]) -> None:
        self._run(["PUBLISH", channel, json.dumps(payload, separators=(",", ":"), sort_keys=True)])


def load_json_model(redis_client: RedisCli, key: str) -> Dict[str, Any]:
    try:
        raw = redis_client.get(key)
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        warn(f"could not read {key}: {exc}")
        return {}


def radio_status(radio: Dict[str, Any]) -> str:
    return str(radio.get("status", "")).strip().lower()


def radio_available(radio: Dict[str, Any]) -> bool:
    if not radio:
        return False
    status = radio_status(radio)
    return boolish(radio.get("available"), False) and status in {"available", "ready"}


def parse_requested(request_model: Dict[str, Any], config: Dict[str, Any]) -> bool:
    if "requested" in request_model:
        return boolish(request_model.get("requested"), DEFAULT_ENABLED)
    if "enabled" in request_model:
        return boolish(request_model.get("enabled"), DEFAULT_ENABLED)
    return cfg_bool(config, "vhf.scan.enabled_default", "RT_VHF_SCAN_ENABLED_DEFAULT", DEFAULT_ENABLED)


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
    return bool(nearby_items(nearby))


def numeric_tone(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.upper() in {"NONE", "OFF", "N/A", "NA", "NULL", "-"}:
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


def normalize_duplex(item: Dict[str, Any], offset_mhz: Optional[float]) -> str:
    raw = str(item.get("duplex") or item.get("offset_direction") or "").strip().lower()

    if raw in {"+", "plus", "positive", "up", "dup+", "duplex+"}:
        return "plus"
    if raw in {"-", "minus", "negative", "down", "dup-", "duplex-"}:
        return "minus"
    if raw in {"simplex", "off", "none", "0", "0.0"}:
        return "simplex"

    if offset_mhz is not None:
        if offset_mhz > 0:
            return "plus"
        if offset_mhz < 0:
            return "minus"

    return "simplex"


def normalize_tone_mode(item: Dict[str, Any], tone_hz: Optional[float]) -> str:
    raw = str(
        item.get("tone_mode")
        or item.get("tone_type")
        or item.get("ctcss_mode")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")

    if raw in {"", "none", "off", "no", "false", "0"}:
        return "tone" if tone_hz is not None else "none"

    if raw in {"tone", "encode", "encode_only", "tx_tone", "tone_encode", "ctcss_encode", "repeater_tone"}:
        return "tone"

    if raw in {"ctcss"}:
        return "tone"

    if raw in {"tsql", "tone_sql", "tone_squelch", "tonesquelch", "decode", "encode_decode", "ctcss_sql"}:
        return "tsql"

    return "tone" if tone_hz is not None else "none"


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


def source_item_id(item: Dict[str, Any]) -> str:
    for key in ("id", "source_id", "repeater_id", "station_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return repeater_callsign(item) or repeater_name(item)


def is_analog_fm(item: Dict[str, Any]) -> bool:
    mode = str(item.get("mode") or item.get("fm_mode") or "FM").strip().upper()
    if mode in {"FM", "NFM", "WFM", ""}:
        text = " ".join(str(item.get(k, "")) for k in ("mode", "type", "special", "notes", "tags", "category")).lower()
        digital_markers = ("dmr", "d-star", "dstar", "fusion", "ysf", "p25", "nxdn", "m17", "aprs")
        return not any(marker in text for marker in digital_markers)
    return False


def normalize_repeater(raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    freq = item_frequency(raw_item)
    if freq is None:
        return None
    if not is_analog_fm(raw_item):
        return None

    tx_freq = item_tx_frequency(raw_item)
    offset: Optional[float] = None
    if tx_freq is not None:
        offset = abs(round(tx_freq - freq, 6))
    else:
        offset_val = parse_float(raw_item.get("offset_mhz"))
        if offset_val is not None:
            offset = abs(round(offset_val, 6))

    if offset is None:
        offset = 0.0

    tone = numeric_tone(raw_item.get("tx_tone"))
    if tone is None:
        tone = numeric_tone(raw_item.get("rx_tone"))
    if tone is None:
        tone = numeric_tone(raw_item.get("tone_hz"))

    return {
        "id": source_item_id(raw_item),
        "source_id": source_item_id(raw_item),
        "name": repeater_name(raw_item),
        "callsign": repeater_callsign(raw_item),
        "frequency_mhz": freq,
        "mode": "FM",
        "duplex": normalize_duplex(raw_item, offset),
        "offset_mhz": offset,
        "tone_hz": tone,
        "tone_mode": normalize_tone_mode(raw_item, tone),
        "distance_miles": raw_item.get("distance_miles"),
        "bearing_degrees": raw_item.get("bearing_degrees"),
        "skywarn": boolish(raw_item.get("skywarn"), False),
        "ares": boolish(raw_item.get("ares"), False),
    }


def eligible_repeaters(nearby: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in nearby_items(nearby):
        normalized = normalize_repeater(item)
        if normalized is not None:
            out.append(normalized)
    return out


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


def comparable_model(model: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in model.items() if k != "updated_utc"}


def publish_scan(redis_client: RedisCli, model: Dict[str, Any], reason: str = "vhf_scan_model_changed") -> None:
    old = load_json_model(redis_client, KEY_SCAN)
    if comparable_model(old) == comparable_model(model):
        return
    redis_client.set(KEY_SCAN, json.dumps(model, separators=(",", ":"), sort_keys=True))
    publish_state_changed(redis_client, [KEY_SCAN], reason)


def scan_model(
    *,
    requested: bool,
    enabled: bool,
    scanning: bool,
    status: str,
    reason: str,
    repeaters: List[Dict[str, Any]],
    current_index: int = 0,
    current_repeater: Optional[Dict[str, Any]] = None,
    last_squelch_activity_utc: Optional[str] = None,
    last_ptt_activity_utc: Optional[str] = None,
    last_user_frequency_change_utc: Optional[str] = None,
    dwell_ms: int = DEFAULT_DWELL_MS,
    timing: Optional[Dict[str, Any]] = None,
    confirm_squelch_seconds: float = DEFAULT_CONFIRM_SQUELCH_SECONDS,
    resume_idle_seconds: float = DEFAULT_RESUME_IDLE_SECONDS,
    scan_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    scan_cfg = scan_cfg if isinstance(scan_cfg, dict) else {}

    mode = str(scan_cfg.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in {"repeaters", "air", "news"}:
        mode = DEFAULT_MODE

    current_frequency = None
    current_repeater_id = None

    if isinstance(current_repeater, dict):
        current_frequency = current_repeater.get("frequency_mhz")
        current_repeater_id = (
            current_repeater.get("id")
            or current_repeater.get("source_id")
            or current_repeater.get("callsign")
            or current_repeater.get("name")
        )

    return {
        "enabled": bool(enabled),
        "requested": bool(requested),
        "mode": mode,
        "scanning": bool(scanning),
        "actual_scan_state": "scanning" if scanning else "not_scanning",
        "status": status,
        "reason": reason,
        "current_index": int(current_index),
        "current_frequency_mhz": current_frequency,
        "current_repeater_id": current_repeater_id,
        "current_repeater": current_repeater,
        "last_squelch_activity_utc": last_squelch_activity_utc,
        "last_ptt_activity_utc": last_ptt_activity_utc,
        "last_user_frequency_change_utc": last_user_frequency_change_utc,
        "dwell_ms": int(dwell_ms),
        "timing": timing or {},
        "confirm_squelch_seconds": float(confirm_squelch_seconds),
        "resume_idle_seconds": float(resume_idle_seconds),
        "repeater_radius_miles": float(scan_cfg.get("repeater_radius_miles", DEFAULT_REPEATER_RADIUS_MILES)),
        "map_radius_miles": float(scan_cfg.get("map_radius_miles", DEFAULT_MAP_RADIUS_MILES)),
        "gps_reload_distance_miles": float(scan_cfg.get("gps_reload_distance_miles", DEFAULT_GPS_RELOAD_DISTANCE_MILES)),
        "ptt_reload_holdoff_seconds": float(scan_cfg.get("ptt_reload_holdoff_seconds", DEFAULT_PTT_RELOAD_HOLDOFF_SECONDS)),
        "squelch_reload_holdoff_seconds": float(scan_cfg.get("squelch_reload_holdoff_seconds", DEFAULT_SQUELCH_RELOAD_HOLDOFF_SECONDS)),
        "repeater_count": len(repeaters),
        "nearby_count": len(repeaters),
        "source": SOURCE,
        "updated_utc": utc_now(),
    }

def write_adapter_request(redis_client: RedisCli, action: str, payload: Dict[str, Any]) -> str:
    request_id = f"vhf-softscan-{action}-{uuid.uuid4().hex}"
    request = {
        "request_id": request_id,
        "action": action,
        "source": SOURCE,
        **payload,
    }
    redis_client.set(KEY_ADAPTER_REQUEST, json.dumps(request, separators=(",", ":"), sort_keys=True))
    publish_state_changed(redis_client, [KEY_ADAPTER_REQUEST], f"vhf_adapter_{action}_request")
    return request_id


def wait_for_adapter_result(redis_client: RedisCli, request_id: str, timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = load_json_model(redis_client, KEY_ADAPTER_LAST_RESULT)
        if str(result.get("request_id") or "") == request_id:
            return result
        time.sleep(0.05)
    return {
        "request_id": request_id,
        "ok": False,
        "status": "timeout",
        "operation_performed": False,
        "reason": "Timed out waiting for adapter result.",
        "updated_utc": utc_now(),
    }


def adapter_request(
    redis_client: RedisCli,
    action: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    started_mono = time.monotonic()
    request_id = write_adapter_request(redis_client, action, payload)
    result = wait_for_adapter_result(redis_client, request_id, timeout_seconds)

    controller_timing = result.get("controller_timing")
    if not isinstance(controller_timing, dict):
        controller_timing = {}

    controller_timing.update(
        {
            "adapter_request_action": action,
            "adapter_request_elapsed_ms": int(round((time.monotonic() - started_mono) * 1000.0)),
            "adapter_result_timeout_seconds": float(timeout_seconds),
        }
    )

    result["controller_timing"] = controller_timing
    return result


def current_requested(redis_client: RedisCli, config: Dict[str, Any]) -> bool:
    return parse_requested(load_json_model(redis_client, KEY_SCAN_REQUEST), config)

def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def write_scan_request_disabled_for_select(
    redis_client: RedisCli,
    select_request: Dict[str, Any],
) -> None:
    payload = {
        "requested": False,
        "enabled": False,
        "reason": "manual_repeater_select",
        "source": SOURCE,
        "selected_id": str(select_request.get("selected_id") or ""),
        "selected_index": select_request.get("selected_index"),
        "updated_utc": utc_now(),
    }
    redis_client.set(KEY_SCAN_REQUEST, compact_json(payload))
    publish_state_changed(redis_client, [KEY_SCAN_REQUEST], "vhf_manual_select_scan_request_disabled")

def publish_user_stopped_scan(
    redis_client: RedisCli,
    *,
    repeaters: List[Dict[str, Any]],
    current_index: int,
    current_repeater: Optional[Dict[str, Any]],
    dwell_ms: int,
    confirm_seconds: float,
    resume_idle_seconds: float,
    scan_cfg: Dict[str, Any],
) -> None:
    publish_scan(
        redis_client,
        scan_model(
            requested=False,
            enabled=False,
            scanning=False,
            status="disabled",
            reason="Repeater scanning stopped by user.",
            repeaters=repeaters,
            current_index=current_index,
            current_repeater=current_repeater,
            dwell_ms=dwell_ms,
            confirm_squelch_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
            scan_cfg=scan_cfg,
        ),
        reason="vhf_scan_stopped_by_user",
    )

def write_scan_request_disabled_for_activity(
    redis_client: RedisCli,
    *,
    repeater: Dict[str, Any],
    index: int,
) -> None:
    payload = {
        "requested": False,
        "enabled": False,
        "reason": "stopped_on_activity",
        "source": SOURCE,
        "current_index": int(index),
        "current_repeater_id": (
            repeater.get("id")
            or repeater.get("source_id")
            or repeater.get("callsign")
            or repeater.get("name")
        ),
        "current_frequency_mhz": repeater.get("frequency_mhz"),
        "updated_utc": utc_now(),
    }

    redis_client.set(KEY_SCAN_REQUEST, compact_json(payload))
    publish_state_changed(
        redis_client,
        [KEY_SCAN_REQUEST],
        "vhf_scan_request_disabled_after_activity",
    )

def write_select_state(
    redis_client: RedisCli,
    *,
    request_id: str,
    selected_id: str,
    selected_index: Optional[int],
    active: bool,
    phase: str,
    status: str,
    reason: str,
    adapter_request_id: Optional[str] = None,
    adapter_result: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "active": bool(active),
        "request_id": request_id,
        "selected_id": selected_id,
        "selected_index": selected_index,
        "phase": phase,
        "status": status,
        "reason": reason,
        "source": SOURCE,
        "updated_utc": utc_now(),
        "updated_at_ms": int(time.time() * 1000),
    }

    if adapter_request_id:
        payload["adapter_request_id"] = adapter_request_id

    if adapter_result is not None:
        payload["adapter_result"] = adapter_result

    redis_client.set(KEY_VHF_SELECT_STATE, compact_json(payload))
    publish_state_changed(redis_client, [KEY_VHF_SELECT_STATE], "vhf_select_state_changed")


def latest_unhandled_select_request(redis_client: RedisCli) -> Dict[str, Any]:
    request = load_json_model(redis_client, KEY_VHF_SELECT_REQUEST)
    if not request:
        return {}

    request_id = str(request.get("request_id") or "").strip()
    selected_id = str(request.get("selected_id") or "").strip()

    if not request_id or not selected_id:
        return {}

    state = load_json_model(redis_client, KEY_VHF_SELECT_STATE)
    if (
        str(state.get("request_id") or "").strip() == request_id
        and boolish(state.get("active"), False) is False
        and str(state.get("status") or "").strip().lower() in {"ok", "partial", "rejected", "error", "timeout"}
    ):
        return {}

    return request


def select_request_pending(redis_client: RedisCli) -> bool:
    return bool(latest_unhandled_select_request(redis_client))


def resolve_selected_repeater(
    select_request: Dict[str, Any],
    repeaters: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[int], str]:
    selected_id = str(select_request.get("selected_id") or "").strip()

    try:
        selected_index = int(select_request.get("selected_index"))
    except Exception:
        selected_index = None

    if selected_id:
        for idx, repeater in enumerate(repeaters):
            rid = str(
                repeater.get("id")
                or repeater.get("source_id")
                or repeater.get("repeater_id")
                or repeater.get("callsign")
                or repeater.get("name")
                or ""
            ).strip()

            if rid and rid == selected_id:
                return repeater, idx, "matched_selected_id"

    if selected_index is not None and 0 <= selected_index < len(repeaters):
        return repeaters[selected_index], selected_index, "matched_selected_index"

    return None, None, "selected_repeater_not_found"


def process_vhf_select_request(
    redis_client: RedisCli,
    config: Dict[str, Any],
    repeaters: List[Dict[str, Any]],
    current_index: int,
    dwell_ms: int,
    confirm_seconds: float,
    resume_idle_seconds: float,
    scan_cfg: Dict[str, Any],
    adapter_timeout: float,
) -> Optional[int]:
    select_request = latest_unhandled_select_request(redis_client)
    if not select_request:
        return None

    request_id = str(select_request.get("request_id") or "").strip()
    selected_id = str(select_request.get("selected_id") or "").strip()

    try:
        selected_index = int(select_request.get("selected_index"))
    except Exception:
        selected_index = None

    if not request_id or not selected_id:
        return None

    write_select_state(
        redis_client,
        request_id=request_id,
        selected_id=selected_id,
        selected_index=selected_index,
        active=True,
        phase="stopping_scan",
        status="pending",
        reason="Stopping software scan before manual repeater select.",
    )

    write_scan_request_disabled_for_select(redis_client, select_request)

    # Latest-selection-wins before tuning starts.
    # If the user moved to another row and pressed OK while we were stopping scan,
    # honor the newer select request instead of sending two tune requests.
    latest_request = latest_unhandled_select_request(redis_client)
    if latest_request:
        latest_request_id = str(latest_request.get("request_id") or "").strip()
        if latest_request_id and latest_request_id != request_id:
            select_request = latest_request
            request_id = latest_request_id
            selected_id = str(select_request.get("selected_id") or "").strip()
            try:
                selected_index = int(select_request.get("selected_index"))
            except Exception:
                selected_index = None

            write_select_state(
                redis_client,
                request_id=request_id,
                selected_id=selected_id,
                selected_index=selected_index,
                active=True,
                phase="stopping_scan",
                status="pending",
                reason="Replaced pending VHF select with latest selected row before tuning.",
            )
            write_scan_request_disabled_for_select(redis_client, select_request)

    repeater, resolved_index, resolve_reason = resolve_selected_repeater(select_request, repeaters)

    if repeater is None:
        write_select_state(
            redis_client,
            request_id=request_id,
            selected_id=selected_id,
            selected_index=selected_index,
            active=False,
            phase="complete",
            status="rejected",
            reason="Selected repeater could not be resolved from controller-owned repeater list.",
        )

        publish_scan(
            redis_client,
            scan_model(
                requested=False,
                enabled=False,
                scanning=False,
                status="manual_select_rejected",
                reason="Selected repeater could not be resolved; no radio command sent.",
                repeaters=repeaters,
                current_index=current_index,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
            reason="vhf_manual_select_rejected",
        )
        return current_index

    target_index = resolved_index if resolved_index is not None else current_index

    write_select_state(
        redis_client,
        request_id=request_id,
        selected_id=selected_id,
        selected_index=target_index,
        active=True,
        phase="tuning",
        status="pending",
        reason=f"Sending one adapter tune request for selected repeater ({resolve_reason}).",
    )

    publish_scan(
        redis_client,
        scan_model(
            requested=False,
            enabled=False,
            scanning=False,
            status="manual_select_tuning",
            reason="Manual repeater select requested; tuning selected repeater.",
            repeaters=repeaters,
            current_index=target_index,
            current_repeater=repeater,
            last_user_frequency_change_utc=utc_now(),
            dwell_ms=dwell_ms,
            confirm_squelch_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
            scan_cfg=scan_cfg,
        ),
        reason="vhf_manual_select_tuning",
    )

    result = adapter_request(
        redis_client,
        "manual_select_fast_tune_repeater_vfo",
        {
            "reason": "manual_repeater_select",
            "selected_id": selected_id,
            "selected_index": target_index,
            "repeater": repeater,
            "force_full_tune": False,
        },
        adapter_timeout,
    )

    ok = bool(result.get("ok"))
    status = str(result.get("status") or ("ok" if ok else "error")).strip().lower()

    tuned_or_attempted = (
        ok
        or status == "partial"
        or bool(result.get("operation_performed"))
        or result.get("frequency_mhz") is not None
    )

    final_scan_status = "manual_selected" if tuned_or_attempted else "manual_select_failed"
    final_reason = (
        "Manual repeater select tune completed."
        if ok
        else (
            "Manual repeater select tune completed with readback warning."
            if tuned_or_attempted
            else str(result.get("reason") or "Manual repeater select tune failed.")
        )
    )

    write_select_state(
        redis_client,
        request_id=request_id,
        selected_id=selected_id,
        selected_index=target_index,
        active=False,
        phase="complete",
        status=status,
        reason=final_reason,
        adapter_request_id=str(result.get("request_id") or ""),
        adapter_result={
            "ok": ok,
            "status": result.get("status"),
            "reason": result.get("reason"),
            "operation_performed": result.get("operation_performed"),
            "action": result.get("action"),
            "frequency_mhz": result.get("frequency_mhz"),
        },
    )

    publish_scan(
        redis_client,
        scan_model(
            requested=False,
            enabled=False,
            scanning=False,
            status=final_scan_status,
            reason=final_reason,
            repeaters=repeaters,
            current_index=target_index,
            current_repeater=repeater,
            last_user_frequency_change_utc=utc_now(),
            dwell_ms=dwell_ms,
            confirm_squelch_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
            scan_cfg=scan_cfg,
        ),
        reason="vhf_manual_select_complete",
    )

    return target_index

def run_scan_cycle(
    redis_client: RedisCli,
    config: Dict[str, Any],
    start_index: int,
) -> int:
    software_scan_enabled = cfg_bool(
        config,
        "vhf.scan.software_scan_enabled",
        "RT_VHF_SOFTWARE_SCAN_ENABLED",
        DEFAULT_SOFTWARE_SCAN_ENABLED,
    )
    dwell_ms = cfg_int(config, "vhf.scan.dwell_ms", "RT_VHF_SCAN_DWELL_MS", DEFAULT_DWELL_MS, minimum=50)
    confirm_seconds = cfg_float(
        config,
        "vhf.scan.confirm_squelch_seconds",
        "RT_VHF_SCAN_CONFIRM_SQUELCH_SECONDS",
        DEFAULT_CONFIRM_SQUELCH_SECONDS,
        minimum=0.1,
    )
    resume_idle_seconds = cfg_float(
        config,
        "vhf.scan.resume_idle_seconds",
        "RT_VHF_SCAN_RESUME_IDLE_SECONDS",
        DEFAULT_RESUME_IDLE_SECONDS,
        minimum=0.1,
    )
    adapter_timeout = cfg_float(
        config,
        "vhf.scan.adapter_result_timeout_seconds",
        "RT_VHF_SCAN_ADAPTER_RESULT_TIMEOUT_SECONDS",
        DEFAULT_ADAPTER_RESULT_TIMEOUT_SECONDS,
        minimum=0.2,
    )

    manual_select_adapter_timeout = cfg_float(
        config,
        "vhf.scan.manual_select_adapter_result_timeout_seconds",
        "RT_VHF_MANUAL_SELECT_ADAPTER_RESULT_TIMEOUT_SECONDS",
        DEFAULT_MANUAL_SELECT_ADAPTER_TIMEOUT_SECONDS,
        minimum=1.0,
    )

    initial_prime_settle_seconds = cfg_float(
        config,
        "vhf.scan.initial_prime_settle_seconds",
        "RT_VHF_SCAN_INITIAL_PRIME_SETTLE_SECONDS",
        DEFAULT_INITIAL_PRIME_SETTLE_SECONDS,
        minimum=0.0,
    )    
    
    scan_cfg = scan_config_values(config)

    request = load_json_model(redis_client, KEY_SCAN_REQUEST)
    requested = parse_requested(request, config)
    radio = load_json_model(redis_client, KEY_VHF_RADIO)
    nearby = load_json_model(redis_client, KEY_NEARBY)
    repeaters = eligible_repeaters(nearby)

    select_result_index = process_vhf_select_request(
        redis_client,
        config,
        repeaters,
        start_index,
        dwell_ms,
        confirm_seconds,
        resume_idle_seconds,
        scan_cfg,
        manual_select_adapter_timeout,
    )
    if select_result_index is not None:
        return select_result_index

    if not requested:
        existing_scan = load_json_model(redis_client, KEY_SCAN)
        existing_status = str(existing_scan.get("status") or "").strip().lower()

        # Preserve the controller-owned manual selection after OK/select completes.
        # Otherwise the next idle scan-manager cycle immediately erases the
        # selected repeater and publishes generic disabled/not_scanning state.
        if existing_status in {"manual_selected", "manual_select_tuning"}:
            return start_index

        current_repeater = None
        existing_current = existing_scan.get("current_repeater")
        if isinstance(existing_current, dict):
            current_repeater = existing_current
        elif repeaters:
            try:
                current_repeater = repeaters[start_index % len(repeaters)]
            except Exception:
                current_repeater = None

        publish_user_stopped_scan(
            redis_client,
            repeaters=repeaters,
            current_index=start_index,
            current_repeater=current_repeater,
            dwell_ms=dwell_ms,
            confirm_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
            scan_cfg=scan_cfg,
        )
        return start_index

    if not software_scan_enabled:
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=False,
                scanning=False,
                status="disabled",
                reason="Software repeater scanning disabled by config.",
                repeaters=repeaters,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        return start_index

    if not radio_available(radio):
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=False,
                scanning=False,
                status="unavailable",
                reason="VHF radio unavailable; no radio command sent.",
                repeaters=repeaters,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        return start_index

    if not nearby_available(nearby) or not repeaters:
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=False,
                scanning=False,
                status="unavailable",
                reason="Nearby analog FM repeater list unavailable or empty.",
                repeaters=repeaters,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        return start_index

    index = start_index % len(repeaters)
    first_repeater = repeaters[index]

    publish_scan(
        redis_client,
        scan_model(
            requested=True,
            enabled=True,
            scanning=False,
            status="priming_radio",
            reason=(
                "Priming IC-2730A scan VFO with a full tune before fast software scanning."
            ),
            repeaters=repeaters,
            current_index=index,
            current_repeater=first_repeater,
            dwell_ms=dwell_ms,
            confirm_squelch_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
            scan_cfg=scan_cfg,
        ),
    )

    prime_timeout = max(adapter_timeout, adapter_timeout + (dwell_ms / 1000.0))
    prime = adapter_request(
        redis_client,
        "software_scan_step",
        {
            "repeater": first_repeater,
            "dwell_ms": dwell_ms,
            "force_full_tune": True,
        },
        prime_timeout,
    )

    if str(prime.get("rx_tx_status") or "").lower() == "tx" or prime.get("tx_active") is True:
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=True,
                scanning=False,
                status="unavailable",
                reason="Radio reports TX; scan loop will not tune while transmitting.",
                repeaters=repeaters,
                current_index=index,
                current_repeater=first_repeater,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        time.sleep(1.0)
        return index

    if str(prime.get("status") or "").lower() == "timeout":
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=True,
                scanning=False,
                status="adapter_timeout",
                reason="Timed out while priming radio; will retry same repeater index.",
                repeaters=repeaters,
                current_index=index,
                current_repeater=first_repeater,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        time.sleep(1.0)
        return index

    if not bool(prime.get("ok")):
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=True,
                scanning=False,
                status="adapter_waiting",
                reason=str(prime.get("reason") or "Initial radio prime did not complete; will retry same repeater index."),
                repeaters=repeaters,
                current_index=index,
                current_repeater=first_repeater,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )
        time.sleep(1.0)
        return index

    if initial_prime_settle_seconds > 0:
        settle_deadline = time.monotonic() + initial_prime_settle_seconds
        while time.monotonic() < settle_deadline:
            if select_request_pending(redis_client):
                break
            time.sleep(min(0.1, max(0.0, settle_deadline - time.monotonic())))
    while current_requested(redis_client, config) and not select_request_pending(redis_client):
        radio = load_json_model(redis_client, KEY_VHF_RADIO)
        if not radio_available(radio):
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=False,
                    scanning=False,
                    status="unavailable",
                    reason="VHF radio became unavailable; scan loop stopped.",
                    repeaters=repeaters,
                    current_index=index,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                    scan_cfg=scan_cfg,
                ),
            )
            return index

        repeater = repeaters[index]

        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=True,
                scanning=True,
                status="scanning",
                reason=f"Scanning repeater {repeater.get('name') or repeater.get('callsign') or repeater.get('frequency_mhz')}.",
                repeaters=repeaters,
                current_index=index,
                current_repeater=repeater,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )

        step_timeout = max(adapter_timeout, adapter_timeout + (dwell_ms / 1000.0))
        step = adapter_request(
            redis_client,
            "software_scan_step",
            {
                "repeater": repeater,
                "dwell_ms": dwell_ms,
                "force_full_tune": False,
            },
            step_timeout,
        )

        if str(step.get("rx_tx_status") or "").lower() == "tx" or step.get("tx_active") is True:
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=True,
                    scanning=False,
                    status="unavailable",
                    reason="Radio reports TX; scan loop will not tune while transmitting.",
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                ),
            )
            time.sleep(1.0)
            return index

        step_status = str(step.get("status") or "").lower()

        if step_status == "timeout":
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=True,
                    scanning=False,
                    status="adapter_timeout",
                    reason="Timed out waiting for adapter scan step; retrying same repeater index.",
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                    scan_cfg=scan_cfg,
                ),
            )
            time.sleep(1.0)
            continue

        if not bool(step.get("ok")):
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=True,
                    scanning=False,
                    status="adapter_waiting",
                    reason=str(step.get("reason") or "Adapter scan step failed; retrying same repeater index."),
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                ),
            )
            time.sleep(1.0)
            continue

        if step.get("squelch_open") is not True:
            index = (index + 1) % len(repeaters)
            continue

        first_activity_utc = utc_now()
        publish_scan(
            redis_client,
            scan_model(
                requested=True,
                enabled=True,
                scanning=True,
                status="confirming_activity",
                reason="Squelch activity detected; confirming before stopping scan.",
                repeaters=repeaters,
                current_index=index,
                current_repeater=repeater,
                last_squelch_activity_utc=first_activity_utc,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
                scan_cfg=scan_cfg,
            ),
        )

        time.sleep(confirm_seconds)

        radio = load_json_model(redis_client, KEY_VHF_RADIO)
        if not radio_available(radio):
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=False,
                    scanning=False,
                    status="unavailable",
                    reason="VHF radio became unavailable before squelch confirmation; no radio command sent.",
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    last_squelch_activity_utc=first_activity_utc,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                    scan_cfg=scan_cfg,
                ),
            )
            return index

        confirm = adapter_request(redis_client, "read_squelch_status", {}, adapter_timeout)

        if confirm.get("squelch_open") is True:
            last_activity_utc = utc_now()

            write_scan_request_disabled_for_activity(
                redis_client,
                repeater=repeater,
                index=index,
            )

            publish_scan(
                redis_client,
                scan_model(
                    requested=False,
                    enabled=False,
                    scanning=False,
                    status="stopped_on_activity",
                    reason="Confirmed S-meter activity; software scan stopped on active repeater.",
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    last_squelch_activity_utc=last_activity_utc,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                    scan_cfg=scan_cfg,
                ),
                reason="vhf_scan_stopped_on_activity",
            )

            return index
        else:
            index = (index + 1) % len(repeaters)

    final_repeater = None
    if repeaters:
        try:
            final_repeater = repeaters[index % len(repeaters)]
        except Exception:
            final_repeater = None

    publish_user_stopped_scan(
        redis_client,
        repeaters=repeaters,
        current_index=index,
        current_repeater=final_repeater,
        dwell_ms=dwell_ms,
        confirm_seconds=confirm_seconds,
        resume_idle_seconds=resume_idle_seconds,
        scan_cfg=scan_cfg,
    )
    return index


def main() -> int:
    log("starting")

    redis_client: Optional[RedisCli] = None
    index = 0

    while True:
        try:
            config = load_json_file(APP_CONFIG_PATH)
            idle_poll_seconds = cfg_float(
                config,
                "vhf.scan.idle_poll_seconds",
                "RT_VHF_SCAN_IDLE_POLL_SECONDS",
                DEFAULT_IDLE_POLL_SECONDS,
                minimum=0.1,
            )

            if redis_client is None:
                redis_client = RedisCli()

            index = run_scan_cycle(redis_client, config, index)
            time.sleep(idle_poll_seconds)

        except KeyboardInterrupt:
            log("stopping")
            return 0
        except Exception as exc:
            redis_client = None
            warn(f"cycle failed: {exc}")
            time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())