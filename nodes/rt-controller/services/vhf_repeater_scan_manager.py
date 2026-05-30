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

SYSTEM_BUS = "rt:system:bus"

DEFAULT_ENABLED = False
DEFAULT_DWELL_MS = 500
DEFAULT_CONFIRM_SQUELCH_SECONDS = 5.0
DEFAULT_RESUME_IDLE_SECONDS = 15.0
DEFAULT_IDLE_POLL_SECONDS = 1.0
DEFAULT_ADAPTER_RESULT_TIMEOUT_SECONDS = 3.0
DEFAULT_SOFTWARE_SCAN_ENABLED = False


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
    dwell_ms: int = DEFAULT_DWELL_MS,
    confirm_squelch_seconds: float = DEFAULT_CONFIRM_SQUELCH_SECONDS,
    resume_idle_seconds: float = DEFAULT_RESUME_IDLE_SECONDS,
) -> Dict[str, Any]:
    current_frequency = None
    if isinstance(current_repeater, dict):
        current_frequency = current_repeater.get("frequency_mhz")

    return {
        "enabled": bool(enabled),
        "requested": bool(requested),
        "mode": "repeaters",
        "scanning": bool(scanning),
        "actual_scan_state": "scanning" if scanning else "not_scanning",
        "status": status,
        "reason": reason,
        "current_index": int(current_index),
        "current_frequency_mhz": current_frequency,
        "current_repeater": current_repeater,
        "last_squelch_activity_utc": last_squelch_activity_utc,
        "last_ptt_activity_utc": None,
        "dwell_ms": int(dwell_ms),
        "confirm_squelch_seconds": float(confirm_squelch_seconds),
        "resume_idle_seconds": float(resume_idle_seconds),
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
    request_id = write_adapter_request(redis_client, action, payload)
    return wait_for_adapter_result(redis_client, request_id, timeout_seconds)


def current_requested(redis_client: RedisCli, config: Dict[str, Any]) -> bool:
    return parse_requested(load_json_model(redis_client, KEY_SCAN_REQUEST), config)


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

    request = load_json_model(redis_client, KEY_SCAN_REQUEST)
    requested = parse_requested(request, config)
    radio = load_json_model(redis_client, KEY_VHF_RADIO)
    nearby = load_json_model(redis_client, KEY_NEARBY)
    repeaters = eligible_repeaters(nearby)

    if not requested:
        publish_scan(
            redis_client,
            scan_model(
                requested=False,
                enabled=False,
                scanning=False,
                status="disabled",
                reason="Repeater scanning disabled.",
                repeaters=repeaters,
                dwell_ms=dwell_ms,
                confirm_squelch_seconds=confirm_seconds,
                resume_idle_seconds=resume_idle_seconds,
            ),
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
            ),
        )
        return start_index

    index = start_index % len(repeaters)

    while current_requested(redis_client, config):
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
            ),
        )

        step_timeout = max(adapter_timeout, adapter_timeout + (dwell_ms / 1000.0))
        step = adapter_request(
            redis_client,
            "software_scan_step",
            {
                "repeater": repeater,
                "dwell_ms": dwell_ms,
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

        if not bool(step.get("ok")):
            index = (index + 1) % len(repeaters)
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
            ),
        )

        time.sleep(confirm_seconds)
        confirm = adapter_request(redis_client, "read_squelch_status", {}, adapter_timeout)

        if confirm.get("squelch_open") is True:
            last_activity_utc = utc_now()
            publish_scan(
                redis_client,
                scan_model(
                    requested=True,
                    enabled=True,
                    scanning=False,
                    status="stopped_on_activity",
                    reason="Confirmed squelch activity; software scan stopped on active repeater.",
                    repeaters=repeaters,
                    current_index=index,
                    current_repeater=repeater,
                    last_squelch_activity_utc=last_activity_utc,
                    dwell_ms=dwell_ms,
                    confirm_squelch_seconds=confirm_seconds,
                    resume_idle_seconds=resume_idle_seconds,
                ),
            )

            idle_start = time.monotonic()
            while current_requested(redis_client, config):
                idle_check = adapter_request(redis_client, "read_squelch_status", {}, adapter_timeout)
                if idle_check.get("squelch_open") is True:
                    idle_start = time.monotonic()
                    publish_scan(
                        redis_client,
                        scan_model(
                            requested=True,
                            enabled=True,
                            scanning=False,
                            status="stopped_on_activity",
                            reason="Activity continues on active repeater.",
                            repeaters=repeaters,
                            current_index=index,
                            current_repeater=repeater,
                            last_squelch_activity_utc=utc_now(),
                            dwell_ms=dwell_ms,
                            confirm_squelch_seconds=confirm_seconds,
                            resume_idle_seconds=resume_idle_seconds,
                        ),
                    )
                elif time.monotonic() - idle_start >= resume_idle_seconds:
                    index = (index + 1) % len(repeaters)
                    break
                time.sleep(1.0)
        else:
            index = (index + 1) % len(repeaters)

    publish_scan(
        redis_client,
        scan_model(
            requested=False,
            enabled=False,
            scanning=False,
            status="disabled",
            reason="Repeater scanning disabled.",
            repeaters=repeaters,
            current_index=index,
            dwell_ms=dwell_ms,
            confirm_squelch_seconds=confirm_seconds,
            resume_idle_seconds=resume_idle_seconds,
        ),
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