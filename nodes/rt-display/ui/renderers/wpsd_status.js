// wpsd_status.js
//
// Drop-in replacement for renderers/wpsd_status.js
// Changes:
// - Callsigns are plain text (NO links; kiosk/car-safe)
// - Removes BER everywhere
// - Adds a country flag icon next to callsign (best-effort heuristic from callsign prefix)
//   -> tries to load WPSD-served flag PNGs: http://rt-wpsd.local/images/flags/<cc>.png
//   -> falls back to emoji flag if image fails (or if unknown)
// - Fresh/STALE pill uses hysteresis (no 1s flip-flop when idle)

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

// --- Country flag (best effort) ---------------------------------------------

function normCall(callsign) {
  return String(callsign || "").trim().toUpperCase();
}

// Heuristic mapping. Not perfect. Good enough for “flag candy” on a car UI.
function countryCodeFromCallsign(callsign) {
  const cs = normCall(callsign);
  if (!cs) return null;

  // USA (very common): K, N, W, AA-AL
  if (/^(K|N|W)\d/.test(cs)) return "us";
  if (/^A[A-L]\d/.test(cs)) return "us";

  // Canada
  if (/^(VE|VA|VY)\d/.test(cs)) return "ca";

  // Australia
  if (/^VK\d/.test(cs)) return "au";

  // New Zealand
  if (/^ZL\d/.test(cs)) return "nz";

  // UK (rough)
  if (/^(G|M|2E|2M|GM|GW|GI|GJ)\d/.test(cs)) return "gb";

  // Germany
  if (/^(DL|DA|DB|DC|DD|DE|DF|DG|DH|DJ|DK|DM|DO|DP|DR)\d/.test(cs)) return "de";

  // France
  if (/^(F|TM|TK)\d/.test(cs)) return "fr";

  // Italy
  if (/^(I|IK|IZ)\d/.test(cs)) return "it";

  // Japan
  if (/^(JA|JE|JF|JG|JH|JI|JJ|JK|JL|JM|JN|JO|JR|7J|7K|7L|7M|7N|8J)\d/.test(cs)) return "jp";

  // China
  if (/^(B|BA|BD|BG|BH|BI|BJ|BL|BM|BN|BO|BP|BQ|BR)\d/.test(cs)) return "cn";
  if (/^BY\d/.test(cs)) return "cn";

  // Russia (rough)
  if (/^(R|RA|RC|RD|RE|RF|RG|RH|RI|RJ|RK|RL|RM|RN|RO|RP|RQ|RR|RS|RT|RU|RV|RW|RX|RY|RZ|UA|UB|UC|UD|UE|UF|UG|UH|UI|UJ|UK|UL|UM|UN|UO|UP|UQ|UR|US|UT|UU|UV|UW|UX|UY|UZ)\d/.test(cs)) return "ru";

  // Netherlands
  if (/^(PA|PB|PC|PD|PE|PF|PG|PH|PI)\d/.test(cs)) return "nl";

  // Spain
  if (/^(EA|EB|EC|ED|EE)\d/.test(cs)) return "es";

  // Sweden
  if (/^(SA|SB|SC|SD|SE|SF|SG|SH|SI|SJ|SK)\d/.test(cs)) return "se";

  return null;
}

function emojiFlagFromCC(cc) {
  const c = String(cc || "").toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return "";
  // Regional Indicator Symbols
  const A = 0x1F1E6;
  const out = [...c].map(ch => String.fromCodePoint(A + (ch.charCodeAt(0) - 65))).join("");
  return out;
}

// You can override this at runtime if you ever want:
//   window.RT_WPSD_BASE = "http://192.168.8.184";
function wpsdBaseUrl() {
  return (typeof window !== "undefined" && window.RT_WPSD_BASE) ? String(window.RT_WPSD_BASE) : "http://rt-wpsd.local";
}

function flagHtmlForCallsign(callsign) {
  const cc = countryCodeFromCallsign(callsign);
  const emoji = emojiFlagFromCC(cc);
  if (!cc) return emoji ? `<span class="rt-flag-emoji">${emoji}</span>` : "";

  // Prefer WPSD local images (no external internet needed).
  const src = `${wpsdBaseUrl()}/images/flags/${cc}.png`;
  const alt = cc.toUpperCase();

  // onerror => hide image, show emoji fallback if we have it
  const fallback = emoji ? emoji.replace(/"/g, "") : "";
  return `
    <span class="rt-flag-wrap" title="${safeText(alt)}">
      <img class="rt-flag-img" src="${safeText(src)}" alt="${safeText(alt)}"
           onerror="this.style.display='none'; if(this.nextSibling){ this.nextSibling.style.display='inline'; }" />
      <span class="rt-flag-emoji" style="display:none;">${safeText(fallback)}</span>
    </span>
  `;
}

// --- Rendering ---------------------------------------------------------------

function callsignText(callsign) {
  const cs = normCall(callsign);
  return cs ? safeText(cs) : "—";
}

function aliasLine(alias) {
  const a = String(alias || "").trim();
  if (!a) return "";
  // Keep it subtle; alias strings sometimes contain junk/truncation
  return `<div class="rt-subtle">${safeText(a)}</div>`;
}

function slotSummary(slotNum, slot) {
  const active = !!slot?.active;
  const statePill = active ? pillHtml("bad", "ACTIVE") : pillHtml("ok", "IDLE");

  const cs = normCall(slot?.callsign);
  const flag = cs ? flagHtmlForCallsign(cs) : "";
  const call = cs ? `${flag}<span class="rt-call">${safeText(cs)}</span>${aliasLine(slot?.alias)}` : "—";

  const tg = (slot?.tg != null) ? `TG ${safeText(slot.tg)}` : "—";
  const tgName = slot?.tg_name ? safeText(slot.tg_name) : ""; // optional future field
  const tgLine = tgName ? `${tg}<div class="rt-subtle">${tgName}</div>` : tg;

  const dir = slot?.direction ? safeText(slot.direction) : "—";
  const dur = (slot?.dur_s != null) ? `${safeText(slot.dur_s)}s` : "—";
  const loss = (slot?.loss_pct != null) ? `${safeText(slot.loss_pct)}%` : "—";

  const sinceMs = slot?.since_ms ?? null;
  const lastEndMs = slot?.last_end_ms ?? null;

  const sinceAge = fmtAge(ageSecFromMs(sinceMs));
  const endAge = fmtAge(ageSecFromMs(lastEndMs));

  const timeLabel = active ? "Since" : "Last";

  return `
    <div class="rt-wpsd-slot">
      <div class="rt-wpsd-slot-hd">
        <div class="rt-wpsd-slot-title">TS${slotNum}</div>
        <div class="rt-wpsd-slot-pill">${statePill}</div>
      </div>

      <table class="rt-kv">
        <tr><td class="k">Call</td><td class="v">${call}</td></tr>
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

    const cs = normCall(it?.callsign);
    const flag = cs ? flagHtmlForCallsign(cs) : "";
    const call = cs ? `${flag}<span class="rt-call">${safeText(cs)}</span>${aliasLine(it?.alias)}` : "—";

    const tg = it?.tg != null ? `TG ${safeText(it.tg)}` : "—";
    const dur = it?.dur_s != null ? `${safeText(it.dur_s)}s` : "—";
    const loss = it?.loss_pct != null ? `${safeText(it.loss_pct)}%` : "—";
    const age = fmtAge(ageSecFromMs(it?.ts_ms));

    return `
      <tr>
        <td>${slot}</td>
        <td>${call}</td>
        <td>${tg}</td>
        <td>${dur}</td>
        <td>${loss}</td>
        <td class="rt-right">${age}</td>
      </tr>
    `;
  }).join("");

  return rows || `<tr><td colspan="6">No recent activity</td></tr>`;
}

// Hysteresis so we don’t flap every second.
// - Go STALE when age >= STALE_ON_SEC
// - Return FRESH only when age <= STALE_OFF_SEC
const STALE_ON_SEC = 20;
const STALE_OFF_SEC = 10;

function startAgeTicker(container) {
  if (container.__rtAgeTimer) {
    try { clearInterval(container.__rtAgeTimer); } catch (_) {}
    container.__rtAgeTimer = null;
  }

  // Track state on the container so it survives re-renders inside the same node
  if (typeof container.__rtWpsdIsStale !== "boolean") {
    container.__rtWpsdIsStale = false;
  }

  container.__rtAgeTimer = setInterval(() => {
    const root = container.querySelector("[data-rt-last-update-ms]");
    if (!root) return;

    const ms = root.getAttribute("data-rt-last-update-ms");
    const age = ageSecFromMs(ms);
    const ageTxt = fmtAge(age);

    const ageEl = container.querySelector("[data-rt-age-text]");
    if (ageEl) ageEl.textContent = ageTxt;

    // Hysteresis logic
    let isStale = !!container.__rtWpsdIsStale;
    if (age == null) {
      // If unknown, don't flap: keep prior state
    } else if (!isStale && age >= STALE_ON_SEC) {
      isStale = true;
    } else if (isStale && age <= STALE_OFF_SEC) {
      isStale = false;
    }

    if (isStale !== container.__rtWpsdIsStale) {
      container.__rtWpsdIsStale = isStale;

      container.classList.toggle("stale", isStale);
      const badge = container.querySelector("[data-rt-stale-badge]");
      if (badge) badge.innerHTML = isStale ? pillHtml("warn", "STALE") : pillHtml("ok", "FRESH");
    }
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

  // If this renderer is called repeatedly, keep the hysteresis state.
  // (We store it on `container`, not inside DOM.)
  const isStale = !!container.__rtWpsdIsStale;

  container.innerHTML = `
    <div class="rt-wpsd" data-rt-last-update-ms="${lastUpdateMs ?? ""}">
      <div class="rt-wpsd-hd">
        <div class="rt-wpsd-title">WPSD RF</div>
        <div class="rt-wpsd-meta">
          <span class="rt-subtle">Age:</span> <span data-rt-age-text>${ageTxt}</span>
          <span data-rt-stale-badge style="margin-left:10px;">${isStale ? pillHtml("warn", "STALE") : pillHtml("ok", "FRESH")}</span>
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
    </div>
  `;

  // Apply stale class immediately based on current hysteresis state
  container.classList.toggle("stale", !!container.__rtWpsdIsStale);

  startAgeTicker(container);
}
