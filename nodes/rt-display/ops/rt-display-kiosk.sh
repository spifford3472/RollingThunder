#!/bin/bash
set -euo pipefail

URL='http://rt-controller:8625/ui/index.html?runtime=1&v=controller'
CHROMIUM_BIN="${RT_CHROMIUM_BIN:-chromium-browser}"
CHROME_PROFILE_DIR="${RT_CHROME_PROFILE_DIR:-/home/spiff/.config/chromium-rt-kiosk}"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

mkdir -p "${CHROME_PROFILE_DIR}"

# Hide mouse cursor if available
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.25 -root >/dev/null 2>&1 &
fi

# Wait briefly for controller UI
for _ in $(seq 1 120); do
  if curl -fsS "${URL}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

exec "${CHROMIUM_BIN}" \
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
  --user-data-dir="${CHROME_PROFILE_DIR}"