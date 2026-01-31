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
DEPLOY_PATHS = ("/api/v1/ui/deploy", "/api/v1/ui/deploy/")
STATE_BATCH_PATHS = ("/api/v1/ui/state/batch", "/api/v1/ui/state/batch/")


KEY_DEPLOY_PREFIX = os.environ.get("RT_KEY_DEPLOY_PREFIX", "rt:deploy:report:")
DEPLOY_MAX_AGE_SEC = int(os.environ.get("RT_DEPLOY_MAX_AGE_SEC", "30"))
DEPLOY_COMMIT_FILE = os.environ.get("RT_DEPLOYED_COMMIT_FILE", "/opt/rollingthunder/.deploy/DEPLOYED_COMMIT")

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

def _load_deploy_report(r: redis.Redis, node_id: str) -> Optional[Dict[str, Any]]:
    """
    Deploy reports are stored as JSON strings at:
      rt:deploy:report:<node_id>
    """
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
    

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            clen = int(self.headers.get("Content-Length", "0"))
        except Exception:
            clen = 0

        if clen <= 0:
            return {}
        if clen > MAX_BODY_BYTES:
            # bounded behavior
            return {"_error": "body_too_large"}

        raw = self.rfile.read(clen)
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else {"_error": "json_not_object"}
        except Exception:
            return {"_error": "json_parse_error"}

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

    def _key_allowed(self, k: Any) -> bool:
        if not isinstance(k, str):
            return False
        if len(k) == 0 or len(k) > MAX_KEY_LEN:
            return False
        return k.startswith("rt:")


    def _write_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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

            # expected commit (controller-local stamp)
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

                # Drift state
                if not reasons:
                    drift_state = "ok"
                elif "deployed_commit_mismatch" in reasons:
                    drift_state = "bad"
                else:
                    drift_state = "warn"

                nodes_out.append({
                    "id": node_id,
                    "role": h.get("role"),
                    "status": h.get("status"),
                    "age_sec": h.get("age_sec"),
                    "deploy": deploy_obj,
                    "drift": {"state": drift_state, "reasons": reasons},
                })

            nodes_out.sort(key=lambda x: str(x.get("id") or ""))
            payload["data"]["nodes"] = nodes_out
            payload["ok"] = True

        except Exception as e:
            payload["errors"].append(f"deploy_failed: {type(e).__name__}: {e}")

        self._write_json(200, payload)


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
            nodes.sort(key=lambda x: str(x.get("id") or ""))


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

    def do_POST(self) -> None:
        if self.path in STATE_BATCH_PATHS:
            return self._handle_state_batch()

        self.send_response(404)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not_found"}')

    def do_GET(self) -> None:
        if self.path in SNAPSHOT_PATHS:
            return self._handle_snapshot()

        if self.path in NODES_PATHS:
            return self._handle_nodes()

        if self.path in DEPLOY_PATHS:
            return self._handle_deploy()

        self.send_response(404)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not_found"}')


def main() -> None:
    httpd = HTTPServer((HOST, PORT), UiSnapshotHandler)
    print(f"ui_snapshot_api listening on {HOST}:{PORT} (redis {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
