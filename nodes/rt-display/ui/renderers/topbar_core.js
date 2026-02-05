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

/**
 * Normalize boolean / missing state into a tri-state UI symbol.
 * This is presentation logic only.
 */
function triState(val, { ok, bad, unknown }) {
  if (val === true) return ok;
  if (val === false) return bad;
  return unknown;
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

export function renderTopbarCore(container, panel, data) {
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  const sys = data?.sys_health || null;
  const clock = data?.clock || null;
  const fix = data?.gps_fix || null;

  // ---- Middle: UTC time/date (prefer gps_state_publisher’s utc_iso; fall back to browser UTC)
  const dt = parseIso(clock?.utc_iso) || new Date();
  const utcTime = fmtUtcTime(dt);
  const utcDate = fmtUtcDate(dt);

  // ---- Right icons (shape-first)
  // 1) System health: ✓ if ok truthy, ✗ if explicitly false, ● if unknown
  const sysSymbol = triState(sys?.ok, {
    ok: "✓",
    bad: "✗",
    unknown: "●",
  });

  const sysLabel =
    sys?.ok === true ? "SYS OK" :
    sys?.ok === false ? "SYS BAD" :
    "SYS ?";


  // 2) Time source: GPS vs SYSTEM vs unknown
  // clock.source currently "system" in your output
  let timeSymbol = "●";
  let timeLabel = "TIME ?";
  if (clock && typeof clock.source === "string") {
    const src = clock.source.toLowerCase();
    if (src === "gps") {
      timeSymbol = "✓";
      timeLabel = "GPS TIME";
    } else if (src === "system") {
      timeSymbol = "●";
      timeLabel = "SYS TIME";
    } else {
      timeSymbol = "●";
      timeLabel = `TIME ${clock.source}`;
    }
  }

  // 3) GPS fix: ✓ when has_fix, ✗ when not
  const gpsSymbol = triState(fix?.has_fix, {
    ok: "✓",
    bad: "✗",
    unknown: "●",
  });

  const sats =
    typeof fix?.sats === "number" ? fix.sats :
    (typeof fix?.sats === "string" && fix.sats.trim() !== "" && !Number.isNaN(Number(fix.sats)))
      ? Number(fix.sats)
      : null;

  const gpsLabel =
    fix?.has_fix === true
      ? `GPS ${sats ?? ""}`.trim()
      : fix?.has_fix === false
        ? `NO FIX ${sats ?? ""}`.trim()
        : "GPS ?";


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
