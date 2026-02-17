// wpsd_status.js
//
// Drop-in replacement (self-contained):
// - Adds flag next to callsign (img, emoji fallback)
// - Keeps alias only when it matches the callsign (prevents mismatched name)
// - Stable FRESH/STALE (hysteresis)
// - No external dependencies

function safeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function pillHtml(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";
  return `<span class="${cls}">${safeText(label)}</span>`;
}

function ageSecFromMs(ms) {
  const n = Number(ms ?? NaN);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.max(0, Math.floor((Date.now() - n) / 1000));
}

function fmtAge(ageSec) {
  if (ageSec == null) return "—";
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

// ---------------- Flag helpers ----------------

// Your planned proxy endpoint (same-origin):
// ui_snapshot_api.py should serve /ui/flags/<cc>.png by proxying from rt-wpsd.
function flagUrlFromCountryCode(cc) {
  const code = String(cc || "").trim().toLowerCase();
  if (!code || code.length !== 2) return null;
  return `/ui/flags/${code}.png`;
}

// Emoji fallback (regional indicator symbols)
function flagEmojiFromCountryCode(cc) {
  const code = String(cc || "").trim().toUpperCase();
  if (!code || code.length !== 2) return "🏳️";
  const A = 0x1F1E6;
  const base = "A".charCodeAt(0);
  const c1 = code.charCodeAt(0);
  const c2 = code.charCodeAt(1);
  if (c1 < 65 || c1 > 90 || c2 < 65 || c2 > 90) return "🏳️";
  return String.fromCodePoint(A + (c1 - base), A + (c2 - base));
}

// Only show alias when it "belongs" to the callsign we’re showing.
// Your ingestor can misassociate alias lines; this stops bad UI.
function aliasForCall(alias, callsign) {
  const a = String(alias || "").trim();
  const cs = String(callsign || "").trim().toUpperCase();
  if (!a || !cs) return null;

  // accept if alias begins with the callsign (common Pi-Star format)
  const aUp = a.toUpperCase();
  if (aUp.startsWith(cs)) return a;

  // or if alias contains the callsign as a whole token
  const re = new RegExp(`\\b${cs.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}\\b`, "i");
  if (re.test(a)) return a;

  return null;
}

function callWithFlagHtml(callsign, countryCode, alias) {
  const cs = String(callsign || "").trim().toUpperCase();
  if (!cs) return "—";

  const cc = String(countryCode || "").trim().toLowerCase();
  const url = flagUrlFromCountryCode(cc);
  const emoji = flagEmojiFromCountryCode(cc);

  const goodAlias = aliasForCall(alias, cs);
  const aliasHtml = goodAlias ? `<div class="rt-subtle">${safeText(goodAlias)}</div>` : "";

  if (url) {
    return `
      <div class="rt-callrow">
        <span class="rt-flagwrap" aria-hidden="true">
          <img class="rt-flagimg" src="${safeText(url)}" alt="" loading="lazy"
               onerror="this.style.display='none'; this.nextElementSibling.style.display='inline-block';" />
          <span class="rt-flagemoji" style="display:none;">${safeText(emoji)}</span>
        </span>
        <div class="rt-calltext">
          <div class="rt-callsign">${safeText(cs)}</div>
          ${aliasHtml}
        </div>
      </div>
    `;
  }

  return `
    <div class="rt-callrow">
      <span class="rt-flagwrap" aria-hidden="true">
        <span class="rt-flagemoji">${safeText(emoji)}</span>
      </span>
      <div class="rt-calltext">
        <div class="rt-callsign">${safeText(cs)}</div>
        ${aliasHtml}
      </div>
    </div>
  `;
}

// ---------------- Rendering ----------------

function slotSummary(slotNum, slot) {
  const active = !!slot?.active;
  const statePill = active ? pillHtml("bad", "ACTIVE") : pillHtml("ok", "IDLE");

  const tg = (slot?.tg != null) ? `TG ${safeText(slot.tg)}` : "—";
  const tgName = slot?.tg_name ? safeText(slot.tg_name) : "";
  const tgLine = tgName ? `${tg}<div class="rt-subtle">${tgName}</div>` : tg;

  const dir = slot?.direction ? safeText(slot.direction) : "—";
  const dur = (slot?.dur_s != null) ? `${safeText(slot.dur_s)}s` : "—";
  const loss = (slot?.loss_pct != null) ? `${safeText(slot.loss_pct)}%` : "—";

  const sinceMs = slot?.since_ms ?? null;
  const lastEndMs = slot?.last_end_ms ?? null;

  const sinceAge = fmtAge(ageSecFromMs(sinceMs));
  const endAge = fmtAge(ageSecFromMs(lastEndMs));
  const timeLabel = active ? "Since" : "Last";

  const callHtml = callWithFlagHtml(
    slot?.callsign,
    slot?.country_code || slot?.cc || null,
    slot?.alias
  );

  return `
    <div class="rt-wpsd-slot">
      <div class="rt-wpsd-slot-hd">
        <div class="rt-wpsd-slot-title">TS${slotNum}</div>
        <div class="rt-wpsd-slot-pill">${statePill}</div>
      </div>

      <table class="rt-kv">
        <tr><td class="k">Call</td><td class="v">${callHtml}</td></tr>
        <tr><td class="k">Target</td><td class="v">${tgLine}</td></tr>
        <tr><td class="k">Dir</td><td class="v">${dir}</td></tr>
        <tr><td class="k">Dur</td><td class="v">${dur}</td></tr>
        <tr><td class="k">Loss</td><td class="v">${loss}</td></tr>
        <tr><td class="k">${timeLabel}</td><td class="v">${active ? sinceAge : endAge}</td></tr>
      </table>
    </div>
  `;
}

function recentRows(items) {
  const arr = Array.isArray(items) ? items : [];
  const rows = arr.slice(0, 8).map((it) => {
    const slot = it?.slot != null ? `TS${safeText(it.slot)}` : "—";
    const callHtml = callWithFlagHtml(
      it?.callsign,
      it?.country_code || it?.cc || null,
      it?.alias
    );
    const tg = it?.tg != null ? `TG ${safeText(it.tg)}` : "—";
    const dur = it?.dur_s != null ? `${safeText(it.dur_s)}s` : "—";
    const loss = it?.loss_pct != null ? `${safeText(it.loss_pct)}%` : "—";
    const age = fmtAge(ageSecFromMs(it?.ts_ms));

    return `
      <tr>
        <td>${slot}</td>
        <td>${callHtml}</td>
        <td>${tg}</td>
        <td>${dur}</td>
        <td>${loss}</td>
        <td class="rt-right">${age}</td>
      </tr>
    `;
  }).join("");

  return rows || `<tr><td colspan="6">No recent activity</td></tr>`;
}

// Hysteresis: stale if >20s, fresh again if <10s
const STALE_ON_SEC = 20;
const STALE_OFF_SEC = 10;

function startAgeTicker(container) {
  if (container.__rtAgeTimer) {
    try { clearInterval(container.__rtAgeTimer); } catch (_) {}
    container.__rtAgeTimer = null;
  }

  container.__rtAgeTimer = setInterval(() => {
    const root = container.querySelector("[data-rt-last-update-ms]");
    if (!root) return;

    const ms = root.getAttribute("data-rt-last-update-ms");
    const age = ageSecFromMs(ms);
    const ageTxt = fmtAge(age);

    const ageEl = container.querySelector("[data-rt-age-text]");
    if (ageEl) ageEl.textContent = ageTxt;

    const prev = container.__rtIsStale === true;
    let isStale = prev;

    if (age == null) {
      isStale = true;
    } else if (!prev && age > STALE_ON_SEC) {
      isStale = true;
    } else if (prev && age < STALE_OFF_SEC) {
      isStale = false;
    }

    container.__rtIsStale = isStale;
    container.classList.toggle("stale", isStale);

    const badge = container.querySelector("[data-rt-stale-badge]");
    if (badge) badge.innerHTML = isStale ? pillHtml("warn", "STALE") : pillHtml("ok", "FRESH");
  }, 1000);
}

export function renderWpsdStatus(container, panel, data) {
  const rfSlots = data?.rf_slots || null;
  const rfRecent = data?.rf_recent || null;

  const slots = rfSlots?.slots || {};
  const s1 = slots?.["1"] || slots?.[1] || {};
  const s2 = slots?.["2"] || slots?.[2] || {};

  const lastUpdateMs = rfSlots?.last_update_ms ?? rfRecent?.last_update_ms ?? null;
  const ageTxt = fmtAge(ageSecFromMs(lastUpdateMs));

  container.innerHTML = `
    <div class="rt-wpsd" data-rt-last-update-ms="${lastUpdateMs ?? ""}">
      <div class="rt-wpsd-hd">
        <div class="rt-wpsd-title">WPSD RF</div>
        <div class="rt-wpsd-meta">
          <span class="rt-subtle">Age:</span> <span data-rt-age-text>${ageTxt}</span>
          <span data-rt-stale-badge style="margin-left:10px;">${pillHtml("ok", "FRESH")}</span>
        </div>
      </div>

      <div class="rt-wpsd-grid">
        ${slotSummary(1, s1)}
        ${slotSummary(2, s2)}
      </div>

      <div class="rt-wpsd-recent">
        <div class="rt-wpsd-subtitle">Recent</div>
        <div class="rt-table-wrap">
          <table class="rt-table">
            <thead>
              <tr>
                <th>TS</th>
                <th>Call</th>
                <th>Target</th>
                <th>Dur</th>
                <th>Loss</th>
                <th class="rt-right">Age</th>
              </tr>
            </thead>
            <tbody>
              ${recentRows(rfRecent?.items)}
            </tbody>
          </table>
        </div>
      </div>

      <style>
        .rt-callrow { display:flex; gap:10px; align-items:flex-start; }
        .rt-flagwrap { width:28px; min-width:28px; display:flex; align-items:center; justify-content:center; }
        .rt-flagimg { width:26px; height:18px; object-fit:cover; border-radius:3px; }
        .rt-flagemoji { font-size:18px; line-height:18px; }
        .rt-calltext { display:flex; flex-direction:column; }
        .rt-callsign { font-weight:700; }
      </style>
    </div>
  `;

  startAgeTicker(container);
}
