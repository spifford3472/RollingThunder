#!/usr/bin/env bash
# rt-display-kiosk-watchdog.sh
# Monitors Chromium memory/CPU and restarts kiosk if thresholds exceeded.
# Runs as a separate systemd service alongside rt-display-kiosk.service

set -euo pipefail

# --- Thresholds ---
MEM_RESTART_MB="${RT_WATCHDOG_MEM_MB:-600}"        # restart if RSS exceeds this
SWAP_RESTART_MB="${RT_WATCHDOG_SWAP_MB:-200}"       # restart if swap used exceeds this
CPU_RESTART_PCT="${RT_WATCHDOG_CPU_PCT:-90}"        # restart if sustained CPU exceeds this
CPU_SUSTAINED_SEC="${RT_WATCHDOG_CPU_SEC:-60}"      # how long CPU must be high before restart
CHECK_INTERVAL="${RT_WATCHDOG_INTERVAL:-30}"        # seconds between checks

# --- State ---
cpu_high_since=0
consecutive_high=0

log() {
    echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

get_chromium_pids() {
    pgrep -x chromium 2>/dev/null || pgrep -x chromium-browser 2>/dev/null || true
}

get_total_rss_mb() {
    local pids=("$@")
    local total=0
    for pid in "${pids[@]}"; do
        local rss
        rss=$(awk '/^VmRSS:/{print $2}' "/proc/${pid}/status" 2>/dev/null || echo 0)
        total=$((total + rss))
    done
    echo $((total / 1024))
}

get_swap_used_mb() {
    free -m | awk '/^Swap:/{print $3}'
}

get_cpu_pct() {
    local pids=("$@")
    local total=0
    for pid in "${pids[@]}"; do
        local pct
        pct=$(ps -p "$pid" -o %cpu= 2>/dev/null | awk '{sum+=$1} END {print int(sum)}' || echo 0)
        total=$((total + pct))
    done
    echo "$total"
}

do_restart() {
    local reason="$1"
    log "RESTARTING kiosk: ${reason}"
    # Clear caches before restart to reclaim memory faster
    rm -rf /var/lib/rt-display/chromium-profile/Default/Cache 2>/dev/null || true
    rm -rf /var/lib/rt-display/chromium-profile/Default/Code\ Cache 2>/dev/null || true
    rm -rf /var/lib/rt-display/chromium-profile/Default/GPUCache 2>/dev/null || true
    systemctl --user restart rt-display-kiosk.service || log "WARN: kiosk restart command failed"
    log "Restart issued. Sleeping 30s for Chromium to settle."
    sleep 30
    # Reset counters
    cpu_high_since=0
    consecutive_high=0
}

log "Watchdog started. mem_limit=${MEM_RESTART_MB}MB swap_limit=${SWAP_RESTART_MB}MB cpu_limit=${CPU_RESTART_PCT}% sustained=${CPU_SUSTAINED_SEC}s interval=${CHECK_INTERVAL}s"

while true; do
    sleep "${CHECK_INTERVAL}"

    mapfile -t pids < <(get_chromium_pids)

    if [[ ${#pids[@]} -eq 0 ]]; then
        log "Chromium not running — kiosk may be starting up, skipping check"
        cpu_high_since=0
        consecutive_high=0
        continue
    fi

    rss_mb=$(get_total_rss_mb "${pids[@]}")
    swap_mb=$(get_swap_used_mb)
    cpu_pct=$(get_cpu_pct "${pids[@]}")
    now=$(date +%s)

    log "pids=${#pids[@]} rss=${rss_mb}MB swap=${swap_mb}MB cpu=${cpu_pct}%"

    # Memory threshold
    if [[ "${rss_mb}" -gt "${MEM_RESTART_MB}" ]]; then
        do_restart "RSS ${rss_mb}MB exceeds limit ${MEM_RESTART_MB}MB"
        continue
    fi

    # Swap threshold
    if [[ "${swap_mb}" -gt "${SWAP_RESTART_MB}" ]]; then
        do_restart "Swap ${swap_mb}MB exceeds limit ${SWAP_RESTART_MB}MB"
        continue
    fi

    # Sustained CPU threshold
    if [[ "${cpu_pct}" -gt "${CPU_RESTART_PCT}" ]]; then
        if [[ "${cpu_high_since}" -eq 0 ]]; then
            cpu_high_since="${now}"
            log "CPU high (${cpu_pct}%), starting sustained timer"
        else
            elapsed=$(( now - cpu_high_since ))
            log "CPU sustained high for ${elapsed}s / ${CPU_SUSTAINED_SEC}s threshold"
            if [[ "${elapsed}" -ge "${CPU_SUSTAINED_SEC}" ]]; then
                do_restart "CPU ${cpu_pct}% sustained for ${elapsed}s"
            fi
        fi
    else
        # CPU back to normal - reset timer
        if [[ "${cpu_high_since}" -ne 0 ]]; then
            log "CPU normalised (${cpu_pct}%), resetting sustained timer"
        fi
        cpu_high_since=0
    fi

done