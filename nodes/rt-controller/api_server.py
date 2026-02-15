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

    @app.get("/nodes")
    def nodes_list():
        """
        Read-only view of node presence derived by rt-node-presence-ingestor.
        Keys: <namespace>:nodes:<node_id> (hash)
        """
        try:
            pattern = _k(prefix, "nodes", "*")
            out_nodes = []

            # scan_iter returns str when decode_responses=True, else bytes
            for key in r.scan_iter(match=pattern):
                ks = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)

                # node_id is the last segment after the final ':'
                node_id = ks.split(":")[-1]

                h = r.hgetall(ks)  # allow key as str
                if not h:
                    continue

                def get_s(name: str) -> Optional[str]:
                    v = h.get(name)
                    if v is None:
                        return None
                    return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else str(v)

                def get_i(name: str) -> Optional[int]:
                    s = get_s(name)
                    if s is None or s == "":
                        return None
                    try:
                        return int(s)
                    except Exception:
                        return None

                def get_b(name: str) -> Optional[bool]:
                    s = get_s(name)
                    if s is None:
                        return None
                    s = s.strip().lower()
                    if s in ("true", "1", "yes", "y"):
                        return True
                    if s in ("false", "0", "no", "n"):
                        return False
                    return None

                node_obj: Dict[str, Any] = {
                    "id": get_s("id") or node_id,
                    "role": get_s("role") or "unknown",
                    "status": get_s("status") or "unknown",
                    "age_sec": get_i("age_sec"),
                    "ip": get_s("ip"),
                    "hostname": get_s("hostname"),
                    "ui_render_ok": get_b("ui_render_ok"),
                    "last_seen_ts": get_s("last_seen_ts"),
                    "last_seen_ms": get_i("last_seen_ms"),
                }

                out_nodes.append(node_obj)

            # Stable ordering for UI
            out_nodes.sort(key=lambda x: (x.get("role") or "", x.get("id") or ""))

            return jsonify({"ok": True, "namespace": prefix, "nodes": out_nodes}), 200

        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 503

    @app.get("/api/v1/ui/state/scan")
    def state_scan():
        """
        Read-only bounded SCAN for Redis hashes under a prefix.
        Example:
          /api/v1/ui/state/scan?prefix=rt:services:&limit=200
        """

        from flask import request

        req_prefix = request.args.get("prefix", "").strip()
        if not req_prefix:
            return jsonify({"ok": False, "error": "prefix query param required"}), 400

        try:
            limit = int(request.args.get("limit", "200"))
        except ValueError:
            return jsonify({"ok": False, "error": "limit must be integer"}), 400

        # Hard safety cap
        limit = max(1, min(limit, 500))

        results = []
        cursor = 0

        try:
            while True:
                cursor, keys = r.scan(cursor=cursor, match=f"{req_prefix}*", count=100)

                for key in keys:
                    if len(results) >= limit:
                        break

                    k = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)

                    if r.type(k) != b"hash" and r.type(k) != "hash":
                        continue

                    h = r.hgetall(k)
                    decoded = {}

                    for hk, hv in (h or {}).items():
                        ks = hk.decode("utf-8") if isinstance(hk, (bytes, bytearray)) else str(hk)
                        vs = hv.decode("utf-8") if isinstance(hv, (bytes, bytearray)) else str(hv)
                        decoded[ks] = vs

                    results.append({
                        "key": k,
                        "value": decoded
                    })

                if cursor == 0 or len(results) >= limit:
                    break

            return jsonify({
                "ok": True,
                "prefix": req_prefix,
                "count": len(results),
                "limit": limit,
                "items": results
            }), 200

        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 503



    return app
