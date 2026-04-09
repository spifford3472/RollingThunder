#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socketserver
import sys
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import redis
from redis.exceptions import RedisError

RT_NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")
RT_CONFIG_PATH = Path(os.environ.get("RT_CONFIG_PATH", "/opt/rollingthunder/config/app.json"))
RT_UI_INTENTS_CHANNEL = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
RT_VIRTUAL_PANEL_UI_DIR = Path(
    os.environ.get("RT_VIRTUAL_PANEL_UI_DIR", "/opt/rollingthunder/ui/virtual-panel")
)
RT_REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
RT_REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
RT_REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
RT_REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

KEY_UI_LED_SNAPSHOT = "rt:ui:led_snapshot"

_running = True


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _handle_signal(signum: int, frame: Any) -> None:
    global _running
    _running = False


def load_app_config() -> dict[str, Any]:
    try:
        return json.loads(RT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log_err(f"failed to load app config {RT_CONFIG_PATH}: {type(exc).__name__}: {exc}")
        return {}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return default


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s or default


@dataclass(frozen=True)
class ServerConfig:
    enabled: bool
    bind: str
    port: int
    poll_ms: int
    ui_dir: Path


def load_server_config() -> ServerConfig:
    app_cfg = load_app_config()
    vp = as_dict(app_cfg.get("virtualPanel"))

    return ServerConfig(
        enabled=as_bool(vp.get("enabled"), False),
        bind=as_str(vp.get("bind"), "0.0.0.0"),
        port=as_int(vp.get("port"), 8630),
        poll_ms=as_int(vp.get("pollMs"), 200),
        ui_dir=RT_VIRTUAL_PANEL_UI_DIR,
    )


class RedisManager:
    def __init__(self) -> None:
        self.client: redis.Redis | None = None

    def get(self) -> redis.Redis:
        if self.client is not None:
            try:
                self.client.ping()
                return self.client
            except RedisError:
                self.client = None

        self.client = redis.Redis(
            host=RT_REDIS_HOST,
            port=RT_REDIS_PORT,
            db=RT_REDIS_DB,
            password=RT_REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=15,
        )
        self.client.ping()
        return self.client


def jsonish_load(value: str | None) -> Any:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def read_led_snapshot(r: redis.Redis) -> dict[str, Any]:
    try:
        raw = r.get(KEY_UI_LED_SNAPSHOT)
    except RedisError:
        return {
            "schema": 1,
            "type": "led_snapshot",
            "ts_ms": int(time.time() * 1000),
            "leds": {},
            "show_push": None,
        }

    obj = jsonish_load(raw)
    if isinstance(obj, dict):
        return obj

    return {
        "schema": 1,
        "type": "led_snapshot",
        "ts_ms": int(time.time() * 1000),
        "leds": {},
        "show_push": None,
    }


def publish_intent(r: redis.Redis, payload: dict[str, Any]) -> None:
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        raise ValueError("missing intent")

    params = payload.get("params")
    if not isinstance(params, dict):
        params = {}

    message = {
        "intent": intent,
        "params": params,
        "source": {
            "type": "virtual_panel",
            "node": RT_NODE_ID,
        },
        "timestamp": int(time.time() * 1000),
    }

    r.publish(RT_UI_INTENTS_CHANNEL, json.dumps(message, separators=(",", ":"), ensure_ascii=False))


def guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "application/octet-stream"


PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>RollingThunder Virtual Panel</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: sans-serif; margin: 2rem; background: #111; color: #eee; }
    .card { max-width: 720px; padding: 1rem 1.25rem; border: 1px solid #444; border-radius: 12px; background: #1b1b1b; }
    code { background: #222; padding: 0.1rem 0.3rem; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>RollingThunder Virtual Panel</h1>
    <p>The server is running.</p>
    <p>LED snapshot API: <code>/api/leds</code></p>
    <p>Intent API: <code>POST /api/intent</code></p>
    <p>The full virtual panel UI can be added later under <code>/opt/rollingthunder/ui/virtual-panel/</code>.</p>
  </div>
</body>
</html>
"""


class VirtualPanelHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class, config: ServerConfig, redis_mgr: RedisManager):
        super().__init__(server_address, handler_class)
        self.config = config
        self.redis_mgr = redis_mgr


class Handler(BaseHTTPRequestHandler):
    server: VirtualPanelHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"http {self.address_string()} - {fmt % args}")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_json_body(self) -> dict[str, Any]:
        length = as_int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid json body")
        if not isinstance(obj, dict):
            raise ValueError("json body must be an object")
        return obj

    def do_OPTIONS(self) -> None:
        self._send_bytes(HTTPStatus.NO_CONTENT, b"", "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "service": "virtual_panel_server",
                "enabled": self.server.config.enabled,
                "bind": self.server.config.bind,
                "port": self.server.config.port,
                "ui_dir": str(self.server.config.ui_dir),
            })
            return

        if path == "/api/config":
            self._send_json(HTTPStatus.OK, {
                "enabled": self.server.config.enabled,
                "bind": self.server.config.bind,
                "port": self.server.config.port,
                "pollMs": self.server.config.poll_ms,
            })
            return

        if path == "/api/leds":
            try:
                r = self.server.redis_mgr.get()
                snapshot = read_led_snapshot(r)
                self._send_json(HTTPStatus.OK, snapshot)
            except Exception as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            return

        if path == "/" or path == "/index.html":
            index_path = self.server.config.ui_dir / "index.html"
            if index_path.exists() and index_path.is_file():
                try:
                    body = index_path.read_bytes()
                    self._send_bytes(HTTPStatus.OK, body, guess_content_type(index_path))
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "ok": False,
                        "error": f"failed to read index.html: {type(exc).__name__}: {exc}",
                    })
                return

            self._send_bytes(HTTPStatus.OK, PLACEHOLDER_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        ui_root = self.server.config.ui_dir.resolve()
        requested = (ui_root / path.lstrip("/")).resolve()

        try:
            requested.relative_to(ui_root)
        except Exception:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "forbidden"})
            return

        if requested.exists() and requested.is_file():
            try:
                self._send_bytes(HTTPStatus.OK, requested.read_bytes(), guess_content_type(requested))
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "ok": False,
                    "error": f"failed to read file: {type(exc).__name__}: {exc}",
                })
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path != "/api/intent":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return

        try:
            body = self._read_json_body()
            r = self.server.redis_mgr.get()
            publish_intent(r, body)
            self._send_json(HTTPStatus.OK, {"ok": True})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_server_config()

    if not cfg.enabled:
        log("virtual panel disabled in app.json; exiting")
        return 0

    redis_mgr = RedisManager()

    try:
        redis_mgr.get()
        log("redis connected")
    except Exception as exc:
        log_err(f"initial redis connect failed: {type(exc).__name__}: {exc}")

    httpd = VirtualPanelHTTPServer((cfg.bind, cfg.port), Handler, cfg, redis_mgr)

    log(
        f"virtual panel server listening on http://{cfg.bind}:{cfg.port} "
        f"ui_dir={cfg.ui_dir}"
    )

    try:
        while _running:
            httpd.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass

    log("virtual panel server stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())