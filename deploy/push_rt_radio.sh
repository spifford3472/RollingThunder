#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-radio}"
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

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

# ---- Sources ----
NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-radio"
SYSTEMD_DIR="${NODE_SRC_DIR}/systemd"
TOOLS_DIR="${NODE_SRC_DIR}/tools"
SVC_DIR="${NODE_SRC_DIR}/services"
ENV_SRC_DIR="${NODE_SRC_DIR}/env"
UDEV_SRC_DIR="${NODE_SRC_DIR}/udev"

GLOBAL_TOOLS_DIR="${REPO_ROOT}/tools"

# ---- Runtime destinations (spiff-owned where appropriate) ----
RT_ROOT="/opt/rollingthunder"
RT_NODE="${RT_ROOT}/nodes/rt-radio"
RT_SVC="${RT_NODE}/services"
RT_OPS="${RT_NODE}/ops"
RT_TOOLS="${RT_ROOT}/tools"

ENV_DST_DIR="/etc/rollingthunder"
UDEV_DST_DIR="/etc/udev/rules.d"
UNIT_DST_DIR="/etc/systemd/system"

# ---- Units ----
LEGACY_UNITS=(
  "rt-deploy-report-publisher.service"
  "rt-deploy-report-publisher.timer"
)

UNITS=(
  "rt-radio-presence.service"
  "rt-radio-ui-intent-worker.service"
  "rt-radio-deploy-report-publisher.timer"
  "rt-radio-deploy-report-publisher.service"
  "rigctld.service"
  "rt-rigctld-watchdog.service"
)

UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

# ---- Sanity checks ----
fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${SYSTEMD_DIR}"
fail_missing_dir "${TOOLS_DIR}"
fail_missing_dir "${SVC_DIR}"
fail_missing_dir "${ENV_SRC_DIR}"
fail_missing_dir "${UDEV_SRC_DIR}"
fail_missing_dir "${GLOBAL_TOOLS_DIR}"

fail_missing "${UDEV_SRC_DIR}/99-rollingthunder-ft891.rules"
fail_missing "${ENV_SRC_DIR}/radio.env"

fail_missing "${SYSTEMD_DIR}/rt-radio-presence.service"
fail_missing "${SYSTEMD_DIR}/rt-radio-ui-intent-worker.service"
fail_missing "${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.service"
fail_missing "${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.timer"
fail_missing "${SYSTEMD_DIR}/rigctld.service"
fail_missing "${SYSTEMD_DIR}/rt-rigctld-watchdog.service"

fail_missing "${TOOLS_DIR}/publish_deploy_report.sh"
fail_missing "${GLOBAL_TOOLS_DIR}/ui_intent_worker.py"

# radio package files required by shared ui_intent_worker.py on rt-radio
fail_missing "${SVC_DIR}/radio/__init__.py"
fail_missing "${SVC_DIR}/radio/config.py"
fail_missing "${SVC_DIR}/radio/hamlib_client.py"
fail_missing "${SVC_DIR}/radio/service.py"
fail_missing "${SVC_DIR}/radio/radios/__init__.py"
fail_missing "${SVC_DIR}/radio/radios/ft891.py"


# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
  --exclude='.dev/'
)

echo "[push] Ensure runtime dirs exist"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo mkdir -p '${RT_ROOT}' '${RT_NODE}' '${RT_SVC}' '${RT_OPS}' '${RT_TOOLS}' '${RT_ROOT}/.deploy'
    sudo mkdir -p '${ENV_DST_DIR}' '${UDEV_DST_DIR}'
    sudo chown -R ${TARGET_USER}:${TARGET_USER} '${RT_ROOT}'
    sudo chmod 0755 '${ENV_DST_DIR}'
  "
else
  echo "[dry] would create runtime dirs under ${RT_ROOT}, ${ENV_DST_DIR}, ${UDEV_DST_DIR}"
fi

echo "[push] Ensure mosquitto_pub exists (mosquitto-clients)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    if ! command -v mosquitto_pub >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y mosquitto-clients
    fi
  "
else
  echo "[dry] would ensure mosquitto-clients installed"
fi

echo "[push] Ensure venv exists + deps (redis + paho-mqtt)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo mkdir -p '${RT_ROOT}'
    sudo chown -R ${TARGET_USER}:${TARGET_USER} '${RT_ROOT}'
    cd '${RT_ROOT}'
    if [ ! -x '${RT_ROOT}/.venv/bin/python' ]; then
      python3 -m venv '${RT_ROOT}/.venv'
      sudo chown -R ${TARGET_USER}:${TARGET_USER} '${RT_ROOT}/.venv'
    fi
    '${RT_ROOT}/.venv/bin/pip' install --upgrade pip >/dev/null
    '${RT_ROOT}/.venv/bin/pip' install paho-mqtt >/dev/null
    '${RT_ROOT}/.venv/bin/pip' install redis >/dev/null
  "
else
  echo "[dry] would ensure venv exists and install deps"
fi

# node.json
NODE_JSON_SRC="${REPO_ROOT}/deploy/common/node_json/${TARGET_HOST}.node.json"
if [[ "${DRY_RUN}" != "1" ]]; then
  push_node_json "${TARGET_HOST}" "${TARGET_USER}" "${NODE_JSON_SRC}" "644"
else
  echo "[dry] would ensure /etc/rollingthunder/node.json exists (or overwrite if FORCE_NODE_JSON=1)"
fi

echo "[push] Sync node subtree -> ${RT_NODE} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  --exclude='systemd/' \
  --exclude='env/' \
  --exclude='udev/' \
  "${NODE_SRC_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_NODE}/"

echo "[push] Sync common python services -> ${COMMON_SERVICES_DST_DIR} (user-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "mkdir -p '${COMMON_SERVICES_DST_DIR}'"
else
  echo "[dry] would mkdir -p ${COMMON_SERVICES_DST_DIR}"
fi
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-times \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${COMMON_SERVICES_SRC_DIR}" \
  "${TARGET_USER}@${TARGET_HOST}:${COMMON_SERVICES_DST_DIR}"

echo "[push] Sync global tools dir -> ${RT_TOOLS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${GLOBAL_TOOLS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

echo "[push] Sync node tools dir -> ${RT_TOOLS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${TOOLS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

echo "[push] Install node env files (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${ENV_SRC_DIR}/radio.env" \
    "${ENV_DST_DIR}/radio.env" "644"
else
  echo "[dry] would install env file to ${ENV_DST_DIR}/radio.env"
fi

echo "[push] Ensure scripts executable"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    chmod +x '${RT_TOOLS}/publish_deploy_report.sh' || true
    chmod +x '${RT_TOOLS}/ui_intent_worker.py' || true
  "
else
  echo "[dry] would chmod +x ${RT_TOOLS}/publish_deploy_report.sh ${RT_TOOLS}/ui_intent_worker.py"
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
  echo "[dry] would stop/disable/remove: ${LEGACY_UNITS[*]}"
fi

echo "[push] Install systemd units and udev rule (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-radio-presence.service" \
    "${UNIT_DST_DIR}/rt-radio-presence.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-radio-ui-intent-worker.service" \
    "${UNIT_DST_DIR}/rt-radio-ui-intent-worker.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.service" \
    "${UNIT_DST_DIR}/rt-radio-deploy-report-publisher.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rt-radio-deploy-report-publisher.timer" \
    "${UNIT_DST_DIR}/rt-radio-deploy-report-publisher.timer" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${SYSTEMD_DIR}/rigctld.service" \
    "${UNIT_DST_DIR}/rigctld.service" "644"

  push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
    "${UDEV_SRC_DIR}/99-rollingthunder-ft891.rules" \
    "${UDEV_DST_DIR}/99-rollingthunder-ft891.rules" "644"
else
  echo "[dry] would install units to ${UNIT_DST_DIR}: ${UNITS[*]}"
  echo "[dry] would install udev rule to ${UDEV_DST_DIR}/99-rollingthunder-ft891.rules"
fi

echo "[push] udev reload + systemd daemon-reload + enable + restart"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart ${UNITS_STR}
  "
else
  echo "[dry] would reload udev, daemon-reload + enable + restart: ${UNITS[*]}"
fi

# Record deployed commit
GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo mkdir -p '${RT_ROOT}/.deploy'
    echo '${GIT_SHA}' | sudo tee '${RT_ROOT}/.deploy/DEPLOYED_COMMIT' >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

# ---- Smoke checks ----
if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl --no-pager --full status rt-radio-presence.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-radio-ui-intent-worker.service | sed -n '1,60p' || true
    sudo systemctl --no-pager --full status rigctld.service | sed -n '1,40p' || true
    sudo systemctl --no-pager --full status rt-radio-deploy-report-publisher.timer | sed -n '1,40p' || true
    exit 0
  "

  echo "[smoke] radio package files on target (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    find '${RT_SVC}/radio' -maxdepth 3 -type f | sort || true
    exit 0
  "

  echo "[smoke] custom udev alias (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    ls -l /dev/rollingthunder/ft891-cat || true
    exit 0
  "

  echo "[smoke] stable by-id serial path (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    ls -l /dev/serial/by-id || true
    exit 0
  "

  echo "[smoke] run deploy report once now (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl start rt-radio-deploy-report-publisher.service || true
    exit 0
  "
else
  echo "[dry] skipping smoke checks"
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"