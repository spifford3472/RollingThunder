#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-display}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"
COMMON_SERVICES_SRC_DIR="${REPO_ROOT}/nodes/common/services/"
COMMON_SERVICES_DST_DIR="/opt/rollingthunder/nodes/common/services/"

# --- repo invariants (deploy gate) ---
deploy_entry

DRY_RUN="${DRY_RUN:-0}"
RSYNC_DRY=()
if [[ "${DRY_RUN}" == "1" ]]; then
  RSYNC_DRY+=(--dry-run)
  echo "[push] DRY RUN enabled: rsync will be --dry-run and NO root/systemd actions will run"
fi

# If set to 1, we will remove legacy UI/config dirs from rt-display to avoid future confusion.
CLEAN_LEGACY="${CLEAN_LEGACY:-0}"

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

NODE_DIR="${REPO_ROOT}/nodes/rt-display"
OPS_DIR="${NODE_DIR}/ops"
SYSTEMD_DIR="${NODE_DIR}/systemd"
SVC_DIR="${NODE_DIR}/services"
TOOLS_DIR="${NODE_DIR}/tools"
GLOBAL_TOOLS_DIR="${REPO_ROOT}/tools"
SVC_USER_DIR="${NODE_DIR}/autostart_systemd"
SVC_AUTOSTART_DIR="${NODE_DIR}/autostart"

# runtime destinations (spiff-owned)
RT_ROOT="/opt/rollingthunder"
RT_NODE="${RT_ROOT}/nodes/rt-display"
RT_SVC="${RT_NODE}/services"
RT_OPS="${RT_NODE}/ops"
RT_TOOLS="${RT_ROOT}/tools"
RT_AUTOSTART="/home/spiff/.config/autostart"
RT_USER_SVC="/home/spiff/.config/systemd/user"

UNIT_DST_DIR="/etc/systemd/system"

LEGACY_UNITS=(
  "rt-deploy-report-publisher.service"
  "rt-deploy-report-publisher.timer"
)

# NOTE: rt-display-kiosk.service is a user service (autostart_systemd) not a system unit.
# The watchdog monitors it via pgrep and restarts it via systemctl --user as root sudo.
UNITS=(
  "rt-display-presence.service"

  # per-node UI intent worker (unique unit name)
  "rt-display-ui-intent-worker.service"

  # deploy report publisher
  "rt-display-deploy-report-publisher.timer"
  "rt-display-deploy-report-publisher.service"
)

UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

# --- sanity checks ---
fail_missing_dir "${NODE_DIR}"
fail_missing_dir "${OPS_DIR}"
fail_missing_dir "${SYSTEMD_DIR}"
fail_missing_dir "${SVC_DIR}"
fail_missing_dir "${TOOLS_DIR}"
fail_missing_dir "${SVC_USER_DIR}"
fail_missing_dir "${SVC_AUTOSTART_DIR}"

fail_missing "${SYSTEMD_DIR}/rt-display-presence.service"
fail_missing "${SYSTEMD_DIR}/rt-display-ui-intent-worker.service"
fail_missing "${SYSTEMD_DIR}/rt-display-deploy-report-publisher.service"
fail_missing "${SYSTEMD_DIR}/rt-display-deploy-report-publisher.timer"

fail_missing "${SVC_DIR}/rt-display-kiosk.sh"
fail_missing "${SVC_DIR}/rt-display-kiosk-watchdog.sh"

fail_missing "${TOOLS_DIR}/publish_deploy_report.sh"
fail_missing "${GLOBAL_TOOLS_DIR}/ui_intent_worker.py"

fail_missing "${SVC_DIR}/rt-display-ui-intent-worker.py"

# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

echo "[push] Ensure runtime dirs exist (spiff-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  mkdir -p '${RT_SVC}' '${RT_OPS}' '${RT_TOOLS}' '${RT_ROOT}/.deploy';
  mkdir -p '${RT_NODE}' '${RT_AUTOSTART}' '${RT_USER_SVC}';
"

# Optional legacy cleanup (strongly recommended once)
if [[ "${CLEAN_LEGACY}" == "1" ]]; then
  echo "[push] CLEAN_LEGACY=1 set: removing legacy UI/config payloads from rt-display"
  if [[ "${DRY_RUN}" != "1" ]]; then
    ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
      rm -rf '${RT_NODE}/ui' '${RT_ROOT}/config' '${RT_NODE}/config' || true
      rm -rf '${RT_NODE}/www' '${RT_NODE}/static' || true
    "
  else
    echo "[dry] would rm -rf ${RT_NODE}/ui ${RT_ROOT}/config ${RT_NODE}/config ..."
  fi
fi

echo "[push] Disable/remove legacy system rt-display-kiosk.service"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl stop rt-display-kiosk.service 2>/dev/null || true
    sudo systemctl disable rt-display-kiosk.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/rt-display-kiosk.service
    sudo rm -f /etc/systemd/system/graphical.target.wants/rt-display-kiosk.service
    sudo systemctl daemon-reload
    sudo systemctl reset-failed rt-display-kiosk.service 2>/dev/null || true
    exit 0
  "
else
  echo "[dry] would stop/disable/remove legacy system rt-display-kiosk.service"
fi

echo "[push] Ensure venv exists + deps"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    cd '${RT_ROOT}'
    if [ ! -x '${RT_ROOT}/.venv/bin/python' ]; then
      python3 -m venv '${RT_ROOT}/.venv'
    fi
    # Only upgrade pip/deps if PyPI is reachable
    if curl -fsS --max-time 5 https://pypi.org >/dev/null 2>&1; then
      '${RT_ROOT}/.venv/bin/pip' install --upgrade pip >/dev/null
      '${RT_ROOT}/.venv/bin/pip' install paho-mqtt >/dev/null
      '${RT_ROOT}/.venv/bin/pip' install redis >/dev/null
    else
      echo '[venv] PyPI unreachable, skipping pip upgrade (using existing packages)'
    fi
  "
else
  echo "[dry] would ensure venv exists and paho-mqtt + redis installed"
fi

echo "[push] Sync common python services -> ${COMMON_SERVICES_DST_DIR} (user-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "mkdir -p ${COMMON_SERVICES_DST_DIR}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-times \
  --no-group --no-perms --omit-dir-times \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
  --exclude='.venv/' --exclude='.git/' --exclude='.dev/' \
  "${COMMON_SERVICES_SRC_DIR}" \
  "${TARGET_USER}@${TARGET_HOST}:${COMMON_SERVICES_DST_DIR}"

# ---- USER-OWNED SYNC ----
echo "[push] Sync user systemd service -> ${RT_USER_SVC}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${SVC_USER_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_USER_SVC}/"

echo "[push] Sync desktop autostart -> ${RT_AUTOSTART}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${SVC_AUTOSTART_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_AUTOSTART}/"

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

# Global tools always overwrite — shared across nodes, may contain cross-node updates
echo "[push] Sync global tools dir -> ${RT_TOOLS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${GLOBAL_TOOLS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

# Ensure scripts executable
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[push] Ensure scripts executable"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    chmod +x '${RT_OPS}/rt-display-kiosk.sh' || true
    chmod +x '${RT_SVC}/rt-display-kiosk-watchdog.sh' || true
    chmod +x '${RT_TOOLS}/publish_deploy_report.sh' || true
    chmod +x '${RT_SVC}/rt-display-ui-intent-worker.py' || true
    chmod +x '${RT_AUTOSTART}/rt-display-kiosk.desktop' || true
    chmod +x '${RT_USER_SVC}/rt-display-kiosk.service' || true
  "
else
  echo "[dry] would chmod +x kiosk + watchdog + publish_deploy_report + intent-worker + autostart"
fi

# ---- ROOT-OWNED: install systemd units ----
echo "[push] Install systemd units (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-display-presence.service" \
    "${UNIT_DST_DIR}/rt-display-presence.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-display-ui-intent-worker.service" \
    "${UNIT_DST_DIR}/rt-display-ui-intent-worker.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-display-deploy-report-publisher.service" \
    "${UNIT_DST_DIR}/rt-display-deploy-report-publisher.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-display-deploy-report-publisher.timer" \
    "${UNIT_DST_DIR}/rt-display-deploy-report-publisher.timer" "644"
else
  echo "[dry] would install systemd units to ${UNIT_DST_DIR}: ${UNITS[*]}"
fi

# ---- disable legacy rt-display-ui.service if it exists ----
echo "[push] Disable legacy rt-display-ui.service (if present)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl stop rt-display-ui.service 2>/dev/null || true
    sudo systemctl disable rt-display-ui.service 2>/dev/null || true
    sudo rm -f '${UNIT_DST_DIR}/rt-display-ui.service' 2>/dev/null || true
    exit 0
  "
else
  echo "[dry] would stop/disable/remove rt-display-ui.service if present"
fi

echo "[push] Remove legacy deploy-report units (if present)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl stop rt-deploy-report-publisher.timer rt-deploy-report-publisher.service 2>/dev/null || true
    sudo systemctl disable rt-deploy-report-publisher.timer rt-deploy-report-publisher.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/rt-deploy-report-publisher.timer
    sudo rm -f /etc/systemd/system/rt-deploy-report-publisher.service
    sudo systemctl daemon-reload
    exit 0
  "
else
  echo "[dry] would stop/disable/remove: ${LEGACY_UNITS[*]}"
fi

# ---- systemd reload + enable + restart (system units only) ----
echo "[push] systemd daemon-reload + enable + restart (system units)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart ${UNITS_STR}
  "
else
  echo "[dry] would daemon-reload + enable + restart: ${UNITS[*]}"
fi

# ---- user systemd reload + enable + restart (kiosk user service) ----
echo "[push] user systemd daemon-reload + enable (kiosk user service)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    systemctl --user daemon-reload
    systemctl --user enable rt-display-kiosk.service
  "
else
  echo "[dry] would user daemon-reload + enable rt-display-kiosk.service"
fi

echo "[push] user systemd daemon-reload + enable (kiosk watchdog user service)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    systemctl --user daemon-reload
    systemctl --user enable rt-display-kiosk-watchdog.service
  "
else
  echo "[dry] would user daemon-reload + enable rt-display-kiosk-watchdog.service"
fi

# Record deployed commit
GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo mkdir -p '${RT_ROOT}/.deploy'
    echo '${GIT_SHA}' | sudo tee '${RT_ROOT}/.deploy/DEPLOYED_COMMIT' >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

# ---- Smoke checks ----
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] system unit status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl --no-pager --full status rt-display-presence.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-display-kiosk-watchdog.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-display-ui-intent-worker.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-display-deploy-report-publisher.timer | sed -n '1,40p' || true
    exit 0
  "

  echo "[smoke] user unit status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    systemctl --user --no-pager --full status rt-display-kiosk.service | sed -n '1,40p' || true
    exit 0
  "

  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" \
    "install with: sudo apt-get update && sudo apt-get install -y curl"

  echo "[smoke] kiosk target reachable? (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    curl -fsS --max-time 2 'http://rt-controller:8625/ui/index.html?runtime=1&page=home' >/dev/null \
      && echo OK || echo WARN
    exit 0
  "
else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"