#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-display}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"
echo "[push] Repo:   ${REPO_ROOT}"

NODE_DIR="${REPO_ROOT}/nodes/rt-display"
OPS_DIR="${NODE_DIR}/ops"
SYSTEMD_DIR="${NODE_DIR}/systemd"
UI_DIR="${NODE_DIR}/ui"
SVC_DIR="${NODE_DIR}/services"

# repo sources
DISPLAY_PRESENCE_SRC="${SVC_DIR}/display_presence.py"
INDEX_HTML_SRC="${UI_DIR}/index.html"
HEALTH_JSON_SRC="${UI_DIR}/health.json"

KIOSK_SH_SRC="${OPS_DIR}/rt-display-kiosk.sh"
KIOSK_UNIT_SRC="${OPS_DIR}/rt-display-kiosk.service.template"
UI_UNIT_SRC="${OPS_DIR}/rt-display-ui.service.template"

PRESENCE_UNIT_SRC="${SYSTEMD_DIR}/rt-display-presence.service"

# runtime destinations (spiff-owned)
RT_ROOT="/opt/rollingthunder"
RT_NODE="${RT_ROOT}/nodes/rt-display"
RT_UI="${RT_NODE}/ui"
RT_SVC="${RT_NODE}/services"
RT_OPS="${RT_NODE}/ops"

UNIT_DST_DIR="/etc/systemd/system"

UNITS=(
  "rt-display-presence.service"
  "rt-display-ui.service"
  "rt-display-kiosk.service"
)

# --- sanity checks ---
fail_missing "${DISPLAY_PRESENCE_SRC}"
fail_missing "${INDEX_HTML_SRC}"
fail_missing "${HEALTH_JSON_SRC}"
fail_missing "${KIOSK_SH_SRC}"
fail_missing "${PRESENCE_UNIT_SRC}"
fail_missing "${KIOSK_UNIT_SRC}"
fail_missing "${UI_UNIT_SRC}"

echo "[push] Ensure runtime dirs exist (spiff-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e; mkdir -p '${RT_UI}' '${RT_SVC}' '${RT_OPS}'"

echo "[push] Ensure venv exists + deps"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  set -e
  cd /opt/rollingthunder
  if [ ! -x /opt/rollingthunder/.venv/bin/python ]; then
    python3 -m venv /opt/rollingthunder/.venv
  fi
  /opt/rollingthunder/.venv/bin/pip install --upgrade pip >/dev/null
  /opt/rollingthunder/.venv/bin/pip install paho-mqtt >/dev/null
"

echo "[push] Copy spiff-owned rt-display files"
scp "${DISPLAY_PRESENCE_SRC}" "${TARGET_USER}@${TARGET_HOST}:${RT_SVC}/display_presence.py"
scp "${INDEX_HTML_SRC}"       "${TARGET_USER}@${TARGET_HOST}:${RT_UI}/index.html"
scp "${HEALTH_JSON_SRC}"      "${TARGET_USER}@${TARGET_HOST}:${RT_UI}/health.json"
scp "${KIOSK_SH_SRC}"         "${TARGET_USER}@${TARGET_HOST}:${RT_OPS}/rt-display-kiosk.sh"

echo "[push] Ensure kiosk script is executable"
ssh "${TARGET_USER}@${TARGET_HOST}" "chmod +x '${RT_OPS}/rt-display-kiosk.sh'"

echo "[push] Install systemd units (root-owned)"
push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${PRESENCE_UNIT_SRC}" "${UNIT_DST_DIR}/rt-display-presence.service" "644"
push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${UI_UNIT_SRC}"       "${UNIT_DST_DIR}/rt-display-ui.service"       "644"
push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${KIOSK_UNIT_SRC}"    "${UNIT_DST_DIR}/rt-display-kiosk.service"    "644"

echo "[push] systemd daemon-reload + enable + restart"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  set -e
  sudo systemctl daemon-reload
  sudo systemctl enable ${UNITS[*]}
  sudo systemctl restart ${UNITS[*]}
"

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

echo "[push] Done."
