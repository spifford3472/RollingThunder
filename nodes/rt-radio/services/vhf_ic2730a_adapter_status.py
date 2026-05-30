#!/usr/bin/env python3
"""
RollingThunder IC-2730A adapter status/request publisher.

Phase 8B scope:
- Run on rt-radio, where the IC-2730A is physically connected.
- Instantiate the safe IC2730AAdapter boundary.
- Publish structured adapter status to Redis key rt:vhf:adapter.
- Process one safe request model: rt:vhf:adapter:request.
- Publish the last structured request result to rt:vhf:adapter:last_result.
- Publish state.changed to rt:system:bus.
- Avoid excessive Redis writes.
- Do not write rt:ui:bus.
- Do not program radio memories except through the adapter's explicitly gated
  write_single_memory_test() path.
- Do not clear memories.
- Do not bulk-write memories.
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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Set, Tuple

try:
    import redis  # type: ignore
except Exception as exc:
    print(f"ERROR: python redis module unavailable: {exc}", file=sys.stderr)
    sys.exit(2)

from ic2730a_adapter import IC2730AAdapter, IC2730AConfig


APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

KEY_ADAPTER = "rt:vhf:adapter"
KEY_ADAPTER_REQUEST = "rt:vhf:adapter:request"
KEY_ADAPTER_LAST_RESULT = "rt:vhf:adapter:last_result"
BUS_SYSTEM = "rt:system:bus"
SOURCE = "vhf_ic2730a_adapter_status"

DEFAULT_PUBLISH_INTERVAL_SECONDS = 30
DEFAULT_FORCE_PUBLISH_SECONDS = 300
DEFAULT_REQUEST_POLL_SECONDS = 0.1
MAX_REMEMBERED_REQUEST_IDS = 200

_running = True

NO_RADIO_REQUIRED_ACTIONS = {
    "plan_cd_bank_reload",
    "plan_load_bank",
    "plan_clear_bank",
    "plan_program_channel",
    "plan_start_memory_bank_scan",
}

RADIO_READY_STATUSES = {"available", "ready", "detected"}

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

def floatish(value: Any, default: float, minimum: float) -> float:
    try:
        parsed = float(value)
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
        "request_poll_seconds": floatish(
            os.environ.get("RT_VHF_ADAPTER_REQUEST_POLL_SECONDS")
            or ic.get("request_poll_seconds"),
            DEFAULT_REQUEST_POLL_SECONDS,
            0.05,
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

    The IC2730AAdapter already owns safe fields. This publisher preserves the
    config gates it reports so Phase 8B can show whether the explicit write-test
    gates are enabled, without adding UI-side logic or radio details.
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

    model["writes_enabled"] = boolish(model.get("writes_enabled"), False)
    model["scan_control_enabled"] = boolish(model.get("scan_control_enabled"), False)
    model["side_b_programming_enabled"] = boolish(model.get("side_b_programming_enabled"), False)
    model["memory_programming_enabled"] = boolish(model.get("memory_programming_enabled"), False)
    model["write_test_enabled"] = boolish(model.get("write_test_enabled"), False)
    model["write_test_allow_single_memory_write"] = boolish(
        model.get("write_test_allow_single_memory_write"),
        False,
    )

    model["updated_utc"] = utc_now_iso()

    return model


def stable_part(model: Dict[str, Any]) -> Dict[str, Any]:
    ignored = {
        "updated_utc",
        "detail",
    }
    return {k: v for k, v in model.items() if k not in ignored}


def read_json_key(client: "redis.Redis", key: str) -> Optional[Dict[str, Any]]:
    try:
        raw = client.get(key)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        warn(f"unable to read existing {key}: {exc}")
        return None


def read_existing(client: "redis.Redis") -> Optional[Dict[str, Any]]:
    return read_json_key(client, KEY_ADAPTER)


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
    client.set(KEY_ADAPTER, json.dumps(model, sort_keys=True, separators=(",", ":")))
    publish_state_changed(client, [KEY_ADAPTER], reason)


def publish_request_result(
    client: "redis.Redis",
    adapter_model: Dict[str, Any],
    result: Dict[str, Any],
    reason: str,
) -> None:
    client.set(KEY_ADAPTER, json.dumps(adapter_model, sort_keys=True, separators=(",", ":")))
    client.set(KEY_ADAPTER_LAST_RESULT, json.dumps(result, sort_keys=True, separators=(",", ":")))
    publish_state_changed(client, [KEY_ADAPTER, KEY_ADAPTER_LAST_RESULT], reason)


def build_adapter_status(app: Dict[str, Any]) -> Dict[str, Any]:
    config = IC2730AConfig.from_app_config(app)
    adapter = IC2730AAdapter(config)
    return normalize_adapter_status(adapter.get_status())


def remember_request_id(request_id: str, remembered: Set[str], order: Deque[str]) -> None:
    if request_id in remembered:
        return
    remembered.add(request_id)
    order.append(request_id)
    while len(order) > MAX_REMEMBERED_REQUEST_IDS:
        old = order.popleft()
        remembered.discard(old)


def request_already_handled(
    client: "redis.Redis",
    request_id: str,
    remembered: Set[str],
) -> bool:
    if request_id in remembered:
        return True

    last_result = read_json_key(client, KEY_ADAPTER_LAST_RESULT)
    if last_result and str(last_result.get("request_id", "")) == request_id:
        return True

    return False

def adapter_ready_for_radio_request(adapter_model: Dict[str, Any]) -> bool:
    status = str(adapter_model.get("status") or "").strip().lower()
    control_mode = str(adapter_model.get("control_mode") or "").strip().lower()

    if control_mode in {"disabled", "dry_run"}:
        return False

    return boolish(adapter_model.get("available"), False) and status in RADIO_READY_STATUSES

def rejected_request_result(request_id: str, action: str, reason: str) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "action": action,
        "ok": False,
        "status": "rejected",
        "reason": reason,
        "operation_performed": False,
        "source": SOURCE,
        "updated_utc": utc_now_iso(),
    }


def process_request_if_needed(
    client: "redis.Redis",
    app: Dict[str, Any],
    remembered: Set[str],
    remembered_order: Deque[str],
) -> bool:
    request = read_json_key(client, KEY_ADAPTER_REQUEST)
    if not request:
        return False

    request_id = str(request.get("request_id") or "").strip()
    action = str(request.get("action") or "").strip()
    request_received_utc = utc_now_iso()

    if not request_id:
        request_id = f"missing-request-id-{utc_now_iso()}"
        result = rejected_request_result(request_id, action or "unknown", "Adapter request missing request_id.")
        adapter_model = build_adapter_status(app)
        publish_request_result(client, adapter_model, result, "adapter_request_rejected")
        remember_request_id(request_id, remembered, remembered_order)
        log("rejected request with missing request_id")
        return True

    if request_already_handled(client, request_id, remembered):
        return False

    config = IC2730AConfig.from_app_config(app)
    adapter = IC2730AAdapter(config)
    adapter_model_before_request = normalize_adapter_status(adapter.get_status())

    if action not in NO_RADIO_REQUIRED_ACTIONS and not adapter_ready_for_radio_request(adapter_model_before_request):   
        result = rejected_request_result(
            request_id,
            action or "unknown",
            "IC-2730A radio/control path unavailable; unsafe adapter request rejected.",
        )
        publish_request_result(
            client,
            adapter_model_before_request,
            result,
            "adapter_request_rejected_radio_unavailable",
        )
        remember_request_id(request_id, remembered, remembered_order)
        log(
            f"rejected request_id={request_id} action={action or 'unknown'} "
            f"because radio/control path is unavailable"
        )
        return True
    if action == "write_single_memory_test":
        result = adapter.write_single_memory_test(
            str(request.get("target_group") or ""),
            intish(request.get("target_channel"), -1, -1),
            request.get("payload") if isinstance(request.get("payload"), dict) else {},
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "side_a_tune_candidate_test":
        result = adapter.side_a_tune_candidate_test(
            request.get("candidate") if isinstance(request.get("candidate"), dict) else {},
            dry_run=boolish(request.get("dry_run"), True),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action in {"plan_cd_bank_reload", "plan_load_bank"}:
        repeaters = request.get("repeaters")
        result = adapter.plan_load_bank(
            str(request.get("target_group") or request.get("group") or ""),
            repeaters if isinstance(repeaters, list) else [],
            start_scan_after=boolish(request.get("start_scan_after"), False),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "plan_clear_bank":
        result = adapter.plan_clear_bank(
            str(request.get("target_group") or request.get("group") or ""),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "plan_program_channel":
        result = adapter.plan_program_channel(
            str(request.get("target_group") or request.get("group") or ""),
            intish(request.get("target_channel") or request.get("channel"), -1, -1),
            request.get("repeater") if isinstance(request.get("repeater"), dict) else {},
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "plan_start_memory_bank_scan":
        result = adapter.plan_start_memory_bank_scan(
            str(request.get("target_group") or request.get("group") or ""),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "query_scan_state":
        result = adapter.query_scan_state()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "query_active_bank":
        result = adapter.query_active_bank()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "query_active_memory_data":
        result = adapter.query_active_memory_data()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "direct_civ_memory_channel_read_proof":
        raw_target_channel = request.get("target_channel")
        if raw_target_channel is None:
            raw_target_channel = request.get("channel")

        result = adapter.direct_civ_memory_channel_read_proof(
            str(request.get("target_group") or request.get("group") or ""),
            intish(raw_target_channel, -1, -1),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "tune_repeater_vfo":
        result = adapter.tune_repeater_vfo(
            request.get("repeater") if isinstance(request.get("repeater"), dict) else {}
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "software_scan_step":
        result = adapter.software_scan_step(
            request.get("repeater") if isinstance(request.get("repeater"), dict) else {},
            dwell_ms=request.get("dwell_ms"),
            force_full_tune=boolish(request.get("force_full_tune"), False),
        )
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()
        timing = result.get("timing") if isinstance(result.get("timing"), dict) else {}
        timing.setdefault("request_received_utc", request_received_utc)
        timing["result_published_utc"] = utc_now_iso()
        result["timing"] = timing

    elif action == "reset_software_scan_cache":
        result = adapter.reset_software_scan_cache()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "read_squelch_status":
        result = adapter.read_squelch_status()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "read_rx_tx_status":
        result = adapter.read_rx_tx_status()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()

    elif action == "read_frequency":
        result = adapter.read_frequency()
        result["request_id"] = request_id
        result["action"] = action
        result["source"] = SOURCE
        result["updated_utc"] = utc_now_iso()        

    else:
        result = rejected_request_result(
            request_id,
            action or "unknown",
            f"Unknown or unsupported adapter request action: {action or 'missing'}",
        )

    adapter_model = adapter_model_before_request
    publish_request_result(client, adapter_model, result, "adapter_request_processed")
    remember_request_id(request_id, remembered, remembered_order)

    log(
        f"processed request_id={request_id} action={action or 'unknown'} "
        f"status={result.get('status')} operation_performed={result.get('operation_performed')}"
    )
    return True


def main() -> int:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    client: Optional["redis.Redis"] = None
    last_publish_mono = 0.0
    last_status_check_mono = 0.0
    remembered_request_ids: Set[str] = set()
    remembered_request_order: Deque[str] = deque()

    log("starting")

    while _running:
        sleep_seconds = DEFAULT_REQUEST_POLL_SECONDS

        try:
            app = load_app_config()
            publish_cfg = get_publish_config(app)

            interval_sec = int(publish_cfg["publish_interval_seconds"])
            force_publish_seconds = int(publish_cfg["force_publish_seconds"])
            sleep_seconds = int(publish_cfg["request_poll_seconds"])

            if client is None:
                client = get_redis_client(app)

            request_processed = process_request_if_needed(
                client,
                app,
                remembered_request_ids,
                remembered_request_order,
            )

            publish_due = (time.monotonic() - last_status_check_mono) >= interval_sec
            if publish_cfg["publish_adapter_status"] and publish_due:
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

                last_status_check_mono = time.monotonic()

            elif not publish_cfg["publish_adapter_status"] and publish_due:
                log("publish_adapter_status=false; request handling remains active")
                last_status_check_mono = time.monotonic()

        except Exception as exc:
            client = None
            warn(f"cycle failed: {exc}")

        sleep_until = time.monotonic() + float(sleep_seconds)
        while _running and time.monotonic() < sleep_until:
            remaining = sleep_until - time.monotonic()
            time.sleep(min(0.05, max(0.0, remaining)))

    log("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
