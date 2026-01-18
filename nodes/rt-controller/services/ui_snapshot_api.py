#!/usr/bin/env python3
"""
ui_snapshot_api.py — RollingThunder (rt-controller)

Phase 11b bring-up service:
- Exposes a minimal read-only endpoint for rt-display polling
- Adds permissive CORS headers to avoid browser cross-origin blocking
- Intentionally returns a small, static JSON payload for now

Later (next phase):
- Replace the payload with a real UI snapshot derived from Redis state
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Tuple


# Bind / port are overridable so you can run multiple services without collisions.
HOST = os.environ.get("RT_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("RT_UI_PORT", "8625"))

# Endpoint path for the display to poll.
SNAPSHOT_PATHS = ("/api/v1/ui/snapshot", "/api/v1/ui/snapshot/")


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_bytes(payload: Dict[str, Any]) -> Tuple[bytes, int]:
    body = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
    return body, len(body)


class UiSnapshotHandler(BaseHTTPRequestHandler):
    # Keep logs quiet; systemd/journalctl already captures service stdout/stderr.
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_cors_headers(self) -> None:
        # Read-only polling. Permissive CORS is acceptable for bring-up.
        # If you later want to constrain this, you can set an allow-list.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        # Preflight response for browsers.
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
            "ok": True,
            "note": (
                "Phase 11b integration endpoint. "
                "Replace payload with real UI snapshot (Redis-backed) later."
            ),
        }

        body, n = json_bytes(payload)
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(n))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    httpd = HTTPServer((HOST, PORT), UiSnapshotHandler)
    print(f"ui_snapshot_api listening on {HOST}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
