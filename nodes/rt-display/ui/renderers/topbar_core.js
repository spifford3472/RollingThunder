const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

export function renderTopbarCore(container, panel, data) {
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  // data is expected to be an object keyed by binding id (clock, gps_fix, etc)
  const gpsFix = data?.gps_fix ?? null;
  const gpsSpeed = data?.gps_speed ?? null;
  const activeAlerts = data?.active_alerts ?? null;

  // Render-friendly strings (no inference, just formatting)
  const gpsFixStr = gpsFix === null ? "-" : esc(JSON.stringify(gpsFix));
  const gpsSpeedStr = gpsSpeed === null ? "-" : esc(JSON.stringify(gpsSpeed));
  const alertsStr = activeAlerts === null ? "-" : esc(JSON.stringify(activeAlerts));

  const now = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dateStr = now.toLocaleDateString([], { weekday: "short", month: "short", day: "2-digit" });

  const sysHealth = data?.sys_health ?? null;
  const sysStr = sysHealth === null ? "state:null" : "state:ok";


  container.innerHTML = `
    <div class="rt-topbar">
      <div class="rt-topbar-left">
        <div class="rt-topbar-brand">RollingThunder</div>
        <div class="rt-topbar-page">${esc(page)}</div>
      </div>

      <div class="rt-topbar-right">
        <div class="rt-topbar-dt">
          <span class="rt-topbar-date">${esc(dateStr)}</span>
          <span class="rt-topbar-time">${esc(timeStr)}</span>
        </div>
        <div class="rt-topbar-meta" style="margin-left:12px; opacity:0.9; font-size:12px;">
          <span>fix:${gpsFixStr}</span>
          <span style="margin-left:8px;">spd:${gpsSpeedStr}</span>
          <span style="margin-left:8px;">alerts:${alertsStr}</span>
        </div>
      </div>
    </div>
  `;
}
