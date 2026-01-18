#!/usr/bin/env bash
set -euo pipefail

# Where our UI lives:
UI_FILE="/opt/rollingthunder/display/ui/index.html"

# Default dummy endpoint (local). Swap later to rt-controller API:
POLL_MS="1000"

# Use an isolated Chromium profile so kiosk settings don't fight the desktop.
CHROME_PROFILE_DIR="/var/lib/rt-display/chromium-profile"

# Wait for X session to be ready
export DISPLAY="${DISPLAY:-:0}"

# Best-effort disable screen blanking/power management (X11)
if command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset s noblank || true
  xset -dpms || true
fi

mkdir -p "$CHROME_PROFILE_DIR"

# Build file:// URL with query params
START_URL="http://127.0.0.1:8619/index.html"

# Chromium executable name varies by distro; try common ones.
CHROMIUM_BIN=""
for c in chromium-browser chromium google-chrome; do
  if command -v "$c" >/dev/null 2>&1; then
    CHROMIUM_BIN="$(command -v "$c")"
    break
  fi
done

if [[ -z "$CHROMIUM_BIN" ]]; then
  echo "Chromium not found (expected chromium-browser/chromium)."
  exit 1
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
  --app="$START_URL"
