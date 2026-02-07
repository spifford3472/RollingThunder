#!/usr/bin/env bash
set -euo pipefail

# RollingThunder rt-display kiosk launcher (Option A: controller serves UI + API)
#
# This script launches Chromium in kiosk mode and points it at the controller-hosted UI.
# The UI and APIs share the same origin (no CORS/proxy), so the display stays dumb and stable.

# -----------------------------
# Config (env-overridable)
# -----------------------------
POLL_MS="${RT_UI_POLL_MS:-1000}"

CTRL_HOST="${RT_CONTROLLER_HOST:-rt-controller}"
CTRL_PORT="${RT_CONTROLLER_PORT:-8625}"

# Controller-served UI root (must exist on controller after Step A)
UI_PATH="${RT_UI_PATH:-/ui/index.html}"

# UI runtime params (your UI may ignore some; harmless to pass)
PAGE="${RT_UI_PAGE:-home}"
RUNTIME="${RT_UI_RUNTIME:-1}"

# Cache-buster: use deployed commit if present (prevents stale JS after deploy)
V="0"
if [[ -r /opt/rollingthunder/.deploy/DEPLOYED_COMMIT ]]; then
  V="$(tr -d ' \n\r\t' </opt/rollingthunder/.deploy/DEPLOYED_COMMIT | head -c 16)"
fi

START_URL="http://${CTRL_HOST}:${CTRL_PORT}${UI_PATH}?runtime=${RUNTIME}&page=${PAGE}&ms=${POLL_MS}&v=${V}"

# Chromium profile (isolate kiosk from desktop profile)
CHROME_PROFILE_DIR="${RT_CHROME_PROFILE_DIR:-/var/lib/rt-display/chromium-profile}"

# Wait for X session to be ready
export DISPLAY="${DISPLAY:-:0}"

# Best-effort disable screen blanking/power management (X11)
if command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset s noblank || true
  xset -dpms || true
fi

mkdir -p "$CHROME_PROFILE_DIR"

# Chromium executable name varies by distro; try common ones.
CHROMIUM_BIN=""
for c in chromium chromium-browser google-chrome; do
  if command -v "$c" >/dev/null 2>&1; then
    CHROMIUM_BIN="$(command -v "$c")"
    break
  fi
done

# Debian/Bookworm often installs Chromium at /usr/lib/chromium/chromium
if [[ -z "$CHROMIUM_BIN" && -x /usr/lib/chromium/chromium ]]; then
  CHROMIUM_BIN="/usr/lib/chromium/chromium"
fi

if [[ -z "$CHROMIUM_BIN" ]]; then
  echo "Chromium not found (expected chromium/chromium-browser/google-chrome or /usr/lib/chromium/chromium)."
  exit 1
fi

echo "[kiosk] DISPLAY=${DISPLAY}"
echo "[kiosk] START_URL=${START_URL}"
echo "[kiosk] CHROMIUM_BIN=${CHROMIUM_BIN}"
echo "[kiosk] PROFILE=${CHROME_PROFILE_DIR}"

exec "$CHROMIUM_BIN" \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=TranslateUI \
  --check-for-update-interval=31536000 \
  --user-data-dir="$CHROME_PROFILE_DIR" \
  --autoplay-policy=no-user-gesture-required \
  --disable-features=CloudMessaging,PushMessaging \
  --disable-notifications \
  --disable-pings \
  --media-router=0 \
  --disable-dev-shm-usage \
  --enable-gpu-rasterization \
  --use-angle=gles \
  --force-renderer-accessibility \
  --app="$START_URL"
