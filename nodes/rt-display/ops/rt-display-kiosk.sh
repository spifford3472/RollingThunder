#!/bin/bash
set -u

URL="${RT_KIOSK_URL:-http://rt-controller:8625/ui/index.html?runtime=1&v=controller}"
CONFIG_URL="${RT_CONFIG_URL:-http://rt-controller:8625/config/app.json}"

CHROMIUM_BIN="${RT_CHROMIUM_BIN:-chromium-browser}"
CHROME_PROFILE_DIR="${RT_CHROME_PROFILE_DIR:-/home/spiff/.config/chromium-rt-kiosk}"
LOG_DIR="/home/spiff/.local/state/rollingthunder"
LOG_FILE="${LOG_DIR}/rt-display-kiosk.log"

mkdir -p "$LOG_DIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [rt-display-kiosk] $*" | tee -a "$LOG_FILE"
}

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

log "============================================================"
log "kiosk service starting"
log "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
log "WAYLAND_DISPLAY=${WAYLAND_DISPLAY}"
log "URL=${URL}"
log "CONFIG_URL=${CONFIG_URL}"

mkdir -p "${CHROME_PROFILE_DIR}"

if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.25 -root >/dev/null 2>&1 &
  log "unclutter started"
fi

while true; do
  log "checking Wayland socket"
  if [ -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]; then
    log "Wayland socket OK: ${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}"
  else
    log "Wayland socket missing; waiting"
    sleep 2
    continue
  fi

  log "checking DNS for rt-controller"
  if getent hosts rt-controller >> "$LOG_FILE" 2>&1; then
    log "DNS OK"
  else
    log "DNS failed for rt-controller"
    sleep 2
    continue
  fi

  log "checking ping to rt-controller"
  if ping -c1 -W1 rt-controller >> "$LOG_FILE" 2>&1; then
    log "ping OK"
  else
    log "ping failed"
    sleep 2
    continue
  fi

  log "checking UI URL"
  if curl -fsS -I "$URL" >> "$LOG_FILE" 2>&1; then
    log "UI URL OK"
  else
    log "UI URL failed"
    sleep 2
    continue
  fi

  log "checking config URL"
  if curl -fsS -I "$CONFIG_URL" >> "$LOG_FILE" 2>&1; then
    log "CONFIG URL OK"
  else
    log "CONFIG URL failed"
    sleep 2
    continue
  fi

  log "all checks passed; launching Chromium"

  "${CHROMIUM_BIN}" \
    --enable-features=UseOzonePlatform \
    --ozone-platform=wayland \
    --disable-gpu \
    --disable-gpu-compositing \
    --disable-features=Vulkan,MediaRouter,DialMediaRouteProvider \
    --use-gl=swiftshader \
    --password-store=basic \
    --kiosk \
    --start-fullscreen \
    --app="${URL}" \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --disable-infobars \
    --disable-background-networking \
    --disable-component-update \
    --user-data-dir="${CHROME_PROFILE_DIR}" >> "$LOG_FILE" 2>&1

  rc=$?
  log "Chromium exited with code ${rc}; restarting checks in 5 seconds"
  sleep 5
done