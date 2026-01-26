#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

DRY_RUN="${DRY_RUN:-0}"
RSYNC_DRY=()
if [[ "${DRY_RUN}" == "1" ]]; then
  RSYNC_DRY+=(--dry-run)
  echo "[push] DRY RUN enabled: rsync will be --dry-run and NO root/systemd actions will run"
fi

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

UNIT_DIR="${REPO_ROOT}/deploy/nodes/rt-controller/systemd"

# --- Source roots (authoritative) ---
NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/"
SERVICES_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/services/"
OPS_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/ops/"

# --- Dest roots ---
NODE_DST_DIR="/opt/rollingthunder/nodes/rt-controller/"
SERVICES_DST_DIR="/opt/rollingthunder/services/"
STATE_ENV_SRC="${OPS_SRC_DIR}/service_state_publisher.env.template"
STATE_ENV_DST="/etc/rollingthunder/service_state_publisher.env"

UNITS=(
  "rollingthunder-controller.service"
  "rollingthunder-api.service"
  "rt-ui-snapshot-api.service"
  "rt-service-state-publisher.service"
  "rt-node-presence-ingestor.service"
)

# Build a safely-escaped unit string for remote shell usage
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

# Ensure dirs
echo "[push] Ensure runtime dirs exist"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p /opt/rollingthunder/services /etc/rollingthunder /opt/rollingthunder/nodes/rt-controller &&
  sudo chown root:root /opt/rollingthunder/services /etc/rollingthunder &&
  sudo chmod 755 /opt/rollingthunder/services /etc/rollingthunder
"

# Debug / validation
echo "[debug] NODE_SRC_DIR=${NODE_SRC_DIR}"
echo "[debug] SERVICES_SRC_DIR=${SERVICES_SRC_DIR}"
echo "[debug] OPS_SRC_DIR=${OPS_SRC_DIR}"
ls -la "${NODE_SRC_DIR}" || true

fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${SERVICES_SRC_DIR}"
fail_missing "${STATE_ENV_SRC}"

# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

# ---- USER-OWNED SYNC (node code) ----
# Exclude services/ to avoid duplicating the separate /opt/rollingthunder/services deploy.
# Exclude ops/ to avoid shipping templates into the runtime tree (env is installed separately).
echo "[push] Sync node subtree -> ${NODE_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  --exclude='services/' \
  --exclude='ops/' \
  "${NODE_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${NODE_DST_DIR}"

# ---- ROOT-OWNED SYNC (service executables) ----
echo "[push] Sync services subtree -> ${SERVICES_DST_DIR} (root-owned)"

if [[ "${DRY_RUN}" == "1" ]]; then
  # Pure diff view: do NOT stage to /tmp or run sudo.
  # This still gives you the "what would change" list.
  echo "[dry] services drift report:"
  rsync -avz --checksum --itemize-changes --dry-run \
    "${RSYNC_EXCLUDES[@]}" \
    "${SERVICES_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${SERVICES_DST_DIR}"
else
  # Stage to /tmp as user, then install to root-owned destination via sudo rsync.
  TMP_REMOTE="/tmp/rt_services_push.$$"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; rm -rf ${TMP_REMOTE}; mkdir -p ${TMP_REMOTE}"

  rsync -avz --checksum --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    "${SERVICES_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${TMP_REMOTE}/"

  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo rsync -a --delete ${TMP_REMOTE}/ ${SERVICES_DST_DIR}/
    sudo chmod -R 755 ${SERVICES_DST_DIR}
    rm -rf ${TMP_REMOTE}
  "
fi

# env install-if-missing
if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file_if_missing "${TARGET_HOST}" "${TARGET_USER}" "${STATE_ENV_SRC}" "${STATE_ENV_DST}" "644"
else
  echo "[dry] would ensure ${STATE_ENV_DST} exists (install-if-missing)"
fi

# units
if [[ "${DRY_RUN}" != "1" ]]; then
  for u in "${UNITS[@]}"; do
    src="${UNIT_DIR}/${u}"
    fail_missing "${src}"
    push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${src}" "/etc/systemd/system/${u}" "644"
  done
else
  echo "[dry] would push systemd unit files to /etc/systemd/system/"
fi

# systemd actions
echo "[push] systemd daemon-reload + enable + restart"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart ${UNITS_STR}
  "
else
  echo "[dry] would daemon-reload + enable + restart: ${UNITS[*]}"
fi

# Record deployed commit for visibility/debugging
GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo mkdir -p /opt/rollingthunder/.deploy
    echo ${GIT_SHA} | sudo tee /opt/rollingthunder/.deploy/DEPLOYED_COMMIT >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

# Smoke checks
if [[ "${DRY_RUN}" != "1" ]]; then
  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" "install with: sudo apt-get update && sudo apt-get install -y curl"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/api/v1/ui/nodes" 5 1.5

  echo "[smoke] redis ping"
  ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli ping || true"

  echo "[smoke] presence key rt:nodes:rt-display"
  ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli HGETALL rt:nodes:rt-display || true"

  echo "[smoke] presence key rt:nodes:rt-controller (3 samples)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    for i in 1 2 3; do
      echo \"sample=\$i\"
      redis-cli HMGET rt:nodes:rt-controller status age_sec last_seen_ms last_update_ms
      sleep 0.8
    done
  " || true
else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"
