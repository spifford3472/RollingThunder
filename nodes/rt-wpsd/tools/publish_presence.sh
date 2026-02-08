#!/usr/bin/env bash
set -euo pipefail

NODE_ID="${RT_NODE_ID:-$(hostname)}"
ROLE="${RT_NODE_ROLE:-external}"

MQTT_HOST="${RT_MQTT_HOST:-rt-controller}"
MQTT_PORT="${RT_MQTT_PORT:-1883}"

TOPIC_PREFIX="${RT_PRESENCE_TOPIC_PREFIX:-rt/presence}"
TOPIC="${TOPIC_PREFIX}/${NODE_ID}"

HOSTNAME="$(hostname)"

# Best-effort IPv4 detection
IP="${RT_NODE_IP:-}"
if [[ -z "${IP}" ]]; then
  IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -n1 || true)"
fi
if [[ -z "${IP}" ]]; then
  IP="$(ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 || true)"
fi

TS_MS="$(python3 - <<'PY'
import time
print(int(time.time()*1000))
PY
)"

# If IP is unknown, omit the field
if [[ -n "${IP}" ]]; then
  IP_JSON="\"ip\":\"${IP}\","
else
  IP_JSON=""
fi

PAYLOAD="$(cat <<JSON
{"schema":"node.presence.v1","node_id":"${NODE_ID}","id":"${NODE_ID}","role":"${ROLE}","hostname":"${HOSTNAME}",${IP_JSON}"status":"online","ts_ms":${TS_MS}}
JSON
)"

exec mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$TOPIC" -m "$PAYLOAD" -q 0
