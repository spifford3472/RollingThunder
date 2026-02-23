#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone

try:
    import paho.mqtt.client as mqtt  # type: ignore
except Exception:
    mqtt = None  # type: ignore

NODE_ID = os.environ.get("RT_NODE_ID", socket.gethostname())
ROLE = os.environ.get("RT_NODE_ROLE", "unknown")

MQTT_HOST = os.environ.get("RT_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("RT_MQTT_PORT", "1883"))
MQTT_KEEPALIVE = int(os.environ.get("RT_MQTT_KEEPALIVE", "30"))

TOPIC_PREFIX = os.environ.get("RT_PRESENCE_TOPIC_PREFIX", "rt/presence")
INTERVAL_SEC = float(os.environ.get("RT_PRESENCE_INTERVAL_SEC", "2.5"))

PRESENCE_HTTP_PORT = os.environ.get("RT_PRESENCE_HTTP_PORT", "").strip()
UI_RENDER_OK = os.environ.get("RT_UI_RENDER_OK", "").strip().lower()
UI_CAP_JSON = os.environ.get("RT_UI_CAPABILITIES_JSON", "").strip()

def now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def get_ip() -> str:
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return ip

def parse_bool(s: str):
    if s == "true":
        return True
    if s == "false":
        return False
    return None

def parse_json_obj(s: str) -> dict:
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def main() -> int:
    if mqtt is None:
        print("[presence_publisher] ERROR: paho-mqtt not installed")
        return 2

    start = time.time()
    topic = f"{TOPIC_PREFIX}/{NODE_ID}"

    client = mqtt.Client(client_id=f"{NODE_ID}-presence", clean_session=True)

    def on_connect(_c, _u, _f, rc):
        if rc == 0:
            print(f"[presence_publisher] MQTT connected; publishing to {topic}")
        else:
            print(f"[presence_publisher] MQTT connect failed rc={rc}")

    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
    client.loop_start()

    ui_caps = parse_json_obj(UI_CAP_JSON)
    ui_render = parse_bool(UI_RENDER_OK)

    while True:
        uptime_sec = int(max(0.0, time.time() - start))
        payload = {
            "node_id": NODE_ID,
            "role": ROLE,
            "hostname": socket.gethostname(),
            "timestamp": now_iso_utc(),
            "uptime_sec": uptime_sec,
            "status": "alive",
            "net": {"ip": get_ip()},
            "mqtt": {"connected": True, "topic": topic},
        }

        if ui_caps:
            payload["ui_capabilities"] = ui_caps

        if ui_render is not None:
            payload["ui"] = {"render_ok": bool(ui_render)}

        if PRESENCE_HTTP_PORT:
            payload["presence_http_port"] = PRESENCE_HTTP_PORT

        try:
            client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=0, retain=False)
        except Exception as e:
            print(f"[presence_publisher] WARN: publish failed: {type(e).__name__}: {e}")

        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    raise SystemExit(main())
