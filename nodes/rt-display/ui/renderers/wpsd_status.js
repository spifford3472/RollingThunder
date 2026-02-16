// wpsd_status.js
//
// Renders:
// - TS1 (left) + TS2 (right) slot summary
// - Recent transmissions list
// - Age ticker + stale visualization (purely presentational)

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

function fmtFreqHz(hz) {
  const n = Number(hz ?? NaN);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return `${(n / 1e6).toFixed(6)} MHz`;
}

function qrzLink(callsign) {
  const cs = String(callsign || "").trim().toUpperCase();
  if (!cs) return "—";
  const url = `https://www.qrz.com/db/${encodeURIComponent(cs)}`;
  return `<a href="${url}" target="_blank" rel="noopener noreferrer">${safeText(cs)}</a>`;
}

function slotSummary(slotNum, slot) {
  const active = !!slot?.active;
  const statePill = active ? pillHtml("bad", "ACTIVE") : pillHtml("ok", "IDLE");

  const callsign = slot?.callsign ? qrzLink(slot.callsign) : "—";
  const tg = (slot?.tg != null) ? `TG ${safeText(slot.tg)}` : "—";
  const tgName = slot?.tg_name ? safeText(slot.tg_name) : ""; // optional future field
  const tgLine = tgName ? `${tg}<div class="rt-subtle">${tgName}</div>` : tg;

  const dir = slot?.direction ? safeText(slot.direction) : "—";
  const dur = (slot?.dur_s != null) ? `${safeText(slot.dur_s)}s` : "—";
  const loss = (slot?.loss_pct != null) ? `${safeText(slot.loss_pct)}%` : "—";
  const ber = (slot?.ber != null) ? safeText(slot.ber) : "—";

  const sinceMs = slot?.since_ms ?? null;
  const lastEndMs = slot?.last_end_ms ?? null;

  const sinceAge = fmtAge(ageSecFromMs(sinceMs));
  const endAge = fmtAge(ageSecFromMs(lastEndMs));

  // If active: show "since"; else show "last"
  const timeLabel = active ? "Since" : "Last";

  return `
    <div class="rt-wpsd-slot">
      <div class="rt-wpsd-slot-hd">
        <div class="rt-wpsd-slot-title">TS${slotNum}</div>
        <div class="rt-wpsd-slot-pill">${statePill}</div>
      </div>

      <table class="rt-kv">
        <tr><td class="k">Call</td><td class="v">${callsign}</td></tr>
        <tr><td class="k">Target</td><td class="v">${tgLine}</td></tr>
        <tr><td class="k">Dir</td><td class="v">${dir}</td></tr>
        <tr><td class="k">Dur</td><td class="v">${dur}</td></tr>
        <tr><td class="k">Loss</td><td class="v">${loss}</td></tr>
        <tr><td class="k">BER</td><td class="v">${ber}</td></tr>
        <tr><td class="k">${timeLabel}</td><td class="v">${active ? sinceAge : endAge}</td></tr>
      </table>
    </div>
  `;
}

function recentRows(items) {
  const arr = Array.isArray(items) ? items : [];
  const rows = arr.slice(0, 8).map((it) => {
    const slot = it?.slot != null ? `TS${safeText(it.slot)}` : "—";
    const call = it?.callsign ? qrzLink(it.callsign) : "—";
    const tg = it?.tg != null ? `TG ${safeText(it.tg)}` : "—";
    const dur = it?.dur_s != null ? `${safeText(it.dur_s)}s` : "—";
    const loss = it?.loss_pct != null ? `${safeText(it.loss_pct)}%` : "—";
    const ber = it?.ber != null ? safeText(it.ber) : "—";
    const age = fmtAge(ageSecFromMs(it?.ts_ms));

    return `
      <tr>
        <td>${slot}</td>
        <td>${call}</td>
        <td>${tg}</td>
        <td>${dur}</td>
        <td>${loss}</td>
        <td>${ber}</td>
        <td class="rt-right">${age}</td>
      </tr>
    `;
  }).join("");

  return rows || `<tr><td colspan="7">No recent activity</td></tr>`;
}

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

    // stale threshold: presentational only
    const stale = (age != null && age > 12);
    container.classList.toggle("stale", stale);

    const badge = container.querySelector("[data-rt-stale-badge]");
    if (badge) badge.innerHTML = stale ? pillHtml("warn", "STALE") : pillHtml("ok", "FRESH");
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
                <th>BER</th>
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

  startAgeTicker(container);
}
