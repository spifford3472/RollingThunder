#!/usr/bin/env python3
"""
RollingThunder RF Intel Advisor Alert Mirror v1

Controller-side mirror from RF Intel advisor items into the existing alert model.

Reads only:
  - rt:rfintel:advisor
  - rt:alerts:active

Writes only:
  - rt:alerts:active
  - rt:rfintel:alert_mirror:state

Publishes only:
  - state.changed to rt:system:bus when rt:alerts:active changes

Does not:
  - write to rt:ui:bus
  - emit intents
  - tune the radio
  - create pending actions
  - scan Redis during normal operation
  - create a new voice/TTS architecture

Voice/TTS:
  No existing voice path was discovered during inspection, so voice integration is
  intentionally disabled in v1. This service only mirrors eligible RF Intel
  advisories into the existing alert overlay model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis
except ImportError:
    print("ERROR: python3 redis module is required. Try: sudo apt install python3-redis", file=sys.stderr)
    sys.exit(2)


REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

KEY_ADVISOR = os.environ.get("RT_KEY_RFINTEL_ADVISOR", "rt:rfintel:advisor")
KEY_ALERTS_ACTIVE = os.environ.get("RT_KEY_ALERTS_ACTIVE", "rt:alerts:active")
KEY_STATE = os.environ.get("RT_KEY_RFINTEL_ALERT_MIRROR_STATE", "rt:rfintel:alert_mirror:state")
SYSTEM_BUS = os.environ.get("RT_SYSTEM_BUS", "rt:system:bus")

SOURCE = "rfintel_advisor_alert_mirror"
SERVICE = "rfintel_advisor_alert_mirror"

DEFAULT_INTERVAL_SEC = float(os.environ.get("RT_RFINTEL_ALERT_MIRROR_INTERVAL_SEC", "5.0"))
DEFAULT_TTL_SEC = int(os.environ.get("RT_RFINTEL_ALERT_TTL_SEC", "300"))
MAX_ALERT_ITEMS = int(os.environ.get("RT_ALERTS_MAX_ITEMS", "20"))

RF_ALERT_PREFIX = "rfintel:"

RUNNING = True


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def on_signal(signum: int, frame: Any) -> None:
    global RUNNING
    RUNNING = False


def clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def safe_json_load(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def json_dumps_compact(obj: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=sort_keys)


def normalize_alert_items(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]

    if isinstance(obj, dict):
        if isinstance(obj.get("items"), list):
            return [x for x in obj["items"] if isinstance(x, dict)]
        if isinstance(obj.get("alerts"), list):
            return [x for x in obj["alerts"] if isinstance(x, dict)]

    return []


def stable_suffix(value: Any) -> str:
    s = clean_str(value).lower()
    out: List[str] = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", "_"):
            out.append(ch)
        elif ch in (" ", ".", "/", ":"):
            out.append("_")
    return "".join(out).strip("_") or "unknown"


def advisor_severity_to_alert_severity(severity: str) -> str:
    sev = clean_str(severity).lower()

    if sev in ("critical",):
        return "critical"
    if sev in ("error", "bad"):
        return "error"
    if sev in ("warning", "warn", "watch"):
        return "warn"
    if sev in ("ok", "info"):
        return sev

    return "warn"


def is_alert_worthy(item: Dict[str, Any]) -> bool:
    category = clean_str(item.get("category")).lower()
    severity = clean_str(item.get("severity")).lower()
    priority = to_int(item.get("priority"), 0)

    if severity in ("warning", "critical", "error", "bad"):
        return True

    if category == "solar" and severity in ("watch", "warning", "critical", "error", "bad"):
        return True

    if priority >= 90 and severity not in ("", "info", "ok"):
        return True

    return False


def alert_title_for(item: Dict[str, Any]) -> str:
    category = clean_str(item.get("category")).lower()
    severity = clean_str(item.get("severity")).lower()
    label = clean_str(item.get("label"))

    if category == "solar":
        if severity in ("critical", "error", "bad"):
            return "Critical solar conditions"
        return "Solar conditions unsettled"

    if category == "propagation":
        if severity in ("critical", "error", "bad"):
            return "Critical propagation advisory"
        return "Propagation advisory"

    if category in ("mobile", "mobile_advisor", "operating"):
        return "Mobile advisor warning"

    if category == "radio":
        return "Radio advisory"

    if label:
        return f"RF Intel: {label}"

    return "RF Intel advisory"


def build_alert_from_advisor_item(item: Dict[str, Any], *, created_ms: int, ttl_sec: int) -> Dict[str, Any]:
    advisor_id = clean_str(item.get("id"), "unknown")
    category = clean_str(item.get("category"), "propagation").lower()
    severity = clean_str(item.get("severity"), "warning").lower()
    priority = to_int(item.get("priority"), 0)

    alert_id = f"{RF_ALERT_PREFIX}{stable_suffix(advisor_id)}"
    message = clean_str(item.get("text") or item.get("message"), "RF Intel advisory requires attention.")
    details = clean_str(item.get("reason") or item.get("details") or "")
    when = clean_str(item.get("timestamp_utc") or item.get("updated_utc") or now_iso_utc())

    return {
        "id": alert_id,
        "title": alert_title_for(item),
        "message": message,
        "details": details,
        "severity": advisor_severity_to_alert_severity(severity),
        "level": advisor_severity_to_alert_severity(severity),
        "kind": category,
        "category": category,
        "priority": priority,
        "when": when,
        "source": SOURCE,
        "service": SERVICE,
        "created_ms": int(created_ms),
        "updated_ms": int(created_ms),
        "ttl_sec": int(ttl_sec),
        "expires_ms": int(created_ms + (ttl_sec * 1000)),
        "advisor_id": advisor_id,
    }


def semantic_alert(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return only the fields that define alert meaning.

    Ignore volatile timestamps and expiration so we avoid unnecessary writes.
    """
    keep = {}
    for key in (
        "id",
        "title",
        "message",
        "details",
        "severity",
        "level",
        "kind",
        "category",
        "priority",
        "source",
        "service",
        "advisor_id",
    ):
        if key in item:
            keep[key] = item[key]
    return keep


def semantic_fingerprint(items: List[Dict[str, Any]]) -> str:
    semantic_items = [semantic_alert(x) for x in items if isinstance(x, dict)]
    raw = json_dumps_compact(semantic_items, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def active_rf_alert_by_id(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        item_id = clean_str(item.get("id"))
        if item_id.startswith(RF_ALERT_PREFIX):
            out[item_id] = item
    return out


def read_json_object(r: redis.Redis, key: str) -> Tuple[Dict[str, Any], str]:
    try:
        raw = r.get(key)
    except Exception:
        return {}, "error"

    obj = safe_json_load(raw)
    if isinstance(obj, dict):
        return obj, "ok"
    if raw is None:
        return {}, "missing"
    return {}, "invalid"


def load_state(r: redis.Redis) -> Dict[str, Any]:
    obj, status = read_json_object(r, KEY_STATE)
    if status == "ok":
        return obj
    return {}


def write_state(r: redis.Redis, state: Dict[str, Any]) -> None:
    r.set(KEY_STATE, json_dumps_compact(state))


def build_desired_rf_alerts(advisor: Dict[str, Any], existing_rf: Dict[str, Dict[str, Any]], ttl_sec: int) -> List[Dict[str, Any]]:
    raw_items = advisor.get("items")
    if not isinstance(raw_items, list):
        return []

    created = now_ms()
    desired: List[Dict[str, Any]] = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        if not is_alert_worthy(raw):
            continue

        new_alert = build_alert_from_advisor_item(raw, created_ms=created, ttl_sec=ttl_sec)
        old_alert = existing_rf.get(clean_str(new_alert.get("id")))

        # If the same semantic alert is already active, keep the existing item so
        # we do not refresh the TTL or churn timestamps every loop.
        if old_alert and semantic_alert(old_alert) == semantic_alert(new_alert):
            desired.append(old_alert)
        else:
            desired.append(new_alert)

    desired.sort(key=lambda x: to_int(x.get("priority"), 0), reverse=True)
    return desired


def publish_state_changed(r: redis.Redis, keys: List[str]) -> None:
    if not keys:
        return

    msg = {
        "type": "state.changed",
        "source": SOURCE,
        "keys": keys,
        "changed_keys": keys,
        "timestamp_ms": now_ms(),
        "timestamp_utc": now_iso_utc(),
    }

    try:
        r.publish(SYSTEM_BUS, json_dumps_compact(msg))
    except Exception:
        # State is still written; bus publish failure should not crash the mirror.
        pass


def reconcile_once(r: redis.Redis, *, ttl_sec: int, dry_run: bool = False, verbose: bool = False) -> bool:
    advisor, advisor_status = read_json_object(r, KEY_ADVISOR)
    alerts_obj, alerts_status = read_json_object(r, KEY_ALERTS_ACTIVE)

    if advisor_status != "ok":
        if verbose:
            print(f"RF advisor unavailable: {advisor_status}")
        return False

    existing_items = normalize_alert_items(alerts_obj)
    existing_rf = active_rf_alert_by_id(existing_items)

    desired_rf = build_desired_rf_alerts(advisor, existing_rf, ttl_sec)

    non_rf_items = [
        item for item in existing_items
        if not clean_str(item.get("id")).startswith(RF_ALERT_PREFIX)
    ]

    new_items = desired_rf + non_rf_items
    new_items = new_items[:MAX_ALERT_ITEMS]

    old_fp = semantic_fingerprint(existing_items)
    new_fp = semantic_fingerprint(new_items)

    if old_fp == new_fp:
        if verbose:
            print(f"No alert semantic change. RF alerts active: {len(desired_rf)}")
        return False

    payload = {
        "items": new_items,
        "last_update_ms": now_ms(),
    }

    if verbose:
        print(f"Advisor status: {advisor_status}")
        print(f"Existing alerts: {len(existing_items)}")
        print(f"Desired RF alerts: {len(desired_rf)}")
        print(f"New total alerts: {len(new_items)}")
        if dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))

    if dry_run:
        return False

    r.set(KEY_ALERTS_ACTIVE, json_dumps_compact(payload))
    publish_state_changed(r, [KEY_ALERTS_ACTIVE])

    # Store a compact diagnostic state. This is not used by the UI.
    state = {
        "source": SOURCE,
        "status": "ok",
        "updated_utc": now_iso_utc(),
        "updated_ms": now_ms(),
        "rf_alert_count": len(desired_rf),
        "total_alert_count": len(new_items),
        "alerts_key": KEY_ALERTS_ACTIVE,
        "advisor_key": KEY_ADVISOR,
        "voice_status": "disabled_no_existing_voice_path_found",
    }
    write_state(r, state)

    return True


def connect_redis() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
        retry_on_timeout=True,
    )
    r.ping()
    return r


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one reconciliation pass and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended payload but do not write Redis.")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostic output.")
    parser.add_argument("--interval-sec", type=float, default=DEFAULT_INTERVAL_SEC)
    parser.add_argument("--ttl-sec", type=int, default=DEFAULT_TTL_SEC)

    args = parser.parse_args()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    r = connect_redis()

    if args.once:
        changed = reconcile_once(r, ttl_sec=max(1, args.ttl_sec), dry_run=args.dry_run, verbose=args.verbose)
        if args.verbose:
            print(f"changed={changed}")
        return 0

    while RUNNING:
        try:
            reconcile_once(r, ttl_sec=max(1, args.ttl_sec), dry_run=False, verbose=bool(args.verbose))
        except Exception as exc:
            if args.verbose:
                print(f"ERROR: {exc}", file=sys.stderr)

        slept = 0.0
        interval = max(1.0, float(args.interval_sec))
        while RUNNING and slept < interval:
            time.sleep(0.25)
            slept += 0.25

    return 0


if __name__ == "__main__":
    raise SystemExit(main())