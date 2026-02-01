const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

function classifyTopbar(data) {
  return {
    systemHealth: "ok" | "warn" | "bad",
    timeSource: "gps" | "system",
    gpsFix: true | false,
    tempF: number | null,
    tempC: number | null,
  };
}

function classifyTopbar(data) {
  const sys = data?.sys_health || null;

  const ok = !!sys?.ok;
  const stale = !!sys?.stale;

  let systemHealth = "warn";
  if (ok && !stale) systemHealth = "ok";
  else if (!ok) systemHealth = "bad";

  const gpsTime = data?.clock;     // from binding id "clock"
  const gpsFix = data?.gps_fix;    // from binding id "gps_fix"

  const timeSource = (gpsTime != null) ? "gps" : "system";
  const hasFix = (gpsFix === true);

  const tempF = data?.temp_f ?? null;
  const tempC = data?.temp_c ?? null;

  return { systemHealth, timeSource, hasFix, tempF, tempC };
}

export function renderTopbarCore(container, panel, data) {
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  const cls = classifyTopbar(data);

  const healthIcon =
    cls.systemHealth === "ok" ? "✅" :
    cls.systemHealth === "bad" ? "❌" : "⚠️";

  const timeIcon = cls.timeSource === "gps" ? "🛰️" : "⏱️";
  const fixIcon = cls.hasFix ? "📍" : "✖";

  // UTC time
  const now = new Date();
  const utcTime = now.toISOString().slice(11, 19); // HH:MM:SS
  const utcDate = now.toISOString().slice(0, 10);  // YYYY-MM-DD

  const tempText =
    (cls.tempF != null && cls.tempC != null)
      ? `${cls.tempF}°F / ${cls.tempC}°C`
      : `--°F / --°C`;

  container.innerHTML = `
    <div class="rt-topbar" style="display:flex; justify-content:space-between; align-items:center; gap:16px;">
      <div class="rt-topbar-left">
        <div class="rt-topbar-brand">RollingThunder</div>
        <div class="rt-topbar-page" style="opacity:0.8; font-size:12px;">${esc(page)}</div>
      </div>

      <div class="rt-topbar-mid" style="text-align:center;">
        <div style="font-size:20px; font-variant-numeric: tabular-nums;">${esc(utcTime)} UTC</div>
        <div style="font-size:12px; opacity:0.8;">${esc(utcDate)}</div>
      </div>

      <div class="rt-topbar-right" style="text-align:right;">
        <div style="display:flex; gap:10px; justify-content:flex-end; font-size:18px;">
          <span title="System Health">${healthIcon}</span>
          <span title="Time Source">${timeIcon}</span>
          <span title="GPS Fix">${fixIcon}</span>
        </div>
        <div style="font-size:12px; opacity:0.85; margin-top:2px;">${esc(tempText)}</div>
      </div>
    </div>
  `;
}

