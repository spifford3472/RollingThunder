#!/usr/bin/env bash


set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/common/lib.sh
source "${REPO_ROOT}/deploy/common/lib.sh"

${REPO_ROOT}/tools/rt_bump_build.sh

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
ALERT_UNIT_SRC="${REPO_ROOT}/nodes/rt-controller/systemd/rt-alert@.service"
ALERT_RECONCILE_SRC="${REPO_ROOT}/nodes/rt-controller/systemd/rt-alerts-reconciler.service"

# --- HF Map Variables ---
HF_MAPS_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/data/MAPS/"
HF_MAPS_DST_DIR="/opt/rollingthunder/nodes/rt-controller/data/MAPS/"

# --- Source roots (authoritative) ---
NODE_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/"
SERVICES_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/services/"
OPS_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/ops/"
COMMON_SERVICES_SRC_DIR="${REPO_ROOT}/nodes/common/services/"
RT_TOOLS="/opt/rollingthunder/tools"
POTA_PARK_DATA_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/data/POTA/"
HF_FLAGS_SRC_DIR="${REPO_ROOT}/nodes/rt-controller/data/FLAGS/"


# Thin-client UI/runtime sources (served by rt-controller)
UI_SRC_DIR="${REPO_ROOT}/nodes/rt-display/ui/"
CFG_SRC_DIR="${REPO_ROOT}/config/"

# --- Dest roots ---
NODE_DST_DIR="/opt/rollingthunder/nodes/rt-controller/"
SERVICES_DST_DIR="/opt/rollingthunder/services/"
STATE_ENV_SRC="${OPS_SRC_DIR}/service_state_publisher.env.template"
STATE_ENV_DST="/etc/rollingthunder/service_state_publisher.env"
COMMON_SERVICES_DST_DIR="/opt/rollingthunder/nodes/common/services/"
GLOBAL_TOOLS_DIR="${REPO_ROOT}/tools"
POTA_PARK_DATA_DST_DIR="/opt/rollingthunder/data/POTA/"
HF_FLAGS_DST_DIR="/opt/rollingthunder/nodes/rt-controller/data/FLAGS/"

# Thin-client UI/runtime destinations (served by rt-controller)
UI_DST_DIR="/opt/rollingthunder/ui/"
CFG_DST_DIR="/opt/rollingthunder/config/"

UNITS=(
  "rt-ui-snapshot-api.service"
  "rt-service-state-publisher.service"
  "rt-node-presence-ingestor.service"
  # Deploy Report Publisher (controller -> Redis)
  "rt-deploy-report-controller.service"
  "rt-deploy-report-controller.timer"
  "rt-gps-state-publisher.service"
  "rt-env-temp-publisher.service"
  "rt-alerts-reconciler.service"
  "rt-controller-presence.service"
  "rt-ui-intent-worker.service"
  "rt-alert@.service"
  "rt-pota-context-manager.service"
  "rt-pota-spots-poller.service"
  "rt-pota-nearby-parks.service"
  "rt-adif-logger.service"
  "rt-ui-state-projector.service"
  "rt-ui-interaction-state.service"
  "rt-console-led-sender.service"
  "rt-panel-input-bridge.service"
  "rt-virtual-panel-server.service"
  "rt-weather-publisher.service"
  "rt-hf-dx-spider-poller.service"
  "rt-rfintel-swpc-collector.service"
  "rt-rfintel-band-advisor.service"
  "rt-rfintel-mobile-advisor.service"
  "rt-rfintel-advisor-engine.service"
  "rt-rfintel-advisor-alert-mirror.service"
  "rt-rfintel-trend-recorder.service"
  "rt-rfintel-dx-activity.service"
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
    /opt/rollingthunder/data/POTA \
    /opt/rollingthunder/config &&
  sudo chown root:root /opt/rollingthunder/services /etc/rollingthunder /opt/rollingthunder/ui /opt/rollingthunder/config &&
  sudo chmod 755 /opt/rollingthunder/services /etc/rollingthunder /opt/rollingthunder/ui /opt/rollingthunder/config  
"


echo "[push] Ensure common services dir exists (user-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p '${COMMON_SERVICES_DST_DIR}';
  sudo chown -R '${TARGET_USER}:${TARGET_USER}' /opt/rollingthunder/nodes;
  sudo chmod -R 755 /opt/rollingthunder/nodes
"

echo "[push] Ensure POTA data dir exists (user-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p '${POTA_PARK_DATA_DST_DIR}';
  sudo chown -R '${TARGET_USER}:${TARGET_USER}' /opt/rollingthunder/data/POTA;
  sudo chmod -R 755 /opt/rollingthunder/data/POTA
"

echo "[push] Ensure HF flags data dir exists (user-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p '${HF_FLAGS_DST_DIR}';
  sudo chown -R '${TARGET_USER}:${TARGET_USER}' /opt/rollingthunder/nodes/rt-controller/data;
  sudo chmod -R 755 /opt/rollingthunder/nodes/rt-controller/data
"

echo "[push] Ensure HF maps data dir exists (user-owned)"
ssh "${TARGET_USER}@${TARGET_HOST}" "set -e;
  sudo mkdir -p '${HF_MAPS_DST_DIR}';
  sudo chown -R '${TARGET_USER}:${TARGET_USER}' /opt/rollingthunder/nodes/rt-controller/data;
  sudo chmod -R 755 /opt/rollingthunder/nodes/rt-controller/data
"

# Common rsync excludes
RSYNC_EXCLUDES=(
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.venv/'
  --exclude='.git/'
  --exclude='.dev/'
)


echo "[push] Sync common python services -> ${COMMON_SERVICES_DST_DIR} (user-owned)"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  --no-group --no-perms --omit-dir-times --no-times \
  "${RSYNC_EXCLUDES[@]}" \
  "${COMMON_SERVICES_SRC_DIR}" \
  "${TARGET_USER}@${TARGET_HOST}:${COMMON_SERVICES_DST_DIR}"


# Debug / validation
echo "[debug] NODE_SRC_DIR=${NODE_SRC_DIR}"
echo "[debug] SERVICES_SRC_DIR=${SERVICES_SRC_DIR}"
echo "[debug] OPS_SRC_DIR=${OPS_SRC_DIR}"
echo "[debug] UI_SRC_DIR=${UI_SRC_DIR}"
echo "[debug] CFG_SRC_DIR=${CFG_SRC_DIR}"
echo "[debug] POTA_PARK_DATA_SRC_DIR=${POTA_PARK_DATA_SRC_DIR}"
ls -la "${NODE_SRC_DIR}" || true

fail_missing_dir "${NODE_SRC_DIR}"
fail_missing_dir "${SERVICES_SRC_DIR}"
fail_missing_dir "${COMMON_SERVICES_SRC_DIR}"
fail_missing_dir "${POTA_PARK_DATA_SRC_DIR}"
fail_missing_dir "${HF_FLAGS_SRC_DIR}"
fail_missing_dir "${HF_MAPS_SRC_DIR}"
fail_missing "${HF_MAPS_SRC_DIR}/world.svg"
fail_missing "${HF_FLAGS_SRC_DIR}/4x3/us.svg"
fail_missing "${HF_FLAGS_SRC_DIR}/4x3/ie.svg"
fail_missing "${COMMON_SERVICES_SRC_DIR}/node_presence_publisher.py"
fail_missing "${STATE_ENV_SRC}"
fail_missing "${GPS_UNIT_SRC}"
fail_missing "${ALERT_UNIT_SRC}"
fail_missing "${ALERT_RECONCILE_SRC}"


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

echo "[push] Sync POTA park data files dir ${POTA_PARK_DATA_SRC_DIR} -> ${POTA_PARK_DATA_DST_DIR}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${POTA_PARK_DATA_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${POTA_PARK_DATA_DST_DIR}"

echo "[push] Sync HF flag data files dir ${HF_FLAGS_SRC_DIR} -> ${HF_FLAGS_DST_DIR}"
rsync -avz --checksum --itemize-changes --delete "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${HF_FLAGS_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${HF_FLAGS_DST_DIR}"

echo "[push] Sync HF map data files dir ${HF_MAPS_SRC_DIR} -> ${HF_MAPS_DST_DIR}"
rsync -avz --checksum --itemize-changes --delete "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${HF_MAPS_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${HF_MAPS_DST_DIR}"
  
echo "[push] Sync global tools dir -> ${RT_TOOLS}"
rsync -avz --checksum --itemize-changes "${RSYNC_DRY[@]}" \
  "${RSYNC_EXCLUDES[@]}" \
  "${GLOBAL_TOOLS_DIR}/" "${TARGET_USER}@${TARGET_HOST}:${RT_TOOLS}/"

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

  # Inject common services into the controller services bundle (single flat dir)
  rsync -avz --checksum --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    "${COMMON_SERVICES_SRC_DIR}/node_presence_publisher.py" \
    "${TARGET_USER}@${TARGET_HOST}:${TMP_REMOTE}/node_presence_publisher.py"


  rsync -avz --checksum --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    "${SERVICES_SRC_DIR}" "${TARGET_USER}@${TARGET_HOST}:${TMP_REMOTE}/"

  # Include common presence publisher in controller /opt/rollingthunder/services/

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
    elif [[ "${u}" == "rt-alert@.service" ]]; then
      push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${ALERT_UNIT_SRC}" "/etc/systemd/system/${u}" "644"
    elif [[ "${u}" == "rt-alerts-reconciler.service" ]]; then
      push_root_file "${TARGET_HOST}" "${TARGET_USER}" "${ALERT_RECONCILE_SRC}" "/etc/systemd/system/${u}" "644"
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

    echo "[push] disable/remove obsolete legacy units if present"
    sudo systemctl stop rollingthunder-controller.service rollingthunder-api.service rt-wpsd-log-ingestor.service 2>/dev/null || true
    sudo systemctl disable rollingthunder-controller.service rollingthunder-api.service rt-wpsd-log-ingestor.service 2>/dev/null || true
    sudo rm -f \
      /etc/systemd/system/rollingthunder-controller.service \
      /etc/systemd/system/rollingthunder-api.service \
      /etc/systemd/system/rt-wpsd-log-ingestor.service
    sudo systemctl daemon-reload
    sudo systemctl reset-failed

    # Split template units (like rt-alert@.service) from normal units.
    NORMAL_UNITS=()
    TEMPLATE_UNITS=()

    for u in ${UNITS_STR}; do
      # UNITS_STR is already shell-escaped by printf %q, so this loop is safe.
      if [[ \"\$u\" == *@.service ]]; then
        TEMPLATE_UNITS+=(\"\$u\")
      else
        NORMAL_UNITS+=(\"\$u\")
      fi
    done

    if (( \${#TEMPLATE_UNITS[@]} )); then
      echo \"[push] template units installed (not enabling/restarting): \${TEMPLATE_UNITS[*]}\"
    fi

    if (( \${#NORMAL_UNITS[@]} )); then
      sudo systemctl enable \${NORMAL_UNITS[*]}
      sudo systemctl restart \${NORMAL_UNITS[*]}
    fi
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
ssh "${TARGET_USER}@${TARGET_HOST}" "set +e
  sudo rm -f /opt/rollingthunder/ui/dev/nodes_health.json
  exit 0
"


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

# ---- SMOKE CHECKS (critical only) ----
if [[ "${DRY_RUN}" != "1" ]]; then
  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "curl" "install with: sudo apt-get update && sudo apt-get install -y curl"
  require_remote_cmd_or_warn "${TARGET_HOST}" "${TARGET_USER}" "redis-cli" "install with: sudo apt-get update && sudo apt-get install -y redis-tools"

  echo "[smoke] no obsolete legacy units active"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    set -e
    for u in rollingthunder-controller.service rollingthunder-api.service rt-wpsd-log-ingestor.service; do
      state="$(systemctl is-active "$u" 2>/dev/null || true)"
      if [ "$state" = "active" ] || [ "$state" = "activating" ]; then
        echo "[error] obsolete unit still active: $u ($state)"
        exit 1
      fi
    done
    echo "obsolete-units=inactive"
  '

  echo "[smoke] critical services active"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    set -e
    for u in \
      rt-ui-snapshot-api.service \
      rt-ui-state-projector.service \
      rt-service-state-publisher.service \
      rt-node-presence-ingestor.service \
      rt-ui-intent-worker.service \
      rt-ui-interaction-state.service \
      rt-controller-presence.service
    do
      printf "%s: " "$u"
      systemctl is-active "$u"
    done
  '

  echo "[smoke] no failed systemd units"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    set -e
    failed="$(systemctl --failed --no-legend --plain | wc -l)"
    if [ "$failed" -ne 0 ]; then
      systemctl --failed --no-pager --plain
      exit 1
    fi
    echo "failed-units=0"
  '

  echo "[smoke] app.json parses"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    python3 - <<'"'"'PY'"'"'
import json
with open("/opt/rollingthunder/config/app.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print("app.json=ok")
print("runtimeVersion=", data.get("runtimeVersion"))
PY
  '

  echo "[smoke] UI API responds"
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/api/v1/ui/nodes" 5 1.5
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/ui/index.html" 5 1.5
  curl_smoke_retry "${TARGET_HOST}" "${TARGET_USER}" "http://127.0.0.1:8625/config/app.json" 5 1.5

  echo "[smoke] HF flag assets deployed"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    set -e
    test -s /opt/rollingthunder/nodes/rt-controller/data/FLAGS/4x3/us.svg
    test -s /opt/rollingthunder/nodes/rt-controller/data/FLAGS/4x3/ie.svg
    echo "hf-flags=ok"
  '

  echo "[smoke] Redis auth and controller presence"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    set -e
    set -a
    [ -f /etc/rollingthunder/redis.env ] && . /etc/rollingthunder/redis.env
    set +a

    REDISCLI_AUTH="${RT_REDIS_PASSWORD:-}" redis-cli ping | grep -q PONG

    status="$(REDISCLI_AUTH="${RT_REDIS_PASSWORD:-}" redis-cli HGET rt:nodes:rt-controller status || true)"
    test -n "$status" || { echo "[error] missing rt:nodes:rt-controller status"; exit 1; }

    model="$(REDISCLI_AUTH="${RT_REDIS_PASSWORD:-}" redis-cli EXISTS rt:ui:model:node_health_summary || true)"
    test "$model" = "1" || { echo "[error] missing rt:ui:model:node_health_summary"; exit 1; }

    echo "redis=ok controller_status=$status"
  '

  echo "[smoke] deployed commit marker"
  ssh "${TARGET_USER}@${TARGET_HOST}" '
    test -s /opt/rollingthunder/.deploy/DEPLOYED_COMMIT &&
    echo "deployed_commit=$(cat /opt/rollingthunder/.deploy/DEPLOYED_COMMIT)"
  '
else
  echo "[dry] skipping smoke checks"
fi