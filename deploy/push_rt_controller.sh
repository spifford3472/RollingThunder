#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${1:-rt-controller}"
TARGET_USER="${RT_SSH_USER:-spiff}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- files (repo -> target) ----

# root-owned service scripts
UI_SNAPSHOT_SRC="${REPO_ROOT}/nodes/rt-controller/services/ui_snapshot_api.py"
UI_SNAPSHOT_DST="/opt/rollingthunder/services/ui_snapshot_api.py"

# spiff-owned node code
PRES_INGEST_SRC="${REPO_ROOT}/nodes/rt-controller/services/node_presence_ingestor.py"
PRES_INGEST_DST="/opt/rollingthunder/nodes/rt-controller/node_presence_ingestor.py"

# root-owned systemd unit(s)
PRES_UNIT_SRC="${REPO_ROOT}/nodes/rt-controller/systemd/rt-node-presence-ingestor.service"
PRES_UNIT_DST="/etc/systemd/system/rt-node-presence-ingestor.service"

echo "[push] Target: ${TARGET_USER}@${TARGET_HOST}"

# --- copy spiff-owned node code directly ---
echo "[push] node_presence_ingestor.py -> ${PRES_INGEST_DST}"
scp "${PRES_INGEST_SRC}" "${TARGET_USER}@${TARGET_HOST}:${PRES_INGEST_DST}"

# --- copy root-owned files via /tmp then sudo mv ---
echo "[push] ui_snapshot_api.py (root-owned) -> ${UI_SNAPSHOT_DST}"
scp "${UI_SNAPSHOT_SRC}" "${TARGET_USER}@${TARGET_HOST}:/tmp/ui_snapshot_api.py"
ssh "${TARGET_USER}@${TARGET_HOST}" "sudo mv /tmp/ui_snapshot_api.py '${UI_SNAPSHOT_DST}' && sudo chown root:root '${UI_SNAPSHOT_DST}' && sudo chmod 755 '${UI_SNAPSHOT_DST}'"

echo "[push] rt-node-presence-ingestor.service -> ${PRES_UNIT_DST}"
scp "${PRES_UNIT_SRC}" "${TARGET_USER}@${TARGET_HOST}:/tmp/rt-node-presence-ingestor.service"
ssh "${TARGET_USER}@${TARGET_HOST}" "sudo mv /tmp/rt-node-presence-ingestor.service '${PRES_UNIT_DST}' && sudo chown root:root '${PRES_UNIT_DST}' && sudo chmod 644 '${PRES_UNIT_DST}'"

# --- systemd reload + restart relevant services ---
echo "[push] systemd daemon-reload + restart services"
ssh "${TARGET_USER}@${TARGET_HOST}" "
  sudo systemctl daemon-reload &&
  sudo systemctl restart rt-ui-snapshot-api.service &&
  sudo systemctl restart rt-node-presence-ingestor.service
"

# --- quick smoke checks (non-fatal: prints results) ---
echo "[smoke] rt-ui-snapshot-api /api/v1/ui/nodes"
ssh "${TARGET_USER}@${TARGET_HOST}" "curl -s http://127.0.0.1:8625/api/v1/ui/nodes | head -c 400; echo"

echo "[smoke] Redis rt:nodes:rt-display"
ssh "${TARGET_USER}@${TARGET_HOST}" "redis-cli HGETALL rt:nodes:rt-display || true"

echo "[push] Done."
