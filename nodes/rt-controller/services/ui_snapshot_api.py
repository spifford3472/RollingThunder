#!/usr/bin/env python3
"""
RollingThunder - UI Snapshot API (rt-controller)

Phase 12c-A:
- Type coercion for Redis hash values (ints/bools/floats/null) when unambiguous
- Derived system health flag: data.system.health.ok
- Still read-only, bounded, CORS enabled
"""

from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

import redis


HOST = os.environ.get("RT_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("RT_UI_PORT", "8625"))

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

SNAPSHOT_PATHS = ("/api/v1/ui/snapshot", "/api/v1/ui/snapshot/")
NODES_PATHS = ("/api/v1/ui/nodes", "/api/v1/ui/nodes/")

# Observed base keys
KEY_SYSTEM_HEALTH = os.environ.get("RT_KEY_SYSTEM_HEALTH", "rt:system:health")
KEY_SYSTEM_NODES = os.environ.get("RT_KEY_SYSTEM_NODES", "rt:system:nodes")
KEY_NODE_PREFIX = os.environ.get("RT_KEY_NODE_PREFIX", "rt:nodes:")
KEY_SERVICE_PREFIX = os.environ.get("RT_KEY_SERVICE_PREFIX", "rt:services:")

# Output bounds / limits
MAX_STR_CHARS = int(os.environ.get("RT_MAX_STR_CHARS", "20000"))
MAX_SERVICES = int(os.environ.get("RT_MAX_SERVICES", "50"))

# Derived health freshness threshold (seconds)
HEALTH_STALE_SEC = int(os.environ.get("RT_HEALTH_STALE_SEC", "30"))

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> int:
    return int(time.time() * 1000)


def _try_parse_json(s: Optional[str]) -> Any:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[") or s.startswith('"')):
        return s
    try:
        return json.loads(s)
    except Exception:
        return s


def _coerce_scalar(s: Any) -> Any:
    """
    Convert common Redis-string scalars to real JSON types.
    Only acts on plain strings.
    """
    if s is None or not isinstance(s, str):
        return s

    v = s.strip()
    if v == "":
        return None

    low = v.lower()
    if low in ("null", "none", "(nil)"):
        return None
    if low in ("true", "false"):
        return low == "true"

    # common 0/1 flags
    if v == "0":
        return False
    if v == "1":
        return True

    # int/float
    if _INT_RE.match(v):
        try:
            return int(v)
        except Exception:
            return s
    if _FLOAT_RE.match(v):
        try:
            return float(v)
        except Exception:
            return s

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
    Read Redis hash and parse/coerce values:
    1) JSON-ish strings -> JSON
    2) otherwise scalar coercion (int/bool/float/null)
    """
    raw = r.hgetall(key)  # Dict[str,str] with decode_responses=True
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        parsed = _try_parse_json(v)
        if isinstance(parsed, str):
            parsed = _coerce_scalar(parsed)
        out[k] = _truncate(parsed)
    return out


def _service_summary_fields(h: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "scope", "ownerNode", "startPolicy", "stopPolicy", "state", "last_update_ms")
    return {k: h.get(k) for k in keep if k in h}


def _derive_system_ok(system_health: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add a derived OK flag and minimal diagnostics.
    Uses:
      - redis_ok (bool)
      - mqtt_ok (bool)
      - last_seen_ms freshness
    """
    redis_ok = bool(system_health.get("redis_ok")) if system_health.get("redis_ok") is not None else False
    mqtt_ok = bool(system_health.get("mqtt_ok")) if system_health.get("mqtt_ok") is not None else False

    last_seen_ms = system_health.get("last_seen_ms")
    stale = True
    age_sec = None
    if isinstance(last_seen_ms, int):
        age_sec = max(0, int((now_ms() - last_seen_ms) / 1000))
        stale = age_sec > HEALTH_STALE_SEC

    ok = bool(redis_ok and mqtt_ok and (stale is False))

    # Put derived fields under system_health
    system_health["ok"] = ok
    system_health["stale"] = stale
    if age_sec is not None:
        system_health["age_sec"] = age_sec

    return system_health


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

    def _redis(self) -> redis.Redis:
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
        return r

    def _write_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_nodes(self) -> None:
        payload: Dict[str, Any] = {
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/nodes",
            "ts": now_iso_utc(),
            "ok": False,
            "data": {"nodes": []},
            "errors": [],
        }

        try:
            r = self._redis()

            nodes = []
            for key in r.scan_iter(match=f"{KEY_NODE_PREFIX}*"):
                if r.type(key) != "hash":
                    continue

                h = _hgetall_parsed(r, key)

                # Bound the fields we expose (UI doesn't need everything)
                node_obj = {
                    "id": h.get("id") or str(key).split(":")[-1],
                    "role": h.get("role"),
                    "status": h.get("status"),
                    "age_sec": h.get("age_sec"),
                    "ip": h.get("ip"),
                    "hostname": h.get("hostname"),
                    "ui_render_ok": h.get("ui_render_ok"),
                    "last_seen_ts": h.get("last_seen_ts"),
                    "last_seen_ms": h.get("last_seen_ms"),
                    "last_update_ms": h.get("last_update_ms"),
                }
                nodes.append(node_obj)

            # stable order
            nodes.sort(key=lambda x: (str(x.get("role") or ""), str(x.get("id") or "")))

            payload["data"]["nodes"] = nodes
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"nodes_failed: {type(e).__name__}: {e}")

        self._write_json(200, payload)

    def _handle_snapshot(self) -> None:
        payload: Dict[str, Any] = {
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/snapshot",
            "ts": now_iso_utc(),
            "ok": False,
            "data": {},
            "errors": [],
        }

        try:
            r = self._redis()

            # System health + derived ok
            system_health = _hgetall_parsed(r, KEY_SYSTEM_HEALTH)
            system_health = _derive_system_ok(system_health)

            # Nodes (legacy set membership; keep for now)
            nodes: Dict[str, Any] = {}
            try:
                node_ids = sorted(list(r.smembers(KEY_SYSTEM_NODES)))
            except Exception:
                node_ids = []

            for nid in node_ids:
                nk = f"{KEY_NODE_PREFIX}{nid}"
                if r.exists(nk):
                    nodes[nid] = _hgetall_parsed(r, nk)

            # Services
            services: Dict[str, Any] = {}
            count = 0
            for key in r.scan_iter(match=f"{KEY_SERVICE_PREFIX}*"):
                if count >= MAX_SERVICES:
                    break
                if r.type(key) != "hash":
                    continue
                h = _hgetall_parsed(r, key)
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

        self._write_json(200, payload)

    def do_GET(self) -> None:
        if self.path in SNAPSHOT_PATHS:
            return self._handle_snapshot()

        if self.path in NODES_PATHS:
            return self._handle_nodes()

        self.send_response(404)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not_found"}')




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

            # System health + derived ok
            system_health = _hgetall_parsed(r, KEY_SYSTEM_HEALTH)
            system_health = _derive_system_ok(system_health)

            # Nodes
            nodes: Dict[str, Any] = {}
            try:
                node_ids = sorted(list(r.smembers(KEY_SYSTEM_NODES)))
            except Exception:
                node_ids = []

            for nid in node_ids:
                nk = f"{KEY_NODE_PREFIX}{nid}"
                if r.exists(nk):
                    nodes[nid] = _hgetall_parsed(r, nk)

            # Services
            services: Dict[str, Any] = {}
            count = 0
            for key in r.scan_iter(match=f"{KEY_SERVICE_PREFIX}*"):
                if count >= MAX_SERVICES:
                    break
                if r.type(key) != "hash":
                    continue
                h = _hgetall_parsed(r, key)
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
