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
function iconBadge({ symbol, label, opacity = 1.0 }) {
  return `
    <div class="rt-topbar-icon" title="${esc(label)}"
         style="display:flex; flex-direction:column; align-items:center; gap:2px; opacity:${opacity};">
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

function badgeFrom({
  obj,
  fresh,
  okVal,          // raw “good/bad” value (can be bool/1/0/"true"/"false")
  okLabel = "OK",
  badLabel = "BAD",
  staleLabel = "STALE",
  unknownLabel = "?",
  staleSymbol = "○",
  unknownSymbol = "●",
  okSymbol = "✓",
  badSymbol = "✗",
}) {
  // 1) missing/unknown
  if (!isObj(obj)) return { symbol: unknownSymbol, label: unknownLabel };

  // 2) stale (includes missing timestamp)
  if (!fresh?.okTs || fresh?.stale) return { symbol: staleSymbol, label: staleLabel };

  // 3/4) fresh + known good/bad
  const known = typeof okVal !== "undefined" && okVal !== null && okVal !== "";
  if (!known) return { symbol: unknownSymbol, label: unknownLabel };

  const ok =
    okVal === true || okVal === 1 || okVal === "1" ||
    okVal === "true" || okVal === "TRUE";

  return ok
    ? { symbol: okSymbol, label: okLabel }
    : { symbol: badSymbol, label: badLabel };
}

function isStale(fresh) {
  return !!fresh && fresh.okTs && fresh.stale;
}

function staleBadgeify(symbol, label, fresh, { staleSymbol = "○" } = {}) {
  if (!fresh?.okTs) return { symbol, label, opacity: 0.85 }; // unknown timestamp
  if (!fresh.stale) return { symbol, label, opacity: 1.0 };
  // stale → downgrade symbol + mark label
  const clean = String(label || "").replace(/\s+\(STALE\)$/, "");
  return { symbol: staleSymbol, label: `${clean} (STALE)`, opacity: 0.45 };
}

// Optional: make center time/date dim when time is stale
function staleTextOpacity(fresh, normal = 1.0, stale = 0.45) {
  if (!fresh?.okTs) return 0.85;
  return fresh.stale ? stale : normal;
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

  const STALE_SYMBOL = "○"; // hollow circle


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

  const sysBadge = badgeFrom({
    obj: sys,
    fresh: sysFresh,
    okVal: sys?.ok,
    okLabel: "SYS OK",
    badLabel: "SYS BAD",
    staleLabel: "SYS STALE",
    unknownLabel: "SYS ?",
  });
  const sysSymbol = sysBadge.symbol;
  const sysLabel = sysBadge.label;

  // 2) Time semantics (prefer GPS time when fix_type >= 1)
  let timeSymbol = "●";
  let timeLabel = "TIME ?";

  if (!isObj(clock)) {
    timeSymbol = "●";
    timeLabel = "TIME NO DATA";

  } else if (!clockFresh?.okTs) {
    timeSymbol = "●";
    timeLabel = "TIME NO TS";

  } else if (clockFresh.stale) {
    timeSymbol = "○";
    timeLabel = "TIME STALE";

  } else {
    // If GPS fix_type >= 1, we consider time "GPS-derived" even without location lock.
    const fixType = numOrNull(fix?.fix_type);

    if (fixType != null && fixType >= 1) {
      timeSymbol = "✓";
      timeLabel = "GPS TIME";
    } else {
      // Not GPS-backed yet (or we don't know) → fallback to system time
      timeSymbol = "●";
      timeLabel = "SYS TIME";
    }
  }


  // 3) GPS fix semantics
  let gpsSymbol = "●";
  let gpsLabel = "GPS ?";

  if (!isObj(fix)) {
    gpsSymbol = "●";
    gpsLabel = "GPS NO DATA";

  } else if (!fixFresh?.okTs) {
    gpsSymbol = "●";
    gpsLabel = "GPS NO TS";

  } else if (fixFresh.stale) {
    gpsSymbol = "○";
    gpsLabel = "GPS STALE";

  } else {
    const fixType = numOrNull(fix.fix_type);
    const sats = numOrNull(fix.sats);

    if (fixType === 1) {
      // Time-only fix (important semantic improvement)
      gpsSymbol = "●";
      gpsLabel = "SEARCH / TIME";

    } else if (fixType >= 2) {
      gpsSymbol = "✓";
      gpsLabel = sats == null ? "GPS FIX" : `GPS FIX ${sats}`;

    } else {
      // fix_type === 0 or unknown
      gpsSymbol = "✗";
      gpsLabel = sats == null ? "NO FIX" : `NO FIX ${sats}`;
    }
  }

  // --- Phase C-3: apply staleness visualization (no new data)
  ({ symbol: sysSymbol, label: sysLabel, opacity: sysOpacity } =
    staleBadgeify(sysSymbol, sysLabel, sysFresh, { staleSymbol: "○" }));

  ({ symbol: timeSymbol, label: timeLabel, opacity: timeOpacity } =
    staleBadgeify(timeSymbol, timeLabel, clockFresh, { staleSymbol: "○" }));

  ({ symbol: gpsSymbol, label: gpsLabel, opacity: gpsOpacity } =
    staleBadgeify(gpsSymbol, gpsLabel, fixFresh, { staleSymbol: "○" }));

  const midOpacity = staleTextOpacity(clockFresh, 1.0, 0.45);




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
          style="flex:1; text-align:center; display:flex; flex-direction:column; gap:2px; opacity:${midOpacity};">
        <div style="font-weight:700; font-size:18px; letter-spacing:0.5px;">${esc(utcTime)}</div>
        <div style="font-size:12px; opacity:0.85;">${esc(utcDate)}</div>
      </div>

      <!-- Right -->
      <div class="rt-topbar-right"
          style="min-width:210px; display:flex; justify-content:flex-end; align-items:flex-end; gap:12px;">
        <div style="display:flex; gap:10px; align-items:flex-end; white-space:nowrap;">
          ${iconBadge({ symbol: sysSymbol, label: sysLabel, opacity: sysOpacity })}
          ${iconBadge({ symbol: timeSymbol, label: timeLabel, opacity: timeOpacity })}
          ${iconBadge({ symbol: gpsSymbol, label: gpsLabel, opacity: gpsOpacity })}
        </div>

        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:2px; min-width:70px; white-space:nowrap;">
          <div style="font-weight:700;">${esc(tempF)}°F</div>
          <div style="font-size:12px; opacity:0.85;">${esc(tempC)}°C</div>
        </div>
      </div>
    </div>
  `;

}
