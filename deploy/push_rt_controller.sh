#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

# --- repo invariants (deploy gate) ---
deploy_entry


DRY_RUN="${DRY_RUN:-0}"
RSYNC_DRY=()
if [[ "${DRY_RUN}" == "1" ]]; then
  RSYNC_DRY+=(--dry-run)
  echo "[push] DRY RUN enabled: rsync will be --dry-run and NO root/systemd actions will run"
fi

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

UNIT_DIR="${REPO_ROOT}/deploy/nodes/rt-controller/systemd"
GPS_UNIT_SRC="${REPO_ROOT}/nodes/rt-controller/systemd/rt-gps-state-publisher.service"

# --- Source roots (authoritative) ---
NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/"
SERVICES_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/services/"
OPS_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/ops/"

# Thin-client UI/runtime sources (served by rt-controller)
UI_SRC_DIR="${REPO_ROOT}/nodes/rt-display/ui/"
CFG_SRC_DIR="${REPO_ROOT}/config/"

# --- Dest roots ---
NODE_DST_DIR="/opt/rollingthunder/nodes/rt-controller/"
SERVICES_DST_DIR="/opt/rollingthunder/services/"
STATE_ENV_SRC="${OPS_SRC_DIR}/service_state_publisher.env.template"
STATE_ENV_DST="/etc/rollingthunder/service_state_publisher.env"

# Thin-client UI/runtime destinations (served by rt-controller)
UI_DST_DIR="/opt/rollingthunder/ui/"
CFG_DST_DIR="/opt/rollingthunder/config/"

UNITS=(
  "rollingthunder-controller.service"
  "rollingthunder-api.service"
  "rt-ui-snapshot-api.service"
  "rt-service-state-publisher.service"
  "rt-node-presence-ingestor.service"
  # Deploy Report Publisher (controller -> Redis)
  "rt-deploy-report-controller.service"
  "rt-deploy-report-controller.timer"
  "rt-gps-state-publisher.service"
  "rt-env-temp-publisher.service"
)

# Build a safely-escaped unit string for remote shell usage
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

# Ensure dirs
echo "[push] Ensure runtime dirs exist"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p \
    /opt/rollingthunder/services \
    /etc/rollingthunder \
    /opt/rollingthunder/nodes/rt-controller \
    /opt/rollingthunder/ui \
    /opt/rollingthunder/config &&
  sudo chown root:root /opt/rollingthunder/services /etc/rollingthunder /opt/rollingthunder/ui /opt/rollingthunder/config &&
  sudo chmod 755 /opt/rollingthunder/services /etc/rollingthunder /opt/rollingthunder/ui /opt/rollingthunder/config
"

# Debug / validation
echo "[debug] NODE_SRC_DIR=${NODE_SRC_DIR}"
echo "[debug] SERVICES_SRC_DIR=${SERVICES_SRC_DIR}"
echo "[debug] OPS_SRC_DIR=${OPS_SRC_DIR}"
echo "[debug] UI_SRC_DIR=${UI_SRC_DIR}"
echo "[debug] CFG_SRC_DIR=${CFG_SRC_DIR}"
ls -la "${NODE_SRC_DIR}" || true

fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${SERVICES_SRC_DIR}"
fail_missing "${STATE_ENV_SRC}"
fail_missing "${GPS_UNIT_SRC}"

# Thin-client runtime assets must exist locally in repo
fail_missing_dir "${UI_SRC_DIR}"
fail_missing_dir "${CFG_SRC_DIR}"
fail_missing "${CFG_SRC_DIR}/app.json"

# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
  --exclude='.dev/'
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
  echo "[dry] services drift report:"
  rsync -avz --checksum --itemize-changes --dry-run \
    "${RSYNC_EXCLUDES[@]}" \
    "${SERVICES_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${SERVICES_DST_DIR}"
else
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

# ---- ROOT-OWNED SYNC (thin-client UI assets) ----
echo "[push] Sync UI assets -> ${UI_DST_DIR} (root-owned, served at /ui/*)"

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[dry] ui drift report:"
  rsync -avz --checksum --itemize-changes --dry-run \
    "${RSYNC_EXCLUDES[@]}" \
    "${UI_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${UI_DST_DIR}"
else
  TMP_UI_REMOTE="/tmp/rt_ui_push.$$"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; rm -rf ${TMP_UI_REMOTE}; mkdir -p ${TMP_UI_REMOTE}"

  rsync -avz --checksum --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    "${UI_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${TMP_UI_REMOTE}/"

  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo rsync -a --delete ${TMP_UI_REMOTE}/ ${UI_DST_DIR}/
    sudo chown -R root:root ${UI_DST_DIR}
    sudo chmod -R 755 ${UI_DST_DIR}
    rm -rf ${TMP_UI_REMOTE}
  "
fi

# ---- ROOT-OWNED SYNC (thin-client config assets) ----
echo "[push] Sync config assets -> ${CFG_DST_DIR} (root-owned, served at /config/*)"

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[dry] config drift report:"
  rsync -avz --checksum --itemize-changes --dry-run \
    "${RSYNC_EXCLUDES[@]}" \
    "${CFG_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${CFG_DST_DIR}"
else
  TMP_CFG_REMOTE="/tmp/rt_cfg_push.$$"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; rm -rf ${TMP_CFG_REMOTE}; mkdir -p ${TMP_CFG_REMOTE}"

  rsync -avz --checksum --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    "${CFG_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${TMP_CFG_REMOTE}/"

  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo rsync -a --delete ${TMP_CFG_REMOTE}/ ${CFG_DST_DIR}/
    sudo chown -R root:root ${CFG_DST_DIR}
    sudo chmod -R 755 ${CFG_DST_DIR}
    rm -rf ${TMP_CFG_REMOTE}
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
    if [[ "${u}" == "rt-gps-state-publisher.service" ]]; then
      push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${GPS_UNIT_SRC}" "/etc/systemd/system/${u}" "644"
    else
      src="${UNIT_DIR}/${u}"
      fail_missing "${src}"
      push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${src}" "/etc/systemd/system/${u}" "644"
    fi
  done
else
  echo "[dry] would push systemd unit files to /etc/systemd/system/"
fi

echo "[guard] rt-node-presence-ingestor ExecStart must point to /opt/rollingthunder/services/"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  systemctl show -p ExecStart rt-node-presence-ingestor.service | grep -q '/opt/rollingthunder/services/node_presence_ingestor.py' \
    || (echo '[error] rt-node-presence-ingestor ExecStart is wrong' && systemctl show -p ExecStart rt-node-presence-ingestor.service && exit 2)
"

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

#Safety cleanup: remove old UI dev dir if it exists (it shouldn't, but just in case)
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; rm -rf /opt/rollingthunder/ui/dev || true"

# Clean deprecated duplicate(s) that must never exist on target
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[push] Remove deprecated node-level copies of service executables (if any)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    rm -f /opt/rollingthunder/nodes/rt-controller/node_presence_ingestor.py || true
  "
else
  echo "[dry] would remove /opt/rollingthunder/nodes/rt-controller/node_presence_ingestor.py if present"
fi

# Guardrail: service executables must NOT live in node tree
if [[ -f "${NODE_SRC_DIR}/node_presence_ingestor.py" ]]; then
  echo "[error] node_presence_ingestor.py found under nodes/rt-controller/. It must live under nodes/rt-controller/services/ only."
  exit 2
fi


# Smoke checks
if [[ "${DRY_RUN}" != "1" ]]; then
  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" "install with: sudo apt-get update && sudo apt-get install -y curl"

  echo "[smoke] api nodes"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/api/v1/ui/nodes" 5 1.5

  echo "[smoke] ui index"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/ui/index.html" 5 1.5

  echo "[smoke] config app.json"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/config/app.json" 5 1.5

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

  echo "[smoke] deploy report key rt:deploy:report:rt-controller"
  ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli GET rt:deploy:report:rt-controller | head -c 200 && echo || true"
else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"
