#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-ki5vnb-dmr2}"
TARGET_USER="${RT_SSH_USER:-pi-star}"

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

# --- Sources ---
NODE_DIR="${REPO_ROOT}/nodes/rt-wpsd"
TOOLS_SRC_DIR="${NODE_DIR}/tools"
SERVICES_SRC_DIR="${NODE_DIR}/services"
SYSTEMD_SRC_DIR="${NODE_DIR}/systemd"

# --- Destinations ---
RT_ROOT="/opt/rollingthunder"
RT_NODE="${RT_ROOT}/nodes/rt-wpsd"
RT_NODE_SERVICES="${RT_NODE}/services"
RT_TOOLS="${RT_ROOT}/tools"

UNIT_DST_DIR="/etc/systemd/system"

LEGACY_UNITS=(
  "rt-deploy-report-publisher.service"
  "rt-deploy-report-publisher.timer"
)

# Units we actually want on Pi-Star
UNITS=(
  "rt-presence-publisher.service"
  "rt-presence-publisher.timer"
  "rt-wpsd-deploy-report-publisher.service"
  "rt-wpsd-deploy-report-publisher.timer"
)
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

# --- Sanity checks ---
fail_missing_dir "${NODE_DIR}"
fail_missing_dir "${TOOLS_SRC_DIR}"
fail_missing_dir "${SYSTEMD_SRC_DIR}"

# tools required
fail_missing "${TOOLS_SRC_DIR}/publish_deploy_report.sh"
fail_missing "${TOOLS_SRC_DIR}/publish_presence.sh"


# systemd required
for u in "${UNITS[@]}"; do
  fail_missing "${SYSTEMD_SRC_DIR}/${u}"
done

# optional python services dir
if [[ -d "${SERVICES_SRC_DIR}" ]]; then
  : # ok
fi

echo "[push] Ensure runtime dirs exist (and are user-owned where needed)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p '${RT_ROOT}' '${RT_ROOT}/.deploy' '${RT_NODE}' '${RT_NODE_SERVICES}' '${RT_TOOLS}' /etc/rollingthunder;
  sudo chown -R '${TARGET_USER}:${TARGET_USER}' '${RT_NODE}' '${RT_TOOLS}' '${RT_ROOT}/.deploy' || true
"

echo "[push] Sync tools -> ${RT_TOOLS}/ (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${TOOLS_SRC_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

if [[ -d "${SERVICES_SRC_DIR}" ]]; then
  echo "[push] Sync optional python services -> ${RT_NODE_SERVICES}/ (user-owned)"
  rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
    --no-group --no-perms --omit-dir-times \
    "${RSYNC_EXCLUDES[@]}" \
    "${SERVICES_SRC_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_NODE_SERVICES}/"
fi

echo "[push] Ensure tool scripts executable"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    chmod +x '${RT_TOOLS}/publish_deploy_report.sh' '${RT_TOOLS}/publish_presence.sh' || true
  "
else
  echo "[dry] would chmod +x publish_* scripts"
fi

echo "[push] Install systemd units (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  for u in "${UNITS[@]}"; do
    push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
      "${SYSTEMD_SRC_DIR}/${u}" \
      "${UNIT_DST_DIR}/${u}" "644"
  done
else
  echo "[dry] would install units: ${UNITS[*]}"
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

echo "[push] systemd daemon-reload + enable + restart timers"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart ${UNITS_STR}
  "
else
  echo "[dry] would daemon-reload + enable + restart: ${UNITS[*]}"
fi

GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    echo '${GIT_SHA}' | sudo tee '${RT_ROOT}/.deploy/DEPLOYED_COMMIT' >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] timers"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    systemctl status rt-presence-publisher.timer --no-pager | sed -n '1,25p' || true
    systemctl status rt-wpsd-deploy-report-publisher.timer --no-pager | sed -n '1,25p' || true
    exit 0
  "
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"
