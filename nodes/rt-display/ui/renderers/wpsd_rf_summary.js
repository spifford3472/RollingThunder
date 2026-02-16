// wpsd_rf_summary.js
//
// Split TS1/TS2 RF summary from rt:wpsd:rf:last_call.
// Pure render: assumes controller already normalized fields.

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
  return `<span class="${cls}">${label}</span>`;
}

function callStatusPill(tsObj) {
  const st = String(tsObj?.status || "").toLowerCase();
  if (st === "tx") return pillHtml("ok", "TX");
  if (st === "rx") return pillHtml("ok", "RX");
  if (st === "idle") return pillHtml("warn", "IDLE");
  if (!st) return pillHtml("warn", "—");
  return pillHtml("warn", st.slice(0, 5).toUpperCase());
}

function splitTarget(target) {
  // target can be:
  // - object: { tg, label }
  // - string: "TG 3100 (BM: TG 3100 USA Bridge)"
  if (target && typeof target === "object") {
    const tg = target.tg != null ? `TG ${target.tg}` : "";
    const label = target.label ? String(target.label) : "";
    return { line1: tg || "—", line2: label || "" };
  }
  const s = String(target || "").trim();
  if (!s) return { line1: "—", line2: "" };

  // try to break "(...)" to line2
  const m = s.match(/^([^()]+)\(([^)]+)\)\s*$/);
  if (m) return { line1: m[1].trim(), line2: m[2].trim() };

  return { line1: s, line2: "" };
}

function renderTimeslotCard(slotLabel, tsObj) {
  if (!tsObj) {
    return `
      <div class="rt-ts-card">
        <div class="rt-ts-head">
          <div class="rt-ts-title">${safeText(slotLabel)}</div>
          <div class="rt-ts-pill">${pillHtml("warn", "IDLE")}</div>
        </div>
        <div class="rt-muted">Idle</div>
      </div>
    `;
  }

  const cs = String(tsObj.callsign || "").trim();
  const qrz = tsObj.qrz_url ? String(tsObj.qrz_url) : (cs ? `https://www.qrz.com/db/${encodeURIComponent(cs)}` : null);
  const callsignHtml = cs
    ? `<a href="${safeText(qrz)}" target="_blank" rel="noopener noreferrer">${safeText(cs)}</a>`
    : "—";

  const country = safeText(tsObj.country || "—");
  const flagUrl = tsObj.flag_url ? String(tsObj.flag_url) : null;
  const flagHtml = flagUrl
    ? `<img class="rt-flag" src="${safeText(flagUrl)}" alt="" />`
    : "";

  const name = safeText(tsObj.name || "—");
  const loc = safeText(tsObj.location || "");
  const mode = safeText(tsObj.mode || "—");
  const src = safeText(tsObj.src || "—");
  const dur = (tsObj.dur_s != null) ? `${safeText(tsObj.dur_s)}s` : "—";

  const tgt = splitTarget(tsObj.target);
  const tgt1 = safeText(tgt.line1);
  const tgt2 = safeText(tgt.line2);

  return `
    <div class="rt-ts-card">
      <div class="rt-ts-head">
        <div class="rt-ts-title">${safeText(slotLabel)}</div>
        <div class="rt-ts-pill">${callStatusPill(tsObj)}</div>
      </div>

      <div class="rt-ts-callsign">${callsignHtml}</div>

      <div class="rt-ts-meta">
        ${flagHtml}
        <span class="rt-ts-country">${country}</span>
      </div>

      <div class="rt-ts-line"><span class="k">Name</span><span class="v">${name}</span></div>
      ${loc ? `<div class="rt-ts-line"><span class="k">Loc</span><span class="v">${loc}</span></div>` : ""}

      <div class="rt-ts-line"><span class="k">Mode</span><span class="v">${mode}</span></div>
      <div class="rt-ts-line"><span class="k">Target</span><span class="v">
        <div>${tgt1}</div>
        ${tgt2 ? `<div class="rt-muted">${tgt2}</div>` : ""}
      </span></div>

      <div class="rt-ts-line"><span class="k">Src</span><span class="v">${src}</span></div>
      <div class="rt-ts-line"><span class="k">Dur</span><span class="v">${dur}</span></div>
    </div>
  `;
}

export function renderWpsdRfSummary(container, panel, data) {
  const obj = data?.last_call || null;
  const ts1 = obj?.ts1 || null;
  const ts2 = obj?.ts2 || null;

  container.innerHTML = `
    <div class="rt-ts-split">
      ${renderTimeslotCard("TS1", ts1)}
      ${renderTimeslotCard("TS2", ts2)}
    </div>
  `;
}
