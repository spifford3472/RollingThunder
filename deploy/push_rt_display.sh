#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-display}"
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

NODE_DIR="${REPO_ROOT}/nodes/rt-display"
OPS_DIR="${NODE_DIR}/ops"
SYSTEMD_DIR="${NODE_DIR}/systemd"
UI_DIR="${NODE_DIR}/ui"
SVC_DIR="${NODE_DIR}/services"
TOOLS_DIR="${NODE_DIR}/tools"
CONFIG_DIR="${REPO_ROOT}/config"

# runtime destinations (spiff-owned)
RT_ROOT="/opt/rollingthunder"
RT_NODE="${RT_ROOT}/nodes/rt-display"
RT_UI="${RT_NODE}/ui"
RT_SVC="${RT_NODE}/services"
RT_OPS="${RT_NODE}/ops"
RT_TOOLS="${RT_ROOT}/tools"
RT_CONFIG="${RT_ROOT}/config"

UNIT_DST_DIR="/etc/systemd/system"

UNITS=(
  "rt-display-presence.service"
  "rt-display-ui.service"
  "rt-display-kiosk.service"
  # deploy report publisher
  "rt-deploy-report-publisher.timer"
  "rt-deploy-report-publisher.service"
)

# Build a safely escaped unit list for remote shell usage
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

# --- sanity checks (directories + required templates/units) ---
fail_missing_dir "${NODE_DIR}"
fail_missing_dir "${UI_DIR}"
fail_missing_dir "${SVC_DIR}"
fail_missing_dir "${OPS_DIR}"
fail_missing_dir "${SYSTEMD_DIR}"
fail_missing_dir "${TOOLS_DIR}"
fail_missing_dir "${CONFIG_DIR}"



# These must exist because we install them as root-owned units
fail_missing "${SYSTEMD_DIR}/rt-display-presence.service"
fail_missing "${OPS_DIR}/rt-display-kiosk.service.template"
fail_missing "${OPS_DIR}/rt-display-ui.service.template"
fail_missing "${OPS_DIR}/rt-display-kiosk.sh"
fail_missing "${TOOLS_DIR}/publish_deploy_report.sh"
fail_missing "${SYSTEMD_DIR}/rt-deploy-report-publisher.service"
fail_missing "${SYSTEMD_DIR}/rt-deploy-report-publisher.timer"
fail_missing "${CONFIG_DIR}/app.json"

# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

echo "[push] Ensure runtime dirs exist (spiff-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; mkdir -p '${RT_UI}' '${RT_SVC}' '${RT_OPS}' '${RT_TOOLS}' '${RT_CONFIG}' '${RT_ROOT}/.deploy'"



echo "[push] Ensure venv exists + deps"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set -e
    cd ${RT_ROOT}
    if [ ! -x ${RT_ROOT}/.venv/bin/python ]; then
      python3 -m venv ${RT_ROOT}/.venv
    fi
    ${RT_ROOT}/.venv/bin/pip install --upgrade pip >/dev/null
    ${RT_ROOT}/.venv/bin/pip install paho-mqtt >/dev/null
  "
else
  echo "[dry] would ensure venv exists and paho-mqtt installed"
fi

# ---- USER-OWNED SYNC (ui + services + ops) ----
# This is the key change: sync directories, not individual files.

echo "[push] Sync config dir -> ${RT_CONFIG}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${CONFIG_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_CONFIG}/"

echo "[push] Sync UI dir -> ${RT_UI}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${UI_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_UI}/"

echo "[push] Sync services dir -> ${RT_SVC}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${SVC_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_SVC}/"

echo "[push] Sync ops dir -> ${RT_OPS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${OPS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_OPS}/"

echo "[push] Sync tools dir -> ${RT_TOOLS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${TOOLS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

# Ensure kiosk script is executable (only if not dry)
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[push] Ensure deploy report script is executable"
  ssh "${TARGET_USER}@${TARGET_HOST}" "chmod +x '${RT_TOOLS}/publish_deploy_report.sh'"
else
  echo "[dry] would chmod +x '${RT_TOOLS}/publish_deploy_report.sh'"
fi


# ---- ROOT-OWNED: install systemd units ----
echo "[push] Install systemd units (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  # presence unit comes from systemd dir
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-display-presence.service" \
    "${UNIT_DST_DIR}/rt-display-presence.service" "644"

  # ui/kiosk units come from ops templates (as in your original script)
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${OPS_DIR}/rt-display-ui.service.template" \
    "${UNIT_DST_DIR}/rt-display-ui.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${OPS_DIR}/rt-display-kiosk.service.template" \
    "${UNIT_DST_DIR}/rt-display-kiosk.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-deploy-report-publisher.service" \
    "${UNIT_DST_DIR}/rt-deploy-report-publisher.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-deploy-report-publisher.timer" \
    "${UNIT_DST_DIR}/rt-deploy-report-publisher.timer" "644"

else
  echo "[dry] would install systemd units to ${UNIT_DST_DIR}: ${UNITS[*]}"
fi

# ---- systemd reload + enable + restart ----
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

# Record deployed commit
GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo mkdir -p ${RT_ROOT}/.deploy
    echo ${GIT_SHA} | sudo tee ${RT_ROOT}/.deploy/DEPLOYED_COMMIT >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

# ---- Smoke checks ----
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set +e
    sudo systemctl --no-pager --full status rt-display-presence.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-display-ui.service      | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-display-kiosk.service   | sed -n '1,40p' || true
    exit 0
  "

  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" "install with: sudo apt-get update && sudo apt-get install -y curl"
  echo "[smoke] UI server health.json on :8619 (retry)"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8619/health.json" 5 1.5

  echo "[smoke] show health.json (non-fatal, if curl exists)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set +e
    if command -v curl >/dev/null 2>&1; then
      curl --max-time 1.5 -s http://127.0.0.1:8619/health.json | head -c 200; echo
    fi
    exit 0
  "

  echo "[smoke] deploy report publish now (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set +e
    sudo systemctl start rt-deploy-report-publisher.service || true
    sudo systemctl --no-pager --full status rt-deploy-report-publisher.timer | sed -n '1,25p' || true
    exit 0
  "

else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"
