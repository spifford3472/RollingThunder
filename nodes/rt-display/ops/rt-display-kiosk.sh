#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"

POLL_MS="${RT_UI_POLL_MS:-500}"
CTRL_HOST="${RT_CTRL_HOST:-rt-controller}"
CTRL_PORT="${RT_CTRL_PORT:-8625}"

# Primary: controller serves UI + API
#START_URL_DEFAULT="http://${CTRL_HOST}:${CTRL_PORT}/ui/index.html?runtime=1&page=home"
# Optional fallback: local display UI server (what you had before)
#FALLBACK_URL="${RT_UI_FALLBACK_URL:-http://127.0.0.1:8619/index.html?runtime=1&page=home}"

# Primary: controller serves UI + API
START_URL_DEFAULT="http://${CTRL_HOST}:${CTRL_PORT}/ui/index.html?runtime=1&v=controller"
FALLBACK_URL="${RT_UI_FALLBACK_URL:-http://192.168.8.134:8625/ui/index.html?runtime=1&v=controller}"

START_URL="${RT_UI_START_URL:-$START_URL_DEFAULT}"

CHROME_PROFILE_DIR="/var/lib/rt-display/chromium-profile"

# Best-effort disable screen blanking/power management (X11)
if command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset s noblank || true
  xset -dpms || true
fi

mkdir -p "$CHROME_PROFILE_DIR"

# Find chromium
CHROMIUM_BIN=""
for c in chromium chromium-browser google-chrome; do
  if command -v "$c" >/dev/null 2>&1; then
    CHROMIUM_BIN="$(command -v "$c")"
    break
  fi
done
if [[ -z "$CHROMIUM_BIN" ]]; then
  echo "Chromium not found."
  exit 1
fi

# Wait briefly for controller UI; fall back if unreachable
echo "[kiosk] checking controller UI: $START_URL_DEFAULT"
if command -v curl >/dev/null 2>&1; then
  for _ in {1..20}; do
    if curl -fsS --max-time 1 "${START_URL_DEFAULT}" >/dev/null 2>&1; then
      START_URL="${START_URL_DEFAULT}"
      break
    fi
    sleep 0.5
  done
fi

if [[ "${START_URL}" != "${START_URL_DEFAULT}" ]]; then
  echo "[kiosk] controller UI not reachable; using fallback: $FALLBACK_URL"
  START_URL="$FALLBACK_URL"
else
  echo "[kiosk] using controller UI: $START_URL"
fi

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
  --app="$START_URL"
