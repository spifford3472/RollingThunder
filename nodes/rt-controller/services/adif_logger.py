#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import redis

import qso_adif
import qso_normalize
import qso_rules
import qso_storage
import rt_config


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
UI_BUS_CH = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")
NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")

# Conservative defaults; override with env if your live keys differ.
RADIO_STATE_KEY = os.environ.get("RT_QSO_RADIO_STATE_KEY", "rt:radio:state")
OPERATOR_STATE_KEY = os.environ.get("RT_QSO_OPERATOR_STATE_KEY", "rt:operator:state")
MOTION_STATE_KEY = os.environ.get("RT_QSO_MOTION_STATE_KEY", "rt:motion:state")
GPS_POS_KEY = os.environ.get("RT_QSO_GPS_POS_KEY", "rt:gps:pos")

RESULT_TOPIC = "ui.radio.log_qso.result"
HELLO_TOPIC = "ui.adif_logger.hello"

DUPLICATE_LOOKBACK = int(os.environ.get("RT_QSO_DUPLICATE_LOOKBACK", "100"))
POLL_TIMEOUT_SEC = float(os.environ.get("RT_QSO_PUBSUB_TIMEOUT_SEC", "1.0"))
IDLE_SLEEP_SEC = float(os.environ.get("RT_QSO_IDLE_SLEEP_SEC", "0.05"))



logger = logging.getLogger("adif_logger")

DIGITAL_SUBMODES = {
    "FT8", "FT4", "JS8", "JS8CALL",
    "PSK31", "PSK63", "PSK125",
    "RTTY", "MFSK", "OLIVIA",
    "THOR", "CONTESTIA",
    "JT65", "JT9",
    "VARA", "WINMOR"
}

CW_SUBMODES = {
    "CW", "CWU", "CWL"
}

SSB_SUBMODES = {
    "USB", "LSB"
}


def normalize_adif_mode(mode: str | None, submode: str | None) -> tuple[str, str]:
    """
    Normalize mode/submode into ADIF-friendly form.

    Returns:
        (mode, submode)
    """
    logger.info("intent params snapshot=%r", params)
    m = (mode or "").strip().upper()
    sm = (submode or "").strip().upper()

    # Prefer submode if present
    key = sm or m

    if key in SSB_SUBMODES:
        return "SSB", key

    if key in CW_SUBMODES:
        return "CW", ""

    if key in DIGITAL_SUBMODES:
        return "DIGI", key

    # Already a valid ADIF mode (AM, FM, etc.)
    return m, sm

def utc_now_iso_z() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    r.ping()
    return r


def publish_bus(r: redis.Redis, payload: Mapping[str, Any]) -> None:
    r.publish(UI_BUS_CH, compact_json(dict(payload)))


def load_app_config() -> Dict[str, Any]:
    cfg = rt_config.load_app_config()
    return cfg if isinstance(cfg, dict) else {}


def get_program_version() -> str:
    if hasattr(rt_config, "get_program_version"):
        try:
            value = rt_config.get_program_version()
            if value is not None and str(value).strip():
                return str(value).strip()
        except Exception:
            logger.exception("get_program_version() failed; falling back to config")

    cfg = load_app_config()
    runtime_version = cfg.get("runtime", {}).get("version")
    if runtime_version is not None and str(runtime_version).strip():
        return str(runtime_version).strip()

    top_level_version = cfg.get("version")
    if top_level_version is not None and str(top_level_version).strip():
        return str(top_level_version).strip()

    return "0.3700"


def _coerce_obj_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _coerce_hash(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not raw:
        return {}
    return {str(k): v for k, v in raw.items()}


def _load_hash_or_json(r: redis.Redis, key: str) -> Dict[str, Any]:
    """
    Read a Redis key that may be either:
    - a hash
    - a JSON string stored at the key
    - absent
    """
    try:
        raw_type = r.type(key)
    except Exception:
        logger.exception("Redis TYPE failed for key=%s", key)
        return {}

    try:
        if raw_type == "hash":
            return _coerce_hash(r.hgetall(key))
        if raw_type == "string":
            raw = r.get(key)
            if not raw:
                return {}
            try:
                obj = json.loads(raw)
                return _coerce_obj_dict(obj)
            except Exception:
                logger.warning("Key %s contains non-JSON string; ignoring", key)
                return {}
    except Exception:
        logger.exception("Redis read failed for key=%s type=%s", key, raw_type)
        return {}

    return {}


def _read_live_state(r: redis.Redis) -> Dict[str, Dict[str, Any]]:
    return {
        "radio_state": _load_hash_or_json(r, RADIO_STATE_KEY),
        "operator_state": _load_hash_or_json(r, OPERATOR_STATE_KEY),
        "motion_state": _load_hash_or_json(r, MOTION_STATE_KEY),
        "gps_pos": _load_hash_or_json(r, GPS_POS_KEY),
    }

def _warn_if_state_missing(live: dict[str, dict[str, Any]]) -> None:
    missing = []

    if not live["radio_state"]:
        missing.append(f"radio_state:{RADIO_STATE_KEY}")
    if not live["operator_state"]:
        missing.append(f"operator_state:{OPERATOR_STATE_KEY}")
    if not live["motion_state"]:
        missing.append(f"motion_state:{MOTION_STATE_KEY}")

    if missing:
        logger.warning("QSO logging proceeding with missing live state: %s", ", ".join(missing))

def _extract_intent_name(message: Mapping[str, Any]) -> str:
    return str(message.get("intent") or "").strip()


def _extract_intent_params(message: Mapping[str, Any]) -> Dict[str, Any]:
    params = message.get("params")
    if isinstance(params, dict):
        return dict(params)

    # Graceful fallback for flatter envelopes.
    if isinstance(message, dict):
        out = dict(message)
        out.pop("intent", None)
        return out

    return {}


def _basic_call_from_params(params: Mapping[str, Any]) -> str:
    return str(params.get("call") or "").strip().upper()


def _ensure_adif_header_once(program_version: str) -> Path:
    """
    Ensure the ADIF file exists and contains the qso_adif-rendered header exactly once.
    """
    path = qso_storage.get_adif_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists() or path.stat().st_size == 0:
        header = qso_adif.render_adif_header(program_version)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(header)
            if header and not header.endswith("\n"):
                fh.write("\n")
            fh.flush()

    return path


def _result_base() -> Dict[str, Any]:
    return {
        "topic": RESULT_TOPIC,
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "ts_utc": utc_now_iso_z(),
    }


def _publish_success(
    r: redis.Redis,
    qso: Mapping[str, Any],
    *,
    exported_records: int,
) -> None:

    mode, submode = normalize_adif_mode(
        qso.get("mode"),
        qso.get("submode")
    )

    payload = {
        **_result_base(),
        "ok": True,
        "qso_id": qso.get("qso_id", ""),
        "call": qso.get("call", ""),
        "band": qso.get("band", ""),
        "mode": mode,
        "submode": submode,
        "duplicate_suspected": bool(qso.get("duplicate_suspected", False)),
        "exported_records": int(exported_records),
    }

    publish_bus(r, payload)


def _publish_error(
    r: redis.Redis,
    *,
    error_message: str,
    call: str = "",
) -> None:
    payload = {
        **_result_base(),
        "ok": False,
        "error": error_message,
        "call": call,
    }
    publish_bus(r, payload)


def _render_adif_text(records: list[str]) -> str:
    if not records:
        return ""
    return "\n".join(records) + "\n"


def process_radio_log_qso_intent(
    r: redis.Redis,
    message_obj: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Process one radio.log_qso intent end-to-end.

    Returns the canonical ruled QSO for logging/testability.
    Raises on failure so the caller can publish a contained error result.
    """
    intent_name = _extract_intent_name(message_obj)
    if intent_name != "radio.log_qso":
        raise ValueError(f"unexpected intent: {intent_name!r}")

    params = _extract_intent_params(message_obj)
    live = _read_live_state(r)
    logger.info(
        "live state keys radio=%s operator=%s motion=%s gps=%s",
        RADIO_STATE_KEY, OPERATOR_STATE_KEY, MOTION_STATE_KEY, GPS_POS_KEY,
    )
    logger.info(
        "live state snapshot radio=%r operator=%r motion=%r gps=%r",
        live["radio_state"], live["operator_state"], live["motion_state"], live["gps_pos"],
    )

    if not live["radio_state"]:
        raise RuntimeError(f"missing radio state at {RADIO_STATE_KEY}")

    if not live["operator_state"]:
        raise RuntimeError(f"missing operator state at {OPERATOR_STATE_KEY}")
    _warn_if_state_missing(live)

    logger.info(
        "Processing radio.log_qso call=%s freq_hz=%s mode=%s",
        params.get("call", ""),
        live["radio_state"].get("freq_hz", ""),
        live["radio_state"].get("mode", ""),
    )

    normalized_qso = qso_normalize.normalize_qso_intent(
        params,
        live["radio_state"],
        live["operator_state"],
        live["motion_state"],
        live["gps_pos"],
    )


    probable_duplicates = qso_storage.find_probable_duplicates(
        normalized_qso,
        limit=DUPLICATE_LOOKBACK,
    )

    ruled_qso = qso_rules.apply_qso_rules(
        normalized_qso,
        recent_qsos=probable_duplicates,
    )

    qso_storage.append_canonical_qso(ruled_qso)

    program_version = get_program_version()
    _ensure_adif_header_once(program_version)

    adif_records = qso_adif.canonical_qso_to_adif_records(ruled_qso)
    qso_storage.append_adif_text(_render_adif_text(adif_records))

    _publish_success(
        r,
        ruled_qso,
        exported_records=len(adif_records),
    )

    logger.info(
        "Logged QSO qso_id=%s call=%s band=%s mode=%s duplicate=%s exported_records=%d",
        ruled_qso.get("qso_id", ""),
        ruled_qso.get("call", ""),
        ruled_qso.get("band", ""),
        ruled_qso.get("mode", ""),
        ruled_qso.get("duplicate_suspected", False),
        len(adif_records),
    )

    return dict(ruled_qso)


def configure_logging() -> None:
    level_name = os.environ.get("RT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    configure_logging()
    logger.info(
        "Starting adif_logger node=%s redis=%s:%s/%s intents=%s ui_bus=%s",
        NODE_ID,
        REDIS_HOST,
        REDIS_PORT,
        REDIS_DB,
        INTENTS_CH,
        UI_BUS_CH,
    )

    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_bus(
        r,
        {
            "topic": HELLO_TOPIC,
            "node": NODE_ID,
            "ts_ms": now_ms(),
            "intents_channel": INTENTS_CH,
            "ui_bus_channel": UI_BUS_CH,
            "radio_state_key": RADIO_STATE_KEY,
            "operator_state_key": OPERATOR_STATE_KEY,
            "motion_state_key": MOTION_STATE_KEY,
            "gps_pos_key": GPS_POS_KEY,
            "duplicate_lookback": DUPLICATE_LOOKBACK,
        },
    )

    while True:
        try:
            msg = ps.get_message(timeout=POLL_TIMEOUT_SEC)
        except redis.RedisError:
            logger.exception("Pub/sub read failed; sleeping before retry")
            time.sleep(1.0)
            continue

        if not msg or msg.get("type") != "message":
            time.sleep(IDLE_SLEEP_SEC)
            continue

        raw = msg.get("data")
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            logger.warning("Ignoring malformed JSON intent payload: %r", raw)
            continue

        intent_name = _extract_intent_name(obj)
        if intent_name != "radio.log_qso":
            continue

        params = _extract_intent_params(obj)
        call = _basic_call_from_params(params)

        try:
            process_radio_log_qso_intent(r, obj)
        except Exception as exc:
            logger.exception("radio.log_qso processing failed")
            _publish_error(
                r,
                error_message=f"{type(exc).__name__}: {exc}",
                call=call,
            )


if __name__ == "__main__":
    main()