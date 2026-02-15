#!/usr/bin/env python3
"""
RollingThunder - UI Snapshot API (rt-controller)

Phase 12c-A:
- Type coercion for Redis hash values (ints/bools/floats/null) when unambiguous
- Derived system health flag: data.system.health.ok
- Still read-only, bounded, CORS enabled

Add-on:
- Serve static UI assets from /opt/rollingthunder/ui under /ui/*
- Serve static config assets from /opt/rollingthunder/config under /config/*
  (same-origin UI + API + config for kiosk/browser simplicity)

Phase 15:
- SSE UI bus subscribe endpoint: /api/v1/ui/bus/subscribe (read-only)
- ThreadingHTTPServer to avoid blocking the entire server on SSE connections
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

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
DEPLOY_PATHS = ("/api/v1/ui/deploy", "/api/v1/ui/deploy/")
STATE_BATCH_PATHS = ("/api/v1/ui/state/batch", "/api/v1/ui/state/batch/")
STATE_SCAN_PATHS = ("/api/v1/ui/state/scan", "/api/v1/ui/state/scan/")


# NEW: SSE bus subscribe endpoint (read-only)
BUS_SUBSCRIBE_PATHS = ("/api/v1/ui/bus/subscribe", "/api/v1/ui/bus/subscribe/")

HEALTHZ_PATHS = ("/healthz", "/healthz/")

KEY_DEPLOY_PREFIX = os.environ.get("RT_KEY_DEPLOY_PREFIX", "rt:deploy:report:")
DEPLOY_MAX_AGE_SEC = int(os.environ.get("RT_DEPLOY_MAX_AGE_SEC", "30"))
DEPLOY_COMMIT_FILE = os.environ.get(
    "RT_DEPLOYED_COMMIT_FILE", "/opt/rollingthunder/.deploy/DEPLOYED_COMMIT"
)

MAX_SCAN_LIMIT = int(os.environ.get("RT_MAX_SCAN_LIMIT", "50"))
MAX_SCAN_PREVIEW_BYTES = int(os.environ.get("RT_MAX_SCAN_PREVIEW_BYTES", "2048"))

# Conservative: only allow scanning inside these prefixes.
SCAN_MATCH_ALLOWLIST = (
    "rt:nodes:",
    "rt:services:",
    "rt:deploy:report:",
    "rt:system:",
)

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

MAX_STATE_KEYS = int(os.environ.get("RT_MAX_STATE_KEYS", "200"))
MAX_KEY_LEN = int(os.environ.get("RT_MAX_STATE_KEY_LEN", "256"))
MAX_BODY_BYTES = int(os.environ.get("RT_MAX_UI_BODY_BYTES", "65536"))  # 64KB

# NEW: SSE safety bounds
SSE_MAX_STREAM_SEC = float(os.environ.get("RT_SSE_MAX_STREAM_SEC", "55"))  # hard cap per connection
SSE_MAX_EVENTS = int(os.environ.get("RT_SSE_MAX_EVENTS", "250"))          # hard cap per connection
SSE_POLL_SEC = float(os.environ.get("RT_SSE_POLL_SEC", "0.5"))            # pubsub poll interval
SSE_HEARTBEAT_SEC = float(os.environ.get("RT_SSE_HEARTBEAT_SEC", "15"))   # keepalive comment
SSE_MAX_DATA_BYTES = int(os.environ.get("RT_SSE_MAX_DATA_BYTES", "16384"))  # per event data cap

# NEW: PubSub channel defaults (read-only)
# Keep it conservative: one channel by default, optional override via query if allowed.
UI_BUS_DEFAULT_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")
UI_BUS_ALLOW_QUERY_CHANNEL = (os.environ.get("RT_UI_BUS_ALLOW_QUERY_CHANNEL", "0").strip() == "1")
UI_BUS_CHANNEL_PREFIX = os.environ.get("RT_UI_BUS_CHANNEL_PREFIX", "rt:")  # must start with this

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")

UI_ROOT = Path(os.environ.get("RT_UI_ROOT", "/opt/rollingthunder/ui")).resolve()
UI_PREFIX = "/ui"

CFG_ROOT = Path(os.environ.get("RT_CFG_ROOT", "/opt/rollingthunder/config")).resolve()
CFG_PREFIX = "/config"


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
    """Convert common Redis-string scalars to real JSON types (only plain strings)."""
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
    """Read Redis hash and parse/coerce values."""
    raw = r.hgetall(key)  # Dict[str,str] with decode_responses=True
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        parsed = _try_parse_json(v)
        if isinstance(parsed, str):
            parsed = _coerce_scalar(parsed)
        out[k] = _truncate(parsed)
    return out


def _load_deploy_report(r: redis.Redis, node_id: str) -> Optional[Dict[str, Any]]:
    """Deploy reports stored as JSON strings at rt:deploy:report:<node_id>."""
    key = f"{KEY_DEPLOY_PREFIX}{node_id}"
    raw = r.get(key)
    if not raw:
        return None
    parsed = _try_parse_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return None


def _service_summary_fields(h: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "scope", "ownerNode", "startPolicy", "stopPolicy", "state", "last_update_ms")
    return {k: h.get(k) for k in keep if k in h}


def _derive_system_ok(system_health: Dict[str, Any]) -> Dict[str, Any]:
    """Add derived OK/stale/age_sec to system_health."""
    redis_ok = bool(system_health.get("redis_ok")) if system_health.get("redis_ok") is not None else False
    mqtt_ok = bool(system_health.get("mqtt_ok")) if system_health.get("mqtt_ok") is not None else False

    last_seen_ms = system_health.get("last_seen_ms")
    stale = True
    age_sec = None
    if isinstance(last_seen_ms, int):
        age_sec = max(0, int((now_ms() - last_seen_ms) / 1000))
        stale = age_sec > HEALTH_STALE_SEC

    ok = bool(redis_ok and mqtt_ok and (stale is False))

    system_health["ok"] = ok
    system_health["stale"] = stale
    if age_sec is not None:
        system_health["age_sec"] = age_sec

    return system_health


class UiSnapshotHandler(BaseHTTPRequestHandler):
    # Keep logs quiet by default; systemd/journal can still show tracebacks if we print
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _parse_int_qs(self, qs: Dict[str, list[str]], name: str, default: int, lo: int, hi: int) -> int:
        try:
            raw = (qs.get(name) or [str(default)])[0]
            v = int(str(raw).strip())
            if v < lo:
                return lo
            if v > hi:
                return hi
            return v
        except Exception:
            return default

    def _scan_match_allowed(self, match: str) -> bool:
        # must be simple and bounded
        if not isinstance(match, str):
            return False
        m = match.strip()
        if not m or len(m) > MAX_KEY_LEN:
            return False
        if "\n" in m or "\r" in m:
            return False

        # Only allow allowlisted prefixes, and only "*" at the end.
        # Example: "rt:services:*" or "rt:services:mqtt_bus" (no wildcard)
        if "*" in m and not m.endswith("*"):
            return False

        base = m[:-1] if m.endswith("*") else m
        return any(base.startswith(p) for p in SCAN_MATCH_ALLOWLIST)

    def _handle_state_scan(self) -> None:
        """
        GET /api/v1/ui/state/scan?match=rt:services:*&limit=50&cursor=0

        Returns:
          {
            ok, ts, server_time_ms,
            data: { cursor, next_cursor, keys: [{key,type,preview}] }
          }

        Bounded:
        - allowlisted match prefixes only
        - cursor-based SCAN
        - limit clamped to MAX_SCAN_LIMIT
        - preview is small (hash subset / string prefix)
        """
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query or "")

        # Accept either `match` or `prefix` for convenience.
        match = ""
        if "match" in qs and qs["match"]:
            match = str(qs["match"][0])
        elif "prefix" in qs and qs["prefix"]:
            pref = str(qs["prefix"][0]).strip()
            match = pref if pref.endswith("*") else (pref + "*")

        if not match:
            match = "rt:services:*"

        if not self._scan_match_allowed(match):
            return self._write_json(
                400,
                {
                    "ok": False,
                    "ts": now_iso_utc(),
                    "server_time_ms": now_ms(),
                    "errors": ["bad_request: match_not_allowed"],
                    "data": {"cursor": 0, "next_cursor": 0, "keys": []},
                },
            )

        limit = self._parse_int_qs(qs, "limit", default=25, lo=1, hi=MAX_SCAN_LIMIT)
        cursor = self._parse_int_qs(qs, "cursor", default=0, lo=0, hi=10_000_000)

        payload: Dict[str, Any] = {
            "ok": False,
            "ts": now_iso_utc(),
            "server_time_ms": now_ms(),
            "errors": [],
            "data": {"cursor": cursor, "next_cursor": cursor, "keys": []},
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/state/scan",
            "schema_version": "ui.state.scan.v1",
        }

        try:
            r = self._redis()

            # redis-py scan: cursor + match + count hint
            next_cursor, keys = r.scan(cursor=cursor, match=match, count=limit)

            out = []
            for k in keys[:limit]:
                ks = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
                if not self._key_allowed(ks):
                    continue

                t = str(r.type(ks))
                preview: Any = None

                try:
                    if t == "hash":
                        h = r.hgetall(ks)
                        # bounded preview: pick a few stable fields
                        keep = {}
                        for fld in ("id", "role", "status", "state", "age_sec", "last_seen_ms", "last_update_ms", "ownerNode", "publisher_error"):
                            if fld in h:
                                keep[fld] = _truncate(_coerce_scalar(h.get(fld)))
                        preview = keep
                    elif t == "string":
                        s = r.get(ks) or ""
                        # small preview only
                        b = s.encode("utf-8", errors="replace")
                        if len(b) > MAX_SCAN_PREVIEW_BYTES:
                            s = b[:MAX_SCAN_PREVIEW_BYTES].decode("utf-8", errors="replace") + "…"
                        parsed_s = _try_parse_json(s)
                        preview = _truncate(parsed_s)
                    else:
                        preview = None
                except Exception:
                    preview = {"_error": "preview_failed"}

                out.append({"key": ks, "type": t, "preview": preview})

            payload["data"] = {
                "cursor": cursor,
                "next_cursor": int(next_cursor),
                "keys": out,
                "match": match,
                "limit": limit,
            }
            payload["ok"] = True
            return self._write_json(200, payload)

        except Exception as e:
            payload["errors"].append(f"scan_failed: {type(e).__name__}: {e}")
            return self._write_json(503, payload)

    # ---------- HTTP helpers ----------
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_HEAD(self) -> None:
        # Mirror GET routing but suppress body
        self._head_only = True  # type: ignore[attr-defined]
        try:
            self.do_GET()
        finally:
            self._head_only = False  # type: ignore[attr-defined]

    def _is_head(self) -> bool:
        return bool(getattr(self, "_head_only", False))

    def _safe_write(self, body: bytes) -> None:
        if self._is_head():
            return
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            return
        except ConnectionResetError:
            return

    def _write_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._safe_write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            clen = int(self.headers.get("Content-Length", "0"))
        except Exception:
            clen = 0

        if clen <= 0:
            return {}
        if clen > MAX_BODY_BYTES:
            return {"_error": "body_too_large"}

        raw = self.rfile.read(clen)
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else {"_error": "json_not_object"}
        except Exception:
            return {"_error": "json_parse_error"}

    # ---------- Redis ----------
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

    def _redis_pubsub_client(self) -> redis.Redis:
        """
        Separate client for PubSub. Use a larger socket timeout so the thread can
        wait/poll safely without hammering Redis.
        """
        t = max(1.0, float(os.environ.get("RT_SSE_REDIS_TIMEOUT_SEC", "2.0")))
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=t,
            socket_connect_timeout=max(t, 1.0),
        )
        r.ping()
        return r

    def _key_allowed(self, k: Any) -> bool:
        if not isinstance(k, str):
            return False
        if len(k) == 0 or len(k) > MAX_KEY_LEN:
            return False
        return k.startswith("rt:")

    # ---------- Static serving (/ui/* and /config/*) ----------
    def _serve_static(self, url_prefix: str, fs_root: Path, default_doc: str) -> bool:
        parsed = urlparse(self.path)
        path = parsed.path

        # Normalize /prefix -> /prefix/
        if path == url_prefix:
            path = url_prefix + "/"

        if not path.startswith(url_prefix + "/"):
            return False

        rel = path[len(url_prefix) + 1 :]  # after "/prefix/"
        if rel == "" or rel.endswith("/"):
            rel = rel + default_doc

        rel = unquote(rel)

        # Block traversal early
        if ".." in rel or rel.startswith("/") or rel.startswith("\\"):
            self.send_error(404, "not_found")
            return True

        candidate = (fs_root / rel).resolve()

        # Ensure candidate stays under root
        try:
            candidate.relative_to(fs_root)
        except Exception:
            self.send_error(404, "not_found")
            return True

        if not candidate.exists() or not candidate.is_file():
            self.send_error(404, "not_found")
            return True

        ctype, _ = mimetypes.guess_type(str(candidate))
        ctype = ctype or "application/octet-stream"

        try:
            body = candidate.read_bytes()
        except Exception:
            self.send_error(404, "not_found")
            return True

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))

        # Dev-friendly caching defaults: UI can be no-cache; config should be no-store.
        if url_prefix == CFG_PREFIX:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-cache")

        self.end_headers()
        self._safe_write(body)
        return True

    def _try_serve_static_ui_or_config(self) -> bool:
        # /ui/* -> index.html
        if self._serve_static(UI_PREFIX, UI_ROOT, default_doc="index.html"):
            return True
        # /config/* -> app.json (more useful than index.html)
        if self._serve_static(CFG_PREFIX, CFG_ROOT, default_doc="app.json"):
            return True
        return False

    # ---------- SSE helpers ----------
    def _sse_write(self, chunk: str) -> bool:
        """
        Write an SSE chunk and flush.
        Returns False if client is gone.
        """
        if self._is_head():
            return False
        try:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False
        except Exception:
            return False

    def _sse_event(self, event: str, data_obj: Any) -> bool:
        """
        Serialize data as JSON (bounded), send as SSE event.
        """
        try:
            data_json = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            data_json = json.dumps({"_error": "event_json_serialize_failed"}, separators=(",", ":"))

        # Hard cap payload size (avoid memory bloat / huge frames)
        if len(data_json.encode("utf-8")) > SSE_MAX_DATA_BYTES:
            data_json = json.dumps(
                {"_truncated": True, "_preview": data_json[: min(1024, len(data_json))] + "…"},
                ensure_ascii=False,
                separators=(",", ":"),
            )

        # SSE framing: one "data:" line is okay for JSON
        chunk = f"event: {event}\ndata: {data_json}\n\n"
        return self._sse_write(chunk)

    def _sse_comment(self, comment: str) -> bool:
        # comment lines start with ":"
        return self._sse_write(f": {comment}\n\n")

    def _select_bus_channel(self) -> str:
        """
        Conservative channel selection:
        - default: UI_BUS_DEFAULT_CHANNEL
        - optionally allow ?channel=... if RT_UI_BUS_ALLOW_QUERY_CHANNEL=1
        - enforce prefix and length bounds
        """
        chan = UI_BUS_DEFAULT_CHANNEL

        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query or "")
        if UI_BUS_ALLOW_QUERY_CHANNEL and "channel" in qs and qs["channel"]:
            candidate = str(qs["channel"][0]).strip()
            if (
                candidate
                and len(candidate) <= MAX_KEY_LEN
                and candidate.startswith(UI_BUS_CHANNEL_PREFIX)
                and "\n" not in candidate
                and "\r" not in candidate
            ):
                chan = candidate

        return chan

    # ---------- API handlers ----------
    def _handle_healthz(self) -> None:
        payload = {
            "ok": True,
            "ts": now_iso_utc(),
            "server_time_ms": now_ms(),
            "redis": {"host": REDIS_HOST, "port": REDIS_PORT, "db": REDIS_DB},
        }
        return self._write_json(200, payload)

    def _handle_bus_subscribe(self) -> None:
        """
        Read-only SSE stream backed by Redis PubSub.

        Safety / boundedness:
        - hard cap by time (SSE_MAX_STREAM_SEC)
        - hard cap by events (SSE_MAX_EVENTS)
        - polling-based loop with small sleeps (SSE_POLL_SEC)
        - heartbeat comments (SSE_HEARTBEAT_SEC)
        - uses ThreadingHTTPServer so each SSE connection consumes one thread, not the whole server
        """
        if self._is_head():
            # SSE doesn't make sense for HEAD; keep it simple.
            self.send_response(405)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(b'{"error":"method_not_allowed"}')
            return

        # Prepare SSE response headers
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        # Helpful when running behind proxies (harmless otherwise)
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        channel = self._select_bus_channel()

        started = time.time()
        last_heartbeat = started
        sent = 0

        # One initial hello event (bounded, useful to the UI)
        if not self._sse_event(
            "hello",
            {
                "source": "rt-controller",
                "endpoint": "/api/v1/ui/bus/subscribe",
                "ts": now_iso_utc(),
                "server_time_ms": now_ms(),
                "channel": channel,
                "bounds": {"max_sec": SSE_MAX_STREAM_SEC, "max_events": SSE_MAX_EVENTS},
                "schema_version": "ui.bus.sse.v1",
            },
        ):
            return

        try:
            r = self._redis_pubsub_client()
            pubsub = r.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(channel)

            while True:
                # time bound
                if (time.time() - started) >= SSE_MAX_STREAM_SEC:
                    self._sse_event("eos", {"reason": "max_stream_sec", "sent": sent, "ts": now_iso_utc()})
                    break

                # event bound
                if sent >= SSE_MAX_EVENTS:
                    self._sse_event("eos", {"reason": "max_events", "sent": sent, "ts": now_iso_utc()})
                    break

                # heartbeat
                now_t = time.time()
                if (now_t - last_heartbeat) >= SSE_HEARTBEAT_SEC:
                    if not self._sse_comment(f"heartbeat {now_iso_utc()}"):
                        break
                    last_heartbeat = now_t

                # poll pubsub (non-blocking-ish)
                msg = None
                try:
                    msg = pubsub.get_message(timeout=SSE_POLL_SEC)
                except Exception:
                    msg = None

                if msg and isinstance(msg, dict) and msg.get("type") == "message":
                    ch = msg.get("channel")
                    data = msg.get("data")

                    parsed = _try_parse_json(data if isinstance(data, str) else str(data))
                    if isinstance(parsed, str):
                        parsed = _coerce_scalar(parsed)

                    evt = {
                        "channel": ch,
                        "ts": now_iso_utc(),
                        "server_time_ms": now_ms(),
                        "data": _truncate(parsed),
                    }

                    if not self._sse_event("message", evt):
                        break
                    sent += 1

                # light yield; avoids tight looping even if timeout=0 on some redis versions
                time.sleep(max(0.0, min(SSE_POLL_SEC, 0.05)))

        except Exception as e:
            # Best-effort error event; don't assume the client is still there.
            try:
                self._sse_event("error", {"error": f"{type(e).__name__}: {e}", "ts": now_iso_utc()})
            except Exception:
                pass
        finally:
            try:
                # Close pubsub cleanly if it exists in locals
                if "pubsub" in locals():
                    pubsub.close()  # type: ignore[name-defined]
            except Exception:
                pass

    def _handle_state_batch(self) -> None:
        payload: Dict[str, Any] = {
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/state/batch",
            "ts": now_iso_utc(),
            "ok": False,
            "data": {"values": {}},
            "errors": [],
            "schema_version": "ui.state.batch.v1",
            "server_time_ms": now_ms(),
        }

        body = self._read_json_body()
        if body.get("_error"):
            payload["errors"].append(f"bad_request: {body['_error']}")
            return self._write_json(400, payload)

        keys = body.get("keys", [])
        if not isinstance(keys, list):
            payload["errors"].append("bad_request: keys_must_be_list")
            return self._write_json(400, payload)

        keys = keys[:MAX_STATE_KEYS]

        try:
            r = self._redis()
            out: Dict[str, Any] = {}

            for k in keys:
                ks = str(k)

                if not self._key_allowed(k):
                    out[ks] = {"ok": False, "encoding": "none", "value": None}
                    continue

                try:
                    t = str(r.type(k))

                    if t == "none":
                        out[ks] = {"ok": False, "encoding": "none", "value": None}

                    elif t == "string":
                        s = r.get(k)
                        if s is None:
                            out[ks] = {"ok": False, "encoding": "none", "value": None}
                        else:
                            parsed = _try_parse_json(s)
                            if isinstance(parsed, str):
                                parsed = _coerce_scalar(parsed)
                                out[ks] = {"ok": True, "encoding": "text", "value": _truncate(parsed)}
                            else:
                                out[ks] = {"ok": True, "encoding": "json", "value": _truncate(parsed)}

                    elif t == "hash":
                        out[ks] = {"ok": True, "encoding": "hash", "value": _hgetall_parsed(r, k)}

                    else:
                        out[ks] = {"ok": False, "encoding": t, "value": None}

                except Exception:
                    out[ks] = {"ok": False, "encoding": "error", "value": None}

            payload["data"]["values"] = out
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"state_batch_failed: {type(e).__name__}: {e}")

        return self._write_json(200, payload)

    def _handle_deploy(self) -> None:
        payload: Dict[str, Any] = {
            "source": "rt-controller",
            "endpoint": "/api/v1/ui/deploy",
            "ts": now_iso_utc(),
            "ok": False,
            "data": {"expected": {}, "nodes": []},
            "errors": [],
        }

        try:
            r = self._redis()

            expected_commit = "unknown"
            try:
                if os.path.exists(DEPLOY_COMMIT_FILE):
                    with open(DEPLOY_COMMIT_FILE, "r", encoding="utf-8") as f:
                        expected_commit = f.read().strip()
            except Exception:
                pass

            payload["data"]["expected"] = {"deployed_commit": expected_commit}

            nodes_out = []
            for key in r.scan_iter(match=f"{KEY_NODE_PREFIX}*"):
                if r.type(key) != "hash":
                    continue
                h = _hgetall_parsed(r, key)
                node_id = str(h.get("id") or str(key).split(":")[-1])

                report = _load_deploy_report(r, node_id)

                deploy_obj: Dict[str, Any] = {
                    "deployed_commit": None,
                    "git_head": None,
                    "dirty": None,
                    "report_age_sec": None,
                    "units": {},
                }

                reasons = []
                if report is None:
                    reasons.append("missing_deploy_report")
                else:
                    deploy_obj["deployed_commit"] = report.get("deployed_commit")
                    deploy_obj["git_head"] = report.get("git_head")
                    deploy_obj["dirty"] = report.get("dirty")
                    deploy_obj["units"] = report.get("units") or {}

                    ts_ms = report.get("ts_ms")
                    if isinstance(ts_ms, int):
                        age = max(0, int((now_ms() - ts_ms) / 1000))
                        deploy_obj["report_age_sec"] = age
                        if age > DEPLOY_MAX_AGE_SEC:
                            reasons.append("deploy_report_stale")

                    rep_commit = report.get("deployed_commit")
                    if (
                        expected_commit != "unknown"
                        and isinstance(rep_commit, str)
                        and rep_commit
                        and rep_commit != expected_commit
                    ):
                        reasons.append("deployed_commit_mismatch")

                if not reasons:
                    drift_state = "ok"
                elif "deployed_commit_mismatch" in reasons:
                    drift_state = "bad"
                else:
                    drift_state = "warn"

                nodes_out.append(
                    {
                        "id": node_id,
                        "role": h.get("role"),
                        "status": h.get("status"),
                        "age_sec": h.get("age_sec"),
                        "deploy": deploy_obj,
                        "drift": {"state": drift_state, "reasons": reasons},
                    }
                )

            nodes_out.sort(key=lambda x: str(x.get("id") or ""))
            payload["data"]["nodes"] = nodes_out
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"deploy_failed: {type(e).__name__}: {e}")

        return self._write_json(200, payload)

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

            nodes.sort(key=lambda x: str(x.get("id") or ""))
            payload["data"]["nodes"] = nodes
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"nodes_failed: {type(e).__name__}: {e}")

        return self._write_json(200, payload)

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

            system_health = _hgetall_parsed(r, KEY_SYSTEM_HEALTH)
            system_health = _derive_system_ok(system_health)

            nodes: Dict[str, Any] = {}
            try:
                node_ids = sorted(list(r.smembers(KEY_SYSTEM_NODES)))
            except Exception:
                node_ids = []

            for nid in node_ids:
                nk = f"{KEY_NODE_PREFIX}{nid}"
                if r.exists(nk):
                    nodes[nid] = _hgetall_parsed(r, nk)

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

        return self._write_json(200, payload)

    # ---------- routing ----------
    def do_POST(self) -> None:
        if self.path in STATE_BATCH_PATHS:
            return self._handle_state_batch()

        self.send_response(404)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self._safe_write(b'{"error":"not_found"}')

    def do_GET(self) -> None:
        # Parse once for path-only routing (ignore query for path matching)
        parsed = urlparse(self.path)
        path = parsed.path

        # 0) healthz
        if path in HEALTHZ_PATHS:
            return self._handle_healthz()

        # 1) Static UI/config first (same-origin UI + API + config)
        if self._try_serve_static_ui_or_config():
            return

        # 2) SSE bus subscribe (read-only)
        if path in BUS_SUBSCRIBE_PATHS:
            return self._handle_bus_subscribe()

        # 3) API routing
        if path in SNAPSHOT_PATHS:
            return self._handle_snapshot()

        if path in NODES_PATHS:
            return self._handle_nodes()

        if path in DEPLOY_PATHS:
            return self._handle_deploy()

        if path in STATE_SCAN_PATHS:
            return self._handle_state_scan()

        # 4) Fallback: not found
        self.send_response(404)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self._safe_write(b'{"error":"not_found"}')


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), UiSnapshotHandler)
    print(f"ui_snapshot_api listening on {HOST}:{PORT} (redis {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
