#!/usr/bin/env python3
"""
RollingThunder - Alerts Reconciler (rt-controller)

Purpose:
- Periodically prunes rt:alerts:active.items[]:
  - expires_ms reached (TTL)
  - clearsOn rules satisfied (optional)
- Bounded, read-only except for writing rt:alerts:active
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

KEY_ALERTS_ACTIVE = os.environ.get("RT_KEY_ALERTS_ACTIVE", "rt:alerts:active")

# NEW: where systemd unit state is written (your service_state_publisher owns this)
KEY_SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")

POLL_SEC = float(os.environ.get("RT_ALERTS_RECONCILE_SEC", "1.0"))
MAX_STR = int(os.environ.get("RT_ALERTS_MAX_JSON_CHARS", "200000"))  # safety cap on stored JSON


def now_ms() -> int:
    return int(time.time() * 1000)


def _safe_json_load(s: Optional[str]) -> Any:
    if not s:
        return None
    if len(s) > MAX_STR:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _normalize_items(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, dict) and isinstance(obj.get("items"), list):
        return [x for x in obj["items"] if isinstance(x, dict)]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def _service_state_ok(r: redis.Redis, service_id: str) -> bool:
    """
    Interprets rt:services:<id> hash. We keep this intentionally loose:
    - state in ("running","active") => ok
    - status in ("ok") => ok
    Anything else => not ok
    """
    key = f"{KEY_SERVICE_PREFIX}{service_id}"
    try:
        if r.type(key) != "hash":
            return False
        h = r.hgetall(key)  # decode_responses=True
        st = str(h.get("state") or "").lower()
        status = str(h.get("status") or "").lower()
        return (st in ("running", "active")) or (status == "ok")
    except Exception:
        return False


def _should_clear(r: redis.Redis, alert: Dict[str, Any], t_ms: int) -> bool:
    # TTL
    exp = alert.get("expires_ms")
    if isinstance(exp, int) and exp > 0 and t_ms >= exp:
        return True

    # clearsOn (metadata-driven)
    clears = alert.get("clearsOn")
    if isinstance(clears, dict):
        typ = str(clears.get("type") or "").strip()
        params = clears.get("params") if isinstance(clears.get("params"), dict) else {}

        if typ == "service_ok":
            svc = str((params or {}).get("service") or "").strip()
            if svc and _service_state_ok(r, svc):
                return True

        # You can add more rule types later without breaking anything:
        # - "gps_has_fix"
        # - "noaa_quiet"
        # - etc.

    return False


def main() -> None:
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

    while True:
        try:
            raw = r.get(KEY_ALERTS_ACTIVE)
            obj = _safe_json_load(raw)
            items = _normalize_items(obj)
            if not items:
                time.sleep(POLL_SEC)
                continue

            t = now_ms()
            kept: List[Dict[str, Any]] = []
            changed = False

            for it in items:
                if _should_clear(r, it, t):
                    changed = True
                else:
                    kept.append(it)

            if changed:
                payload = {"items": kept, "last_update_ms": t}
                r.set(KEY_ALERTS_ACTIVE, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

        except Exception:
            # don't die; this is housekeeping
            pass

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()