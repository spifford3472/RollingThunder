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

Enhancements:
- optional TTL: --ttl-sec N  -> adds created_ms + ttl_sec + expires_ms
- optional clearsOn metadata: --clears-on <type> [--clear-param k=v ...]
- dedup by id; if --refresh-existing is set and same id exists, we update it in-place
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

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


def now_ms() -> int:
    return int(time.time() * 1000)


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


def _parse_kv_list(kvs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in kvs or []:
        s = str(raw).strip()
        if not s or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # bound keys/values a little (avoid surprises)
        if len(k) > 64 or len(v) > 256:
            continue
        out[k] = v
    return out


def _upsert_item(items: List[Dict[str, Any]], new_item: Dict[str, Any], refresh_existing: bool) -> List[Dict[str, Any]]:
    new_id = str(new_item.get("id") or "").strip()
    if not new_id:
        return items

    out: List[Dict[str, Any]] = []
    replaced = False

    for it in items:
        it_id = str(it.get("id") or "").strip()
        if not it_id:
            continue
        if it_id == new_id:
            if refresh_existing:
                out.append(new_item)
            else:
                out.append(it)  # keep old
            replaced = True
        else:
            out.append(it)

    if not replaced:
        out = [new_item] + out

    # bound
    return out[:MAX_ITEMS]


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

    # NEW
    ap.add_argument("--ttl-sec", type=int, default=None, help="Auto-expire after N seconds (adds expires_ms).")
    ap.add_argument("--clears-on", default=None, help="Clearing rule type (metadata only; reconciler enforces).")
    ap.add_argument("--clear-param", action="append", default=[], help="Key=Value params for clearsOn.")
    ap.add_argument(
        "--refresh-existing",
        action="store_true",
        help="If an alert with the same id already exists, overwrite it (useful to extend TTL).",
    )

    args = ap.parse_args()

    when = args.when or now_iso_utc()
    sid = args.service or ""
    base_id = args.id or f"{args.source}:{sid}:{when}:{args.title}"

    created = now_ms()
    ttl_sec = args.ttl_sec if (args.ttl_sec is not None and args.ttl_sec > 0) else None
    expires_ms = (created + int(ttl_sec * 1000)) if ttl_sec is not None else None

    item: Dict[str, Any] = {
        "id": base_id,
        "title": args.title,
        "message": args.message,
        "severity": args.severity,
        "kind": args.kind,
        "when": when,
        "source": args.source,
        "created_ms": created,
    }
    if args.service:
        item["service"] = args.service
    if ttl_sec is not None:
        item["ttl_sec"] = int(ttl_sec)
        item["expires_ms"] = int(expires_ms or 0)

    if args.clears_on:
        params = _parse_kv_list(args.clear_param)
        item["clearsOn"] = {"type": str(args.clears_on).strip(), "params": params}

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

    existing_raw = r.get(KEY_ALERTS_ACTIVE)
    existing_obj = _safe_json_load(existing_raw)
    items = _normalize_items(existing_obj)

    items = _upsert_item(items, item, refresh_existing=bool(args.refresh_existing))

    payload = {"items": items, "last_update_ms": now_ms()}
    r.set(KEY_ALERTS_ACTIVE, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()