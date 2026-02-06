const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

/** Parse ISO string safely. Returns Date or null. */
function parseIso(iso) {
  if (!iso || typeof iso !== "string") return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function isObj(v) {
  return v && typeof v === "object";
}

function fmtUtcTime(d) {
  // 24h UTC HH:MM:SS
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}Z`;
}

function fmtUtcDate(d) {
  // YYYY-MM-DD
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * shape-first icon: check, x, or dot
 * - symbol conveys state even if colors are hard to distinguish
 */
function iconBadge({ symbol, label }) {
  return `
    <div class="rt-topbar-icon" title="${esc(label)}"
         style="display:flex; flex-direction:column; align-items:center; gap:2px;">
      <div style="font-size:18px; line-height:18px;">${esc(symbol)}</div>
      <div style="font-size:10px; opacity:0.85;">${esc(label)}</div>
    </div>
  `;
}

function numOrNull(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

/** Returns { ageMs, stale, okTs } where okTs indicates we had a usable timestamp. */
function freshnessFrom(obj, tsField, staleAfterMs) {
  const ts = numOrNull(obj?.[tsField]);
  if (ts == null) return { ageMs: null, stale: true, okTs: false };
  const ageMs = Date.now() - ts;
  return { ageMs, stale: ageMs > staleAfterMs, okTs: true };
}

export function renderTopbarCore(container, panel, data) {
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  const sys = data?.sys_health || null;
  const clock = data?.clock || null;
  const fix = data?.gps_fix || null;
  const speed = data?.gps_speed || null;

  // ---- Phase B Step 1: freshness (derived only; no visuals yet)
  const STALE_SYS_MS = 15000;   // system health should be chatty
  const STALE_GPS_MS = 5000;    // gps publisher tick-ish

  const sysFresh = freshnessFrom(sys, "last_seen_ms", STALE_SYS_MS); // sys health uses last_seen_ms
  const clockFresh = freshnessFrom(clock, "last_update_ms", STALE_GPS_MS);
  const fixFresh = freshnessFrom(fix, "last_update_ms", STALE_GPS_MS);
  const speedFresh = freshnessFrom(speed, "last_update_ms", STALE_GPS_MS);

  // Attach derived info for debugging / later steps (no rendering change required)
  data = {
    ...data,
    _freshness: {
      sys: sysFresh,
      clock: clockFresh,
      fix: fixFresh,
      speed: speedFresh,
    },
  };

  // ---- Middle: UTC time/date (prefer gps_state_publisher’s utc_iso; fall back to browser UTC)
  const dt = parseIso(clock?.utc_iso) || new Date();
  const utcTime = fmtUtcTime(dt);
  const utcDate = fmtUtcDate(dt);

  // ---- Right icons (shape-first)
  let sysSymbol = "●";
  let sysLabel = "SYS ?";

  if (!isObj(sys)) {
    sysSymbol = "●";
    sysLabel = "SYS ?";
  } else if (sysFresh?.okTs === false || sysFresh?.stale) {
    sysSymbol = "●";
    sysLabel = "SYS STALE";
  } else {
    // sys.ok might be 1/0 or true/false; normalize to boolean when present
    const okVal = sys.ok;
    const ok =
      okVal === true || okVal === 1 || okVal === "1" || okVal === "true";

    const known =
      typeof okVal !== "undefined" && okVal !== null && okVal !== "";

    sysSymbol = known ? (ok ? "✓" : "✗") : "●";
    sysLabel = known ? (ok ? "SYS OK" : "SYS BAD") : "SYS ?";
  }


  // 2) Time source: GPS vs SYSTEM vs unknown
  // clock.source currently "system" in your output
  let timeSymbol = "●";
  let timeLabel = "TIME ?";

  if (!isObj(clock)) {
    timeSymbol = "●";
    timeLabel = "TIME ?";
  } else if (clockFresh?.okTs === false || clockFresh?.stale) {
    timeSymbol = "●";
    timeLabel = "TIME STALE";
  } else if (typeof clock.source === "string" && clock.source.length) {
    const src = clock.source.toLowerCase();
    if (src === "gps") {
      timeSymbol = "✓";
      timeLabel = "GPS TIME";
    } else if (src === "system") {
      timeSymbol = "●";       // “not GPS” isn’t “bad”, it’s “fallback”
      timeLabel = "SYS TIME";
    } else {
      timeSymbol = "●";
      timeLabel = `TIME ${clock.source}`;
    }
  } else {
    timeSymbol = "●";
    timeLabel = "TIME ?";
  }


  // 3) GPS fix: ✓ when has_fix, ✗ when not
  let gpsSymbol = "●";
  let gpsLabel = "GPS ?";

  if (!isObj(fix)) {
    gpsSymbol = "●";
    gpsLabel = "GPS ?";
  } else if (fixFresh?.okTs === false || fixFresh?.stale) {
    gpsSymbol = "●";
    gpsLabel = "GPS STALE";
  } else if (typeof fix.has_fix !== "undefined") {
    const hasFix = fix.has_fix === true || fix.has_fix === 1 || fix.has_fix === "true";
    const sats = typeof fix.sats === "number" ? fix.sats : null;

    gpsSymbol = hasFix ? "✓" : "✗";
    gpsLabel = hasFix ? `GPS ${sats ?? ""}`.trim() : `NO FIX ${sats ?? ""}`.trim();
  } else {
    gpsSymbol = "●";
    gpsLabel = "GPS ?";
  }



  // ---- Temp (placeholder for now; you said we haven’t written it yet)
  const tempF = data?.temp?.f ?? "--";
  const tempC = data?.temp?.c ?? "--";

  container.innerHTML = `
    <div class="rt-topbar"
        style="display:flex; align-items:center; justify-content:space-between; gap:16px; padding:6px 10px;">

      <!-- Left -->
      <div class="rt-topbar-left"
          style="min-width:170px; display:flex; flex-direction:column; gap:2px;">
        <div class="rt-topbar-brand" style="font-weight:700;">RollingThunder</div>
        <div class="rt-topbar-page" style="font-size:12px; opacity:0.85;">${esc(page)}</div>
      </div>

      <!-- Middle -->
      <div class="rt-topbar-mid"
          style="flex:1; text-align:center; display:flex; flex-direction:column; gap:2px;">
        <div style="font-weight:700; font-size:18px; letter-spacing:0.5px;">${esc(utcTime)}</div>
        <div style="font-size:12px; opacity:0.85;">${esc(utcDate)}</div>
      </div>

      <!-- Right -->
      <div class="rt-topbar-right"
          style="min-width:210px; display:flex; justify-content:flex-end; align-items:flex-end; gap:12px;">
        <div style="display:flex; gap:10px; align-items:flex-end; white-space:nowrap;">
          ${iconBadge({ symbol: sysSymbol, label: sysLabel })}
          ${iconBadge({ symbol: timeSymbol, label: timeLabel })}
          ${iconBadge({ symbol: gpsSymbol, label: gpsLabel })}
        </div>

        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:2px; min-width:70px; white-space:nowrap;">
          <div style="font-weight:700;">${esc(tempF)}°F</div>
          <div style="font-size:12px; opacity:0.85;">${esc(tempC)}°C</div>
        </div>
      </div>
    </div>
  `;

}
