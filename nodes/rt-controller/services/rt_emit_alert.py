#!/usr/bin/env python3
"""
RollingThunder - Alert Emitter (rt-controller)

Writes to:
- rt:alerts:active (Redis string containing JSON {items:[...]}), owned by rt-controller

Design:
- bounded list (default 20)
- safe under restart/power loss
- tolerant of bad existing data
- no UI writes, no control actions
"""

from __future__ import annotations

import argparse
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
MAX_ITEMS = int(os.environ.get("RT_ALERTS_MAX_ITEMS", "20"))

def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _safe_json_load(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def _normalize_items(obj: Any) -> List[Dict[str, Any]]:
    # Accept: {items:[...]}, {alerts:[...]}, or [...] directly
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        if isinstance(obj.get("items"), list):
            return [x for x in obj["items"] if isinstance(x, dict)]
        if isinstance(obj.get("alerts"), list):
            return [x for x in obj["alerts"] if isinstance(x, dict)]
    return []

def _dedup_keep_first(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        _id = str(it.get("id") or "")
        if not _id:
            continue
        if _id in seen:
            continue
        seen.add(_id)
        out.append(it)
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--severity", default="warn", choices=["ok", "info", "warn", "bad", "critical", "error"])
    ap.add_argument("--kind", default="alert")
    ap.add_argument("--when", default=None)          # ISO UTC; defaults now
    ap.add_argument("--source", default="rt-controller")
    ap.add_argument("--service", default=None)
    ap.add_argument("--id", default=None)            # if omitted, we generate a stable-ish id
    args = ap.parse_args()

    when = args.when or now_iso_utc()
    sid = args.service or ""
    base_id = args.id or f"{args.source}:{sid}:{when}:{args.title}"

    item: Dict[str, Any] = {
        "id": base_id,
        "title": args.title,
        "message": args.message,
        "severity": args.severity,
        "kind": args.kind,
        "when": when,
        "source": args.source,
    }
    if args.service:
        item["service"] = args.service

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

    # Read existing
    existing_raw = r.get(KEY_ALERTS_ACTIVE)
    existing_obj = _safe_json_load(existing_raw)
    items = _normalize_items(existing_obj)

    # Prepend new item, dedup, bound
    items = [item] + items
    items = _dedup_keep_first(items)
    items = items[:MAX_ITEMS]

    payload = {"items": items, "last_update_ms": int(time.time() * 1000)}
    r.set(KEY_ALERTS_ACTIVE, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

if __name__ == "__main__":
    main()