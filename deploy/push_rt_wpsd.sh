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

TOOLS_SRC_DIR="${NODE_SRC_DIR}/tools/"
TOOLS_DST_DIR="/opt/rollingthunder/tools/"

SVC_SRC_DIR="${NODE_SRC_DIR}/services/"
SVC_DST_DIR="${NODE_DST_DIR}/services/"

SYSTEMD_DIR="${NODE_SRC_DIR}/systemd"
UNIT_DST_DIR="/etc/systemd/system"

# Units present in your repo:
UNITS=(
  "rt-deploy-report-publisher.service"
  "rt-deploy-report-publisher.timer"
  "rt-presence-publisher.service"
  "rt-presence-publisher.timer"
)

# Optional legacy/special unit (only if you truly want it enabled)
OPTIONAL_UNITS=(
  "rt-wpsd-presence.service"
)

UNITS_STR="$(printf '%q ' "${UNITS[@]}")"

RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
)

fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${TOOLS_SRC_DIR}"
fail_missing_dir "${SYSTEMD_DIR}"

for u in "${UNITS[@]}"; do
  fail_missing "${SYSTEMD_DIR}/${u}"
done

# Optional unit sanity check (don’t fail if missing)
HAS_OPTIONAL=0
if [[ -f "${SYSTEMD_DIR}/rt-wpsd-presence.service" ]]; then
  HAS_OPTIONAL=1
fi

echo "[push] Ensure runtime dirs exist (and are user-owned where needed)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p /opt/rollingthunder/nodes/rt-wpsd /opt/rollingthunder/tools /opt/rollingthunder/.deploy /etc/rollingthunder
  sudo chown root:root /etc/rollingthunder
  sudo chmod 755 /etc/rollingthunder
  sudo chown -R ${TARGET_USER}:${TARGET_USER} /opt/rollingthunder/nodes/rt-wpsd /opt/rollingthunder/tools /opt/rollingthunder/.deploy
"

echo "[push] Sync tools -> ${TOOLS_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${TOOLS_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${TOOLS_DST_DIR}"

echo "[push] Sync optional python services -> ${SVC_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${SVC_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${SVC_DST_DIR}"

echo "[push] Ensure tool scripts executable"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    chmod +x '${TOOLS_DST_DIR}/publish_presence.sh' || true
    chmod +x '${TOOLS_DST_DIR}/publish_deploy_report.sh' || true
  "
else
  echo "[dry] would chmod +x publish_presence.sh publish_deploy_report.sh"
fi

echo "[push] Install systemd units (root-owned)"
if [[ "${DRY_RUN}" != "1" ]]; then
  for u in "${UNITS[@]}"; do
    push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
      "${SYSTEMD_DIR}/${u}" "${UNIT_DST_DIR}/${u}" "644"
  done

  if [[ "${HAS_OPTIONAL}" == "1" ]]; then
    push_root_file "${TARGET_HOST}" "${TARGET_USER}" \
      "${SYSTEMD_DIR}/rt-wpsd-presence.service" "${UNIT_DST_DIR}/rt-wpsd-presence.service" "644"
    # If you want it enabled, uncomment next line:
    # UNITS+=("rt-wpsd-presence.service"); UNITS_STR="$(printf '%q ' "${UNITS[@]}")"
  fi
else
  echo "[dry] would install unit files to ${UNIT_DST_DIR}"
fi

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

GIT_SHA="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD)"
if [[ "${DRY_RUN}" != "1" ]]; then
  ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
    echo ${GIT_SHA} | sudo tee /opt/rollingthunder/.deploy/DEPLOYED_COMMIT >/dev/null
  "
else
  echo "[dry] would record deployed commit ${GIT_SHA}"
fi

if [[ "${DRY_RUN}" != "1" ]]; then
  echo "[smoke] timer status (non-fatal)"
  ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
    sudo systemctl --no-pager --full status rt-presence-publisher.timer | sed -n '1,25p' || true
    sudo systemctl --no-pager --full status rt-deploy-report-publisher.timer | sed -n '1,25p' || true
    exit 0
  "
fi

echo "[push] Done. Deployed commit ${GIT_SHA}"
