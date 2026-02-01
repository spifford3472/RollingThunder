const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

function iconTri(state /* "ok" | "bad" | "unk" */) {
  if (state === "ok") return { glyph: "✅", label: "OK" };
  if (state === "bad") return { glyph: "✖", label: "BAD" };
  return { glyph: "●", label: "UNK" };
}

function boolish(v) {
  // Accept true/false, 1/0, "true"/"false"
  if (v === true || v === 1) return true;
  if (v === false || v === 0) return false;
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    if (s === "true" || s === "1" || s === "yes" || s === "ok") return true;
    if (s === "false" || s === "0" || s === "no") return false;
  }
  return null;
}

function getPageName() {
  const params = new URLSearchParams(location.search);
  return params.get("page") || "home";
}

export function renderTopbarCore(container, panel, data) {
  // --- Left section ---
  const page = getPageName();

  // --- Middle section: always show UTC ---
  const now = new Date();
  const utcTime = now.toLocaleTimeString("en-GB", {
    timeZone: "UTC",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const utcDate = now.toLocaleDateString("en-GB", {
    timeZone: "UTC",
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "2-digit",
  });

  // --- Right section: icons ---
  // 1) System health: prefer sys_health.ok else infer from redis_ok/mqtt_ok
  const sh = data?.sys_health || null;
  let sysState = "unk";
  if (sh && typeof sh === "object") {
    const ok = boolish(sh.ok);
    if (ok !== null) sysState = ok ? "ok" : "bad";
    else {
      const r = boolish(sh.redis_ok);
      const m = boolish(sh.mqtt_ok);
      if (r !== null && m !== null) sysState = (r && m) ? "ok" : "bad";
    }
  }

  // 2) Time source: if we have rt:gps:time -> GPS, else local
  // (Until GPS exists, this will show "LOCAL")
  const hasGpsTime = data?.clock != null; // clock binding maps to rt:gps:time
  const timeState = hasGpsTime ? "ok" : "unk"; // "unk" means "not available yet"
  const timeSrcLabel = hasGpsTime ? "GPS" : "LOCAL";

  // 3) GPS lock: use gps_fix truthiness (unknown until GPS exists)
  // When you implement GPS later, you can refine this check.
  const fixVal = data?.gps_fix;
  let gpsState = "unk";
  const fixBool = boolish(fixVal);
  if (fixBool !== null) gpsState = fixBool ? "ok" : "bad";
  else if (fixVal != null) gpsState = "ok"; // any non-null object/string -> assume "some fix"
  const gpsLabel = (gpsState === "ok") ? "LOCK" : (gpsState === "bad") ? "NOFIX" : "N/A";

  // Temp placeholders until you publish a real key
  const tempF = data?.temp_f ?? null;
  const tempC = data?.temp_c ?? null;
  const tempLine = (tempF != null || tempC != null)
    ? `${tempF != null ? esc(tempF) + "°F" : "--°F"} / ${tempC != null ? esc(tempC) + "°C" : "--°C"}`
    : `--°F / --°C`;

  const sysI = iconTri(sysState);
  const timeI = iconTri(timeState);
  const gpsI = iconTri(gpsState);

  container.innerHTML = `
    <div class="rt-topbar rt-topbar-grid">
      <!-- LEFT -->
      <div class="rt-topbar-left">
        <div class="rt-brand">
          <div class="rt-brand-mark">RollingThunder</div>
          <div class="rt-brand-page">${esc(page)}</div>
        </div>
      </div>

      <!-- MIDDLE -->
      <div class="rt-topbar-mid">
        <div class="rt-utc-time">${esc(utcTime)} <span class="rt-utc-tag">UTC</span></div>
        <div class="rt-utc-date">${esc(utcDate)}</div>
      </div>

      <!-- RIGHT -->
      <div class="rt-topbar-right">
        <div class="rt-icons">
          <div class="rt-icon" title="System Health">${sysI.glyph}<span>${esc(sysI.label)}</span></div>
          <div class="rt-icon" title="Time Source">${timeI.glyph}<span>${esc(timeSrcLabel)}</span></div>
          <div class="rt-icon" title="GPS Fix">${gpsI.glyph}<span>${esc(gpsLabel)}</span></div>
        </div>
        <div class="rt-temp">${tempLine}</div>
      </div>
    </div>
  `;
}
