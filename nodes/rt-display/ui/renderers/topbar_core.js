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

function weatherBadge({ symbol, label, opacity = 1.0, iconClass = "" }) {
  return `
    <div class="rt-topbar-icon rt-wx-badge" title="${esc(label)}"
         style="display:flex; flex-direction:column; align-items:center; gap:2px; opacity:${opacity}; width:72px; min-width:72px; flex:0 0 72px;">
      <div class="rt-wx-symbol ${esc(iconClass)}">${esc(symbol)}</div>
      <div class="rt-wx-label">${esc(label)}</div>
    </div>
  `;
}

/**
 * shape-first icon: check, x, or dot
 * - symbol conveys state even if colors are hard to distinguish
 */
function iconBadge({ symbol, label, opacity = 1.0 }) {
  return `
    <div class="rt-topbar-icon" title="${esc(label)}"
         style="display:flex; flex-direction:column; align-items:center; gap:2px; opacity:${opacity};">
      <div style="font-size:24px; line-height:18px;">${esc(symbol)}</div>
      <div style="font-size:20px; opacity:0.85;">${esc(label)}</div>
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

function startLocalClock(initialIsoTime) {
  if (!initialIsoTime) return null;

  let base = new Date(initialIsoTime);
  let baseMs = base.getTime();
  let startMs = Date.now();

  return () => {
    const now = Date.now();
    const delta = now - startMs;
    return new Date(baseMs + delta);
  };
}

export function renderTopbarCore(container, panel, data) {
  console.log("topbar_core data:", data);
  const params = new URLSearchParams(location.search);
  //const page = params.get("page") || "home";

  const pageId =
    data?.ui?.page ||   // 👈 THIS is the real source
    data?.ui_page?.page ||
    data?.ui_page?.id ||
    data?.page ||
    params.get("page") ||
    "home";

  const pageTitle =
    data?.ui_page?.title ||
    data?.page_title ||
    pageId;

  const page = `${pageTitle} (${pageId})`;

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

  // These get "badgeified" later, so they must be mutable:
  let sysSymbol = sysBadge.symbol;
  let sysLabel  = sysBadge.label;
  let sysOpacity = 1.0;


  // 2) Time semantics (prefer GPS time when fix_type >= 1)
  let timeSymbol = "●";
  let timeLabel = "TIME ?";
  let timeOpacity = 1.0;

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
  let gpsOpacity  = 1.0;

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

  // ---- CPU temp status
  const tempF = numOrNull(data?.temp?.f);
  const tempC = numOrNull(data?.temp?.c);
  const tempStale = String(data?.temp?.stale ?? "1") === "1";

  let cpuSymbol = "●";
  let cpuClass = "rt-cpu-unknown";
  let cpuTitle = "CPU temp unknown";

  if (tempStale || tempC == null) {
    cpuSymbol = "?";
    cpuClass = "rt-cpu-unknown";
    cpuTitle = "CPU temp stale/unknown";
  } else if (tempC >= 75) {
    cpuSymbol = "🔴";
    cpuClass = "rt-cpu-bad";
    cpuTitle = `CPU hot ${tempF?.toFixed(0)}°F / ${tempC.toFixed(1)}°C`;
  } else if (tempC >= 60) {
    cpuSymbol = "⚠";
    cpuClass = "rt-cpu-warn";
    cpuTitle = `CPU warm ${tempF?.toFixed(0)}°F / ${tempC.toFixed(1)}°C`;
  } else {
    cpuSymbol = "✔";
    cpuClass = "rt-cpu-ok";
    cpuTitle = `CPU good ${tempF?.toFixed(0)}°F / ${tempC.toFixed(1)}°C`;
  }

  // ---- Outside weather
  const weatherF = numOrNull(data?.weather?.f);
  const weatherC = numOrNull(data?.weather?.c);
  const weatherStale = String(data?.weather?.stale ?? "1") === "1";

  const showWeatherC = Math.floor(Date.now() / 10000) % 2 === 1;

  const weatherText =
    weatherF == null || weatherC == null
      ? "WX"
      : showWeatherC
        ? `${weatherC.toFixed(1)}°C`
        : `${weatherF.toFixed(0)}°F`;

  let weatherSymbol = "☀";
  let weatherIconClass = "rt-wx-sun";
  let weatherOpacity = weatherStale ? 0.45 : 1.0;

  const forecast = String(data?.weather?.short_forecast || "").toLowerCase();

  if (forecast.includes("storm") || forecast.includes("thunder")) {
    weatherSymbol = "⚡";
    weatherIconClass = "rt-wx-storm";
  } else if (forecast.includes("rain") || forecast.includes("shower")) {
    weatherSymbol = "☂︎";
    weatherIconClass = "rt-wx-rain";
  } else if (forecast.includes("snow")) {
    weatherSymbol = "❄︎";
    weatherIconClass = "rt-wx-snow";
  } else if (forecast.includes("cloud")) {
    weatherSymbol = "☁︎";
    weatherIconClass = "rt-wx-cloud";
  } else if (forecast.includes("sun") || forecast.includes("clear")) {
    weatherSymbol = "☀︎";
    weatherIconClass = "rt-wx-sun";
  }

  // ---- Radio/RIG state
  const radioState = data?.radio_state || null;
  const radioOnline = String(radioState?.online || "").toLowerCase() === "true";
  const radioReason = String(radioState?.reason || "").trim();

  let rigSymbol = "✗";
  let rigOpacity = 1.0;

  const failures = numOrNull(radioState?.failures);

  if (radioOnline) {
    rigSymbol = "✔";
  } else if (failures && failures < 3) {
    rigSymbol = "⚠";   // transient issue
  } else {
    rigSymbol = "✗";   // real failure
  }

  container.innerHTML = `
    <div class="rt-topbar"
        style="display:flex; align-items:center; justify-content:space-between; gap:16px; padding:6px 10px;">

      <!-- Left -->
      <div class="rt-topbar-left"
          style="min-width:170px; display:flex; flex-direction:column; gap:2px;">

        <div class="rt-logo-wordmark" title="Rolling Thunder - Mobile QTH Anywhere">
          <div class="rt-logo-main">ROLLING THUNDER</div>
          <div class="rt-logo-sub">MOBILE QTH ✦ ANYWHERE</div>
        </div>
        
      </div>

      <!-- Middle -->
      <div class="rt-topbar-mid"
          style="flex:1; text-align:center; display:flex; flex-direction:column; gap:2px; opacity:${midOpacity};">
        <div class="rt-utc-time" style="font-weight:700; font-size:28px; letter-spacing:0.5px;">${esc(utcTime)}</div>
        <div class="rt-utc-date" style="font-size:20px; opacity:0.85;">${esc(utcDate)}</div>
      </div>

      <!-- Right -->
      <div class="rt-topbar-right"
          style="min-width:210px; display:flex; justify-content:flex-end; align-items:flex-end; gap:12px;">
        <div style="display:flex; gap:10px; align-items:flex-end; white-space:nowrap;">
          ${iconBadge({ symbol: timeSymbol, label: timeLabel, opacity: timeOpacity })}
          ${iconBadge({ symbol: gpsSymbol, label: gpsLabel, opacity: gpsOpacity })}
          ${iconBadge({ symbol: rigSymbol, label: "RIG", opacity: rigOpacity })}
          ${iconBadge({ symbol: cpuSymbol, label: "CPU", opacity: 1.0 })}
          ${weatherBadge({ symbol: weatherSymbol, label: weatherText, opacity: weatherOpacity, iconClass: weatherIconClass })}
        </div>
      </div>
    </div>
  `;

  window.__rtTopbarClockBaseMs = dt.getTime();
  window.__rtTopbarClockStartedAtMs = Date.now();

  if (!window.__rtTopbarClockTimer) {
    window.__rtTopbarClockTimer = setInterval(() => {
      const timeEl = document.querySelector(".rt-utc-time");
      const dateEl = document.querySelector(".rt-utc-date");
      if (!timeEl || !dateEl) return;

      const baseMs = window.__rtTopbarClockBaseMs;
      const startedAtMs = window.__rtTopbarClockStartedAtMs;
      if (!Number.isFinite(baseMs) || !Number.isFinite(startedAtMs)) return;

      const live = new Date(baseMs + (Date.now() - startedAtMs));
      timeEl.textContent = fmtUtcTime(live);
      dateEl.textContent = fmtUtcDate(live);
    }, 1000);
  }
  if (!window.__rtWeatherRotateTimer) {
    window.__rtWeatherRotateTimer = setInterval(() => {
      const el = document.querySelector(".rt-wx-label");
      if (!el) return;

      const f = window.__rtWeatherF;
      const c = window.__rtWeatherC;
      if (!Number.isFinite(f) || !Number.isFinite(c)) return;

      const showC = Math.floor(Date.now() / 10000) % 2 === 1;
      el.textContent = showC ? `${c.toFixed(1)}°C` : `${f.toFixed(0)}°F`;
    }, 1000);
  }

  window.__rtWeatherF = weatherF;
  window.__rtWeatherC = weatherC;
}
