#!/usr/bin/env python3
"""
rt-radio presence publisher (Phase 14.2/14.3)

Tighten-ups included:
1) No MQTT publish spam when broker is unavailable (publish loop is gated).
2) Presence payload includes an explicit mqtt.topic field for debugging.

Responsibilities:
- Load node identity from /etc/rollingthunder/node.json (runtime)
  with a dev fallback to nodes/rt-radio/node.json
- Publish periodic presence heartbeats to MQTT topic rt/presence/<node_id>
- Serve local HTTP endpoints:
    GET /healthz
    GET /presence

Non-goals:
- No Redis writes
- No intents
- No control loops
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# -----------------------------
# Config (env-overridable)
# -----------------------------
HTTP_HOST = os.getenv("RT_PRESENCE_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("RT_PRESENCE_HTTP_PORT", "8787"))

MQTT_HOST = os.getenv("RT_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("RT_MQTT_PORT", "1883"))
MQTT_KEEPALIVE = int(os.getenv("RT_MQTT_KEEPALIVE", "30"))

PUBLISH_INTERVAL_SEC = float(os.getenv("RT_PRESENCE_INTERVAL_SEC", "2.5"))

# Runtime identity location (authoritative on the appliance)
NODE_JSON_RUNTIME = Path(os.getenv("RT_NODE_JSON", "/etc/rollingthunder/node.json"))

# Dev fallback: repo-relative (works when running from repo checkout)
NODE_JSON_DEV_FALLBACK = Path(__file__).resolve().parents[1] / "node.json"

# MQTT topic prefix
TOPIC_PREFIX = os.getenv("RT_PRESENCE_TOPIC_PREFIX", "rt/presence")


# -----------------------------
# Helpers
# -----------------------------
def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_node_identity() -> Tuple[Dict[str, Any], Path]:
    """
    Load node.json from runtime path, fallback to dev path.
    Returns (identity_dict, path_used)
    """
    if NODE_JSON_RUNTIME.exists():
        return read_json(NODE_JSON_RUNTIME), NODE_JSON_RUNTIME
    if NODE_JSON_DEV_FALLBACK.exists():
        return read_json(NODE_JSON_DEV_FALLBACK), NODE_JSON_DEV_FALLBACK
    return {}, NODE_JSON_RUNTIME


def get_host_ip_best_effort() -> Optional[str]:
    """
    Best-effort: determine primary IP without external calls.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't have to be reachable; no packets necessarily sent
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


@dataclass
class PresenceState:
    identity: Dict[str, Any]
    identity_path: Path
    start_monotonic: float

    last_publish_ok: bool = False
    last_publish_err: Optional[str] = None
    last_publish_ts: Optional[str] = None

    # recorded once at startup
    mqtt_connected: bool = False
    mqtt_connect_err: Optional[str] = None
    mqtt_topic: str = ""

    def uptime_sec(self) -> int:
        return int(time.monotonic() - self.start_monotonic)

    @property
    def node_id(self) -> str:
        return str(self.identity.get("node_id") or "rt-display")

    @property
    def role(self) -> str:
        return str(self.identity.get("node_role") or "display")


# -----------------------------
# MQTT publisher (optional)
# -----------------------------
class MqttPublisher:
    def __init__(self, host: str, port: int, keepalive: int) -> None:
        self.host = host
        self.port = port
        self.keepalive = keepalive
        self._client = None
        self._enabled = False
        self._lock = threading.Lock()

        try:
            import paho.mqtt.client as mqtt  # type: ignore
            self._mqtt = mqtt
            # We do not rely on callbacks; warning is harmless.
            self._client = mqtt.Client()
            self._enabled = True
        except Exception as e:
            self._enabled = False
            self._mqtt = None
            self._import_error = str(e)

    def connect(self) -> Tuple[bool, Optional[str]]:
        if not self._enabled or self._client is None:
            return False, getattr(self, "_import_error", "paho-mqtt not available")
        try:
            self._client.connect(self.host, self.port, self.keepalive)
            self._client.loop_start()  # Non-blocking network loop
            return True, None
        except Exception as e:
            return False, str(e)

    def publish_json(self, topic: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if not self._enabled or self._client is None:
            return False, getattr(self, "_import_error", "paho-mqtt not available")
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
            with self._lock:
                info = self._client.publish(topic, body, qos=0, retain=False)
            ok = getattr(info, "rc", 1) == 0
            return ok, None if ok else f"publish rc={getattr(info,'rc',None)}"
        except Exception as e:
            return False, str(e)


# -----------------------------
# HTTP server
# -----------------------------
class PresenceHandler(BaseHTTPRequestHandler):
    STATE: PresenceState = None  # type: ignore

    def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
        body = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        st = self.STATE

        if self.path == "/healthz":
            self._send_json(
                200,
                {
                    "ok": True,
                    "node_id": st.node_id,
                    "role": st.role,
                    "ts": utc_iso(),
                    "uptime_sec": st.uptime_sec(),
                    "identity_path": str(st.identity_path),
                    "mqtt_connected": st.mqtt_connected,
                    "mqtt_connect_err": st.mqtt_connect_err,
                    "mqtt_topic": st.mqtt_topic,
                    "mqtt_last_publish_ok": st.last_publish_ok,
                    "mqtt_last_publish_ts": st.last_publish_ts,
                    "mqtt_last_publish_err": st.last_publish_err,
                },
            )
            return

        if self.path == "/presence":
            self._send_json(200, build_presence_payload(st))
            return

        self._send_json(404, {"ok": False, "error": "not_found", "path": self.path})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep logs minimal (appliance mindset)
        return


# -----------------------------
# Presence payload
# -----------------------------
def build_presence_payload(state: PresenceState) -> Dict[str, Any]:
    ident = state.identity or {}
    hostname = str(ident.get("hostname") or socket.gethostname())
    ip = get_host_ip_best_effort()

    payload: Dict[str, Any] = {
        "node_id": state.node_id,
        "role": state.role,
        "hostname": hostname,
        "timestamp": utc_iso(),
        "uptime_sec": state.uptime_sec(),
        "status": "alive",
        "ui_capabilities": ident.get("ui_capabilities") or {},
        "net": {"ip": ip},
        "ui": {"render_ok": True},
        "system": {},
        # Tighten-up #2: explicit MQTT topic for debugging / observability
        "mqtt": {
            "connected": state.mqtt_connected,
            "topic": state.mqtt_topic,
        },
    }
    return payload


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    identity, path_used = load_node_identity()
    if not identity:
        print(
            f"[presence] ERROR: could not load node identity from {NODE_JSON_RUNTIME} "
            f"or {NODE_JSON_DEV_FALLBACK}"
        )
        # Continue anyway (still serves HTTP), but node_id defaults.
    else:
        print(f"[presence] Loaded identity from {path_used}")

    state = PresenceState(identity=identity, identity_path=path_used, start_monotonic=time.monotonic())
    state.mqtt_topic = f"{TOPIC_PREFIX}/{state.node_id}"

    # MQTT setup
    pub = MqttPublisher(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
    mqtt_ok, err = pub.connect()
    state.mqtt_connected = mqtt_ok
    state.mqtt_connect_err = err

    if mqtt_ok:
        print(f"[presence] MQTT connected to {MQTT_HOST}:{MQTT_PORT}")
    else:
        print(f"[presence] MQTT unavailable: {err}")

    # HTTP server
    PresenceHandler.STATE = state
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), PresenceHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, name="presence-http", daemon=True)
    http_thread.start()
    print(f"[presence] HTTP listening on http://{HTTP_HOST}:{HTTP_PORT} (healthz, presence)")

    # Heartbeat loop
    while True:
        payload = build_presence_payload(state)

        # Tighten-up #1: never attempt to publish if we didn't connect
        if state.mqtt_connected:
            ok, perr = pub.publish_json(state.mqtt_topic, payload)
            state.last_publish_ok = ok
            state.last_publish_err = perr
            state.last_publish_ts = utc_iso()

            # keep logs quiet; only log failures
            if not ok and perr:
                print(f"[presence] MQTT publish failed: {perr}")
        else:
            # Still provide HTTP presence; don't spam logs.
            state.last_publish_ok = False
            state.last_publish_err = "mqtt_unavailable"
            state.last_publish_ts = utc_iso()

        time.sleep(PUBLISH_INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
