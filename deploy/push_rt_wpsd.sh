#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-ki5vnb-dmr2}"
TARGET_USER="${RT_SSH_USER:-pi-star}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

DRY_RUN="${DRY_RUN:-0}"
RSYNC_DRY=()
if [[ "${DRY_RUN}" == "1" ]]; then
  RSYNC_DRY+=(--dry-run)
  echo "[push] DRY RUN enabled: rsync will be --dry-run and NO root/systemd actions will run"
fi

NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-wpsd/"
NODE_DST_DIR="/opt/rollingthunder/nodes/rt-wpsd/"

UNIT_SRC="${REPO_ROOT}/nodes/rt-wpsd/systemd/rt-wpsd-presence.service"
UNIT_DST="/etc/systemd/system/rt-wpsd-presence.service"

UNITS=("rt-wpsd-presence.service")
UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

fail_missing_dir "${NODE_SRC_DIR}"
fail_missing "${UNIT_SRC}"

echo "[push] Ensure runtime dirs exist"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p /opt/rollingthunder/nodes/rt-wpsd /etc/rollingthunder &&
  sudo chown root:root /etc/rollingthunder &&
  sudo chmod 755 /etc/rollingthunder
"

echo "[push] Sync node subtree -> ${NODE_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
--no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${NODE_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${NODE_DST_DIR}"

if [[ "${DRY_RUN}" != "1" ]]; then
  push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${UNIT_SRC}" "${UNIT_DST}" "644"

  echo "[push] systemd daemon-reload + enable + restart"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e
    sudo systemctl daemon-reload
    sudo systemctl enable ${UNITS_STR}
    sudo systemctl restart ${UNITS_STR}
  "

  GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    sudo mkdir -p /opt/rollingthunder/.deploy
    echo ${GIT_SHA} | sudo tee /opt/rollingthunder/.deploy/DEPLOYED_COMMIT >/dev/null
  "
else
  echo "[dry] would push ${UNIT_DST} and restart ${UNITS[*]}"
  echo "[dry] would record deployed commit"
fi

echo "[push] Done."
