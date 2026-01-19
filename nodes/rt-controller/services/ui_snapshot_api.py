#!/usr/bin/env python3
"""
RollingThunder - UI Snapshot API (rt-controller)

Phase 12:
- Build a read-only UI snapshot from Redis structured state.
- No writes, no control loops.
- Bounded output to protect UI + network.
- CORS enabled for rt-display polling.

Redis model (observed):
- rt:system:health  (hash)
- rt:system:nodes   (set of node ids)
- rt:nodes:<id>     (hash per node)
- rt:services:*     (hash per service)
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

import redis


HOST = os.environ.get("RT_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("RT_UI_PORT", "8625"))

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

SNAPSHOT_PATHS = ("/api/v1/ui/snapshot", "/api/v1/ui/snapshot/")

# Observed base keys
KEY_SYSTEM_HEALTH = os.environ.get("RT_KEY_SYSTEM_HEALTH", "rt:system:health")
KEY_SYSTEM_NODES = os.environ.get("RT_KEY_SYSTEM_NODES", "rt:system:nodes")
KEY_NODE_PREFIX = os.environ.get("RT_KEY_NODE_PREFIX", "rt:nodes:")
KEY_SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")

# Output bounds
MAX_STR_CHARS = int(os.environ.get("RT_MAX_STR_CHARS", "20000"))
MAX_SERVICES = int(os.environ.get("RT_MAX_SERVICES", "50"))
MAX_SERVICE_FIELDS = int(os.environ.get("RT_MAX_SERVICE_FIELDS", "60"))


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _try_parse_json(s: Optional[str]) -> Any:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # Quick gate: only attempt if it looks like JSON
    if not (s.startswith("{") or s.startswith("[") or s.startswith('"')):
        return s
    try:
        return json.loads(s)
    except Exception:
        return s


def _truncate(value: Any, max_chars: int = MAX_STR_CHARS) -> Any:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return value if len(value) <= max_chars else value[:max_chars] + "…"
        dumped = json.dumps(value, ensure_ascii=False)
        if len(dumped) <= max_chars:
            return value
        return {"_truncated": True, "_preview": dumped[:max_chars] + "…"}
    except Exception:
        return {"_truncated": True, "_preview": str(value)[:max_chars] + "…"}


def _hgetall_parsed(r: redis.Redis, key: str) -> Dict[str, Any]:
    """
    Read a Redis hash and attempt to parse any JSON-ish values.
    """
    raw = r.hgetall(key)  # decode_responses=True => Dict[str,str]
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        out[k] = _truncate(_try_parse_json(v))
    return out


def _service_summary_fields(h: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep service info tight and predictable for UI.
    """
    keep = ("id", "scope", "ownerNode", "startPolicy", "stopPolicy", "state", "last_update_ms")
    return {k: h.get(k) for k in keep if k in h}


class UiSnapshotHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path not in SNAPSHOT_PATHS:
            self.send_response(404)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not_found"}')
            return

        payload: Dict[str, Any] = {
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/snapshot",
            "ts": now_iso_utc(),
            "ok": False,
            "data": {},
            "errors": [],
        }

        try:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                decode_responses=True,
                socket_timeout=REDIS_TIMEOUT,
                socket_connect_timeout=REDIS_TIMEOUT,
            )
            r.ping()

            # System health
            system_health = _hgetall_parsed(r, KEY_SYSTEM_HEALTH)

            # Nodes
            nodes: Dict[str, Any] = {}
            try:
                node_ids = sorted(list(r.smembers(KEY_SYSTEM_NODES)))
            except Exception:
                node_ids = []

            for nid in node_ids:
                nk = f"{KEY_NODE_PREFIX}{nid}"
                # Guard: only read if it exists to avoid surprises
                if r.exists(nk):
                    nodes[nid] = _hgetall_parsed(r, nk)

            # Services (hashes under rt:services:*)
            services: Dict[str, Any] = {}
            # Scan is safe; bounded by MAX_SERVICES
            count = 0
            for key in r.scan_iter(match=f"{KEY_SERVICE_PREFIX}*"):
                if count >= MAX_SERVICES:
                    break
                if r.type(key) != "hash":
                    continue
                h = _hgetall_parsed(r, key)
                # Use either explicit id field or key suffix as identifier
                sid = str(h.get("id") or key.split(":", 2)[-1])
                services[sid] = _service_summary_fields(h)
                count += 1

            payload["data"] = {
                "system": {
                    "health": system_health,
                    "nodes": nodes,
                },
                "services": services,
            }
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"snapshot_failed: {type(e).__name__}: {e}")

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    httpd = HTTPServer((HOST, PORT), UiSnapshotHandler)
    print(f"ui_snapshot_api listening on {HOST}:{PORT} (redis {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
