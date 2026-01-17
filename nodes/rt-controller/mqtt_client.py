# nodes/rt-controller/mqtt_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import socket

import paho.mqtt.client as mqtt


class MqttConnectError(RuntimeError):
    pass


@dataclass(frozen=True)
class MqttConnInfo:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    client_id: str
    keepalive_sec: int
    connect_timeout_sec: float


def _int_or(default: int, value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float_or(default: float, value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return default


def resolve_mqtt_conn_info(cfg: Dict[str, Any], *, node_id: str = "rt-controller") -> MqttConnInfo:
    """
    Config-first resolution.

    Reads from:
      globals.bus.mqtt.host
      globals.bus.mqtt.port
      globals.bus.mqtt.username
      globals.bus.mqtt.password
      globals.bus.mqtt.keepaliveSec
      globals.bus.mqtt.connectTimeoutSec
      globals.bus.mqtt.clientId (optional)

    Defaults: 127.0.0.1:1883, client_id derived from node_id.
    """
    gb = (cfg.get("globals") or {}).get("bus") or {}
    m = (gb.get("mqtt") or {}) if isinstance(gb, dict) else {}

    host = m.get("host") if isinstance(m, dict) else None
    port = m.get("port") if isinstance(m, dict) else None
    username = m.get("username") if isinstance(m, dict) else None
    password = m.get("password") if isinstance(m, dict) else None
    keepalive = m.get("keepaliveSec") if isinstance(m, dict) else None
    timeout = m.get("connectTimeoutSec") if isinstance(m, dict) else None
    client_id = m.get("clientId") if isinstance(m, dict) else None

    cid = str(client_id).strip() if client_id else f"{node_id}-{socket.gethostname()}"

    return MqttConnInfo(
        host=str(host) if host else "127.0.0.1",
        port=_int_or(1883, port),
        username=str(username) if username else None,
        password=str(password) if password else None,
        client_id=cid,
        keepalive_sec=_int_or(30, keepalive),
        connect_timeout_sec=_float_or(2.0, timeout),
    )


def connect_and_probe(info: MqttConnInfo) -> mqtt.Client:
    """
    Minimal proof of life:
    - create client
    - connect
    - run a short network loop to complete handshake
    - disconnect

    Raises MqttConnectError on failure.
    """
    rc_holder = {"rc": None}

    def on_connect(client, userdata, flags, rc, properties=None):
        rc_holder["rc"] = rc

    try:
        client = mqtt.Client(client_id=info.client_id, protocol=mqtt.MQTTv311)
        client.on_connect = on_connect
        if info.username:
            client.username_pw_set(info.username, info.password)

        # Initiate connection
        client.connect(info.host, info.port, keepalive=info.keepalive_sec)

        # Process network events briefly to receive CONNACK
        client.loop(timeout=info.connect_timeout_sec)

        rc = rc_holder["rc"]
        if rc is None:
            raise MqttConnectError(
                f"No CONNACK received from MQTT broker at {info.host}:{info.port}"
            )
        if rc != 0:
            raise MqttConnectError(
                f"MQTT broker rejected connection (rc={rc}) at {info.host}:{info.port}"
            )

        # Clean disconnect (still minimal)
        client.disconnect()
        return client

    except Exception as e:
        raise MqttConnectError(
            f"Unable to connect to MQTT broker at {info.host}:{info.port}: {e}"
        )

import json
from typing import Any, Dict

def publish_json_event(
    info: MqttConnInfo,
    topic: str,
    payload: Dict[str, Any],
    *,
    retain: bool = True,
    qos: int = 1,
) -> None:
    """
    Minimal one-shot publish: connect -> publish -> disconnect.
    No subscriptions, no loops.
    """
    client = mqtt.Client(client_id=info.client_id, protocol=mqtt.MQTTv311)
    if info.username:
        client.username_pw_set(info.username, info.password)

    client.connect(info.host, info.port, keepalive=info.keepalive_sec)

    body = json.dumps(payload, separators=(",", ":"), sort_keys=False)
    result = client.publish(topic, payload=body, qos=qos, retain=retain)
    result.wait_for_publish(timeout=info.connect_timeout_sec)

    client.disconnect()
