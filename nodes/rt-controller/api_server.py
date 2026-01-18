# nodes/rt-controller/api_server.py
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import Flask, jsonify
import redis

from redis_client import resolve_redis_conn_info, connect_and_ping


def _ns_from_cfg(cfg: Dict[str, Any]) -> str:
    return str(((cfg.get("globals") or {}).get("state") or {}).get("namespace") or "rt").strip()


def _k(prefix: str, *parts: str) -> str:
    return prefix + ":" + ":".join(parts)


def create_app(cfg: Dict[str, Any]) -> Flask:
    app = Flask(__name__)
    prefix = _ns_from_cfg(cfg)

    # Redis client (connect once at startup)
    info = resolve_redis_conn_info(cfg)
    r = connect_and_ping(info)

    @app.get("/healthz")
    def healthz():
        # quick check: redis ping + last_seen_ms exists
        try:
            r.ping()
            last_seen = r.hget(_k(prefix, "system", "health"), "last_seen_ms")
            return jsonify(
                {
                    "ok": True,
                    "redis": "ok",
                    "last_seen_ms": int(last_seen) if last_seen else None,
                }
            ), 200
        except Exception as e:
            return jsonify({"ok": False, "redis": "error", "error": str(e)}), 503

    @app.get("/state/summary")
    def state_summary():
        # Return a compact snapshot from the authoritative Redis keys
        system_info = r.hgetall(_k(prefix, "system", "info"))
        system_health = r.hgetall(_k(prefix, "system", "health"))
        raw_nodes = r.smembers(_k(prefix, "system", "nodes"))
        nodes = sorted([(x.decode("utf-8") if isinstance(x, (bytes, bytearray)) else str(x)) for x in raw_nodes])


        def decode_hash(h) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            for k, v in (h or {}).items():
                ks = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
                vs = v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else str(v)

                if ks.endswith("_ms") or ks.endswith("_sec") or ks.endswith("_count") or ks in (
                    "pages_count", "panels_count", "services_count", "pid", "redis_connected", "mqtt_connected"
                ):
                    try:
                        out[ks] = int(vs)
                        continue
                    except Exception:
                        pass

                out[ks] = vs
            return out


        return jsonify(
            {
                "namespace": prefix,
                "system": {
                    "info": decode_hash(system_info),
                    "health": decode_hash(system_health),
                    "nodes": nodes,
                },
            }
        ), 200

    return app
