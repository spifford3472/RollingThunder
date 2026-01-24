#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

# ---- repo sources ----
UNIT_DIR="${REPO_ROOT}/deploy/nodes/rt-controller/systemd"

UI_SNAPSHOT_SRC="${REPO_ROOT}/nodes/rt-controller/services/ui_snapshot_api.py"
STATE_PUB_SRC="${REPO_ROOT}/nodes/rt-controller/services/service_state_publisher.py"
PRES_INGEST_SRC="${REPO_ROOT}/nodes/rt-controller/services/node_presence_ingestor.py"

STATE_ENV_SRC="${REPO_ROOT}/nodes/rt-controller/ops/service_state_publisher.env.template"

# ---- runtime destinations ----
UI_SNAPSHOT_DST="/opt/rollingthunder/services/ui_snapshot_api.py"
STATE_PUB_DST="/opt/rollingthunder/services/service_state_publisher.py"
PRES_INGEST_DST="/opt/rollingthunder/nodes/rt-controller/node_presence_ingestor.py"

STATE_ENV_DST="/etc/rollingthunder/service_state_publisher.env"

# ---- authoritative unit set ----
UNITS=(
  "rollingthunder-controller.service"
  "rollingthunder-api.service"
  "rt-ui-snapshot-api.service"
  "rt-service-state-publisher.service"
  "rt-node-presence-ingestor.service"
)

echo "[push] Ensure runtime dirs exist (root-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  sudo mkdir -p /opt/rollingthunder/services /etc/rollingthunder &&
  sudo chown root:root /opt/rollingthunder/services /etc/rollingthunder &&
  sudo chmod 755 /opt/rollingthunder/services &&
  sudo chmod 755 /etc/rollingthunder
"

echo "[push] node_presence_ingestor.py (spiff-owned) -> ${PRES_INGEST_DST}"
scp "${PRES_INGEST_SRC}" "${TARGET_USER}@${TARGET_HOST}:${PRES_INGEST_DST}"

push_root_file () {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local tmp="/tmp/$(basename "$dst").$$"

  echo "[push] $(basename "$dst") (root-owned) -> ${dst}"
  scp "$src" "${TARGET_USER}@${TARGET_HOST}:${tmp}"
  ssh "${TARGET_USER}@${TARGET_HOST}" "sudo mv '${tmp}' '${dst}' && sudo chown root:root '${dst}' && sudo chmod ${mode} '${dst}'"
}

# root-owned service executables
push_root_file "${UI_SNAPSHOT_SRC}" "${UI_SNAPSHOT_DST}" "755"
push_root_file "${STATE_PUB_SRC}"   "${STATE_PUB_DST}"   "755"

# env (root-owned, 644)
# NOTE: if you want "do not overwrite existing", swap mv for install -n or a conditional
push_root_file "${STATE_ENV_SRC}" "${STATE_ENV_DST}" "644"

# systemd units (root-owned, 644)
for u in "${UNITS[@]}"; do
  src="${UNIT_DIR}/${u}"
  dst="/etc/systemd/system/${u}"
  if [[ ! -f "${src}" ]]; then
    echo "[error] missing unit in repo: ${src}"
    exit 1
  fi
  push_root_file "${src}" "${dst}" "644"
done

echo "[push] systemd daemon-reload + enable + restart authoritative units"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  sudo systemctl daemon-reload &&
  sudo systemctl enable ${UNITS[*]} &&
  sudo systemctl restart ${UNITS[*]}
"

echo "[smoke] rt-ui-snapshot-api http status"
ssh "${TARGET_USER}@${TARGET_HOST}" "curl -s -o /dev/null -w 'http=%{http_code}\n' http://127.0.0.1:8625/api/v1/ui/nodes || true"

echo "[smoke] redis ping"
ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli ping || true"

echo "[smoke] presence key rt:nodes:rt-display"
ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli HGETALL rt:nodes:rt-display || true"

echo "[push] Done."
