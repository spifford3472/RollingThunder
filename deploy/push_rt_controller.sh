#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

UNIT_DIR="${REPO_ROOT}/deploy/nodes/rt-controller/systemd"

UI_SNAPSHOT_SRC="${REPO_ROOT}/nodes/rt-controller/services/ui_snapshot_api.py"
STATE_PUB_SRC="${REPO_ROOT}/nodes/rt-controller/services/service_state_publisher.py"
PRES_INGEST_SRC="${REPO_ROOT}/nodes/rt-controller/services/node_presence_ingestor.py"
STATE_ENV_SRC="${REPO_ROOT}/nodes/rt-controller/ops/service_state_publisher.env.template"

UI_SNAPSHOT_DST="/opt/rollingthunder/services/ui_snapshot_api.py"
STATE_PUB_DST="/opt/rollingthunder/services/service_state_publisher.py"
PRES_INGEST_DST="/opt/rollingthunder/nodes/rt-controller/node_presence_ingestor.py"
STATE_ENV_DST="/etc/rollingthunder/service_state_publisher.env"

UNITS=(
  "rollingthunder-controller.service"
  "rollingthunder-api.service"
  "rt-ui-snapshot-api.service"
  "rt-service-state-publisher.service"
  "rt-node-presence-ingestor.service"
)

# Ensure dirs
echo "[push] Ensure runtime dirs exist"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p /opt/rollingthunder/services /etc/rollingthunder &&
  sudo chown root:root /opt/rollingthunder/services /etc/rollingthunder &&
  sudo chmod 755 /opt/rollingthunder/services /etc/rollingthunder &&
  mkdir -p /opt/rollingthunder/nodes/rt-controller
"

# spiff-owned node code
echo "[push] node_presence_ingestor.py (spiff-owned) -> ${PRES_INGEST_DST}"
scp "${PRES_INGEST_SRC}" "${TARGET_USER}@${TARGET_HOST}:${PRES_INGEST_DST}"

# root-owned executables
push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${UI_SNAPSHOT_SRC}" "${UI_SNAPSHOT_DST}" "755"
push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${STATE_PUB_SRC}"   "${STATE_PUB_DST}"   "755"

# env install-if-missing
push_root_file_if_missing "${TARGET_HOST}" "${TARGET_USER}" "${STATE_ENV_SRC}" "${STATE_ENV_DST}" "644"

# units
for u in "${UNITS[@]}"; do
  src="${UNIT_DIR}/${u}"
  fail_missing "${src}"
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${src}" "/etc/systemd/system/${u}" "644"
done

echo "[push] systemd daemon-reload + enable + restart"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  set -e
  sudo systemctl daemon-reload
  sudo systemctl enable ${UNITS[*]}
  sudo systemctl restart ${UNITS[*]}
"

require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" "install with: sudo apt-get update && sudo apt-get install -y curl"
curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/api/v1/ui/nodes" 5 1.5

echo "[smoke] redis ping"
ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli ping || true"

echo "[smoke] presence key rt:nodes:rt-display"
ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli HGETALL rt:nodes:rt-display || true"

echo "[push] Done."
