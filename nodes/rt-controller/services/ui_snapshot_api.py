#!/usr/bin/env python3
"""
ui_snapshot_api.py — RollingThunder (rt-controller)

Phase 12:
- Read-only UI snapshot from Redis
- Bounded payload (avoid huge blobs)
- CORS enabled for browser polling from rt-display
- No writes, no control loops, no side effects
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple

import redis  # pip: redis


HOST = os.environ.get("RT_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("RT_UI_PORT", "8625"))

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_SOCKET_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

# Existing state keys (no new schema): adjust only if your committed STATE_KEYS say otherwise.
STATE_CURRENT_PAGE = os.environ.get("RT_STATE_CURRENT_PAGE", "ui:current_page")
STATE_FOCUS_PANEL = os.environ.get("RT_STATE_FOCUS_PANEL", "ui:focus_panel")
STATE_ALERTS_JSON = os.environ.get("RT_STATE_ALERTS_JSON", "alerts:active_json")
STATE_NODE_HEALTH_JSON = os.environ.get("RT_STATE_NODE_HEALTH_JSON", "system:nodes_health_json")
STATE_GPS_JSON = os.environ.get("RT_STATE_GPS_JSON", "gps:fix_json")

SNAPSHOT_PATHS = ("/api/v1/ui/snapshot", "/api/v1/ui/snapshot/")


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_json_loads(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _truncate(value: Any, max_chars: int = 20_000) -> Any:
    """
    Ensure we don't accidentally return megabytes (a UI killer).
    - Strings: truncate
    - Dict/list: JSON-dump and truncate (best-effort)
    """
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return value if len(value) <= max_chars else value[:max_chars] + "…"
        # dict/list or other JSONable
        dumped = json.dumps(value, ensure_ascii=False)
        if len(dumped) <= max_chars:
            return value
        # If huge, return truncated string form + marker
        return {"_truncated": True, "_preview": dumped[:max_chars] + "…"}
    except Exception:
        return {"_truncated": True, "_preview": str(value)[:max_chars] + "…"}


class UiSnapshotHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path not in SNAPSHOT_PATHS:
            self.send_response(404)
            self._send_cors_headers()
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
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
            )

            # Ping is cheap and gives a clear fail-fast.
            r.ping()

            # Read a small set of keys. Missing keys are OK.
            page = r.get(STATE_CURRENT_PAGE)
            focus = r.get(STATE_FOCUS_PANEL)

            alerts_raw = r.get(STATE_ALERTS_JSON)
            health_raw = r.get(STATE_NODE_HEALTH_JSON)
            gps_raw = r.get(STATE_GPS_JSON)

            alerts = _safe_json_loads(alerts_raw)
            health = _safe_json_loads(health_raw)
            gps = _safe_json_loads(gps_raw)

            payload["data"] = {
                "ui": {
                    "current_page": page,
                    "focus_panel": focus,
                },
                "alerts": _truncate(alerts, 15_000),
                "system": {
                    "nodes_health": _truncate(health, 15_000),
                },
                "gps": _truncate(gps, 10_000),
            }
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"redis_read_failed: {type(e).__name__}: {e}")

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers()
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
