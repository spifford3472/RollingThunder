#!/usr/bin/env bash
set -euo pipefail

NODE_ID="${RT_NODE_ID:-$(hostname)}"
ROLE="${RT_NODE_ROLE:-display}"
DEPLOYED_COMMIT_FILE="/opt/rollingthunder/.deploy/DEPLOYED_COMMIT"

MQTT_HOST="${RT_MQTT_HOST:-rt-controller}"
MQTT_PORT="${RT_MQTT_PORT:-1883}"
TOPIC="rt/deploy/report/${NODE_ID}"

TS_MS="$(python3 - <<'PY'
import time
print(int(time.time()*1000))
PY
)"

DEPLOYED_COMMIT="unknown"
if [[ -f "$DEPLOYED_COMMIT_FILE" ]]; then
  DEPLOYED_COMMIT="$(tr -d ' \n\r\t' < "$DEPLOYED_COMMIT_FILE")"
fi

UNITS=("rt-display-kiosk.service" "rt-display-presence.service" "rt-display-ui.service")

UNITS_JSON="{"
first=1
for u in "${UNITS[@]}"; do
  p="/etc/systemd/system/${u}"
  h="missing"
  if [[ -f "$p" ]]; then
    h="$(sha256sum "$p" | awk '{print $1}')"
  fi
  if [[ $first -eq 0 ]]; then UNITS_JSON+=", "; fi
  first=0
  UNITS_JSON+="\"${u}\": \"sha256:${h}\""
done
UNITS_JSON+="}"

PAYLOAD="$(cat <<JSON
{
  "schema": "deploy.report.v1",
  "node_id": "${NODE_ID}",
  "role": "${ROLE}",
  "ts_ms": ${TS_MS},
  "deployed_commit": "${DEPLOYED_COMMIT}",
  "git_head": null,
  "dirty": null,
  "units": ${UNITS_JSON}
}
JSON
)"

mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$TOPIC" -m "$PAYLOAD" -q 0
