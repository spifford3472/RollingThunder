#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-radio}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

# HARD GUARD: always validate repo invariants (even in DRY_RUN)
verify_repo_invariants

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

# ---- Sources ----
NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-radio/"
TOOLS_SRC_DIR="${REPO_ROOT}/nodes/rt-radio/tools"
SYSTEMD_DIR="${REPO_ROOT}/nodes/rt-radio/systemd"

PRES_UNIT_SRC="${SYSTEMD_DIR}/rt-radio-presence.service"
PRES_UNIT_DST="/etc/systemd/system/rt-radio-presence.service"

DEPLOY_TOOL_SRC="${TOOLS_SRC_DIR}/publish_deploy_report.sh"
DEPLOY_TOOL_DST="/opt/rollingthunder/tools/publish_deploy_report.sh"

DEPLOY_SVC_SRC="${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.service"
DEPLOY_SVC_DST="/etc/systemd/system/rt-radio-deploy-report-publisher.service"
DEPLOY_TMR_SRC="${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.timer"
DEPLOY_TMR_DST="/etc/systemd/system/rt-radio-deploy-report-publisher.timer"

# ---- Dests ----
NODE_DST_DIR="/opt/rollingthunder/nodes/rt-radio/"

LEGACY_UNITS=(
  "rt-deploy-report-publisher.service"
  "rt-deploy-report-publisher.timer"
)

UNITS=(
  "rt-radio-presence.service"
  "rt-radio-deploy-report-publisher.service"
  "rt-radio-deploy-report-publisher.timer"
)
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

# ---- Sanity checks ----
fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${TOOLS_SRC_DIR}"
fail_missing_dir "${SYSTEMD_DIR}"

fail_missing "${PRES_UNIT_SRC}"
fail_missing "${DEPLOY_TOOL_SRC}"
fail_missing "${DEPLOY_SVC_SRC}"
fail_missing "${DEPLOY_TMR_SRC}"


echo "[push] Ensure runtime dirs exist"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p /opt/rollingthunder/nodes /opt/rollingthunder/tools /etc/rollingthunder &&
  sudo chown -R ${TARGET_USER}:${TARGET_USER} /opt/rollingthunder/nodes /opt/rollingthunder/tools &&
  sudo chmod 755 /etc/rollingthunder
"
echo "[push] Ensure mosquitto_pub exists (mosquitto-clients)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set -e
    if ! command -v mosquitto_pub >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y mosquitto-clients
    fi
  "
else
  echo "[dry] would ensure mosquitto-clients installed"
fi

# node.json (as you had it)
NODE_JSON_SRC="${REPO_ROOT}/deploy/common/node_json/${TARGET_HOST}.node.json"
if [[ "${DRY_RUN}" != "1" ]]; then
  push_node_json "${TARGET_HOST}" "${TARGET_USER}" "${NODE_JSON_SRC}" "644"
else
  echo "[dry] would ensure /etc/rollingthunder/node.json exists (or overwrite if FORCE_NODE_JSON=1)"
fi

echo "[push] Sync node subtree -> ${NODE_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  --exclude='tools/' \
  --exclude='systemd/' \
  "${NODE_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${NODE_DST_DIR}"

# tools: publish_deploy_report.sh goes to /opt/rollingthunder/tools/
echo "[push] Install deploy report tool -> ${DEPLOY_TOOL_DST} (user-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  rsync -avz --checksum --itemize-changes \
    --no-group --no-perms --omit-dir-times \
    "${DEPLOY_TOOL_SRC}" "${TARGET_USER}@${TARGET_HOST}:${DEPLOY_TOOL_DST}"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; chmod +x '${DEPLOY_TOOL_DST}'"
else
  echo "[dry] would rsync ${DEPLOY_TOOL_SRC} -> ${DEPLOY_TOOL_DST} and chmod +x"
fi

echo "[push] Remove legacy deploy-report units (if present)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl stop rt-deploy-report-publisher.timer rt-deploy-report-publisher.service 2>/dev/null || true
    sudo systemctl disable rt-deploy-report-publisher.timer rt-deploy-report-publisher.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/rt-deploy-report-publisher.timer /etc/systemd/system/rt-deploy-report-publisher.service
    sudo systemctl daemon-reload
    exit 0
  "
else
  echo \"[dry] would stop/disable/remove: ${LEGACY_UNITS[*]}\"
fi

# systemd units
if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${PRES_UNIT_SRC}" "${PRES_UNIT_DST}" "644"
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${DEPLOY_SVC_SRC}" "${DEPLOY_SVC_DST}" "644"
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${DEPLOY_TMR_SRC}" "${DEPLOY_TMR_DST}" "644"

  echo "[push] systemd daemon-reload + enable + restart"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart rt-radio-presence.service
    sudo systemctl restart rt-radio-deploy-report-publisher.timer
  "

  # Record deployed commit
  GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo mkdir -p /opt/rollingthunder/.deploy
    echo ${GIT_SHA} | sudo tee /opt/rollingthunder/.deploy/DEPLOYED_COMMIT >/dev/null
  "
else
  echo "[dry] would push units and enable/restart: ${UNITS[*]}"
  echo "[dry] would record deployed commit"
fi

# Smoke checks
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] deploy report timer status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set +e
    sudo systemctl --no-pager --full status rt-radio-deploy-report-publisher.timer | sed -n '1,30p' || true
    sudo systemctl --no-pager --full status rt-radio-deploy-report-publisher.service | sed -n '1,30p' || true
    exit 0
  "

  echo "[smoke] run deploy report once now (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "
    set +e
    sudo systemctl start rt-radio-deploy-report-publisher.service || true
    exit 0
  "
else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done."
