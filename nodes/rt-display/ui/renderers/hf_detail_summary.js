// hf_detail_summary.js
// PURE RENDERER — displays controller/projector-owned selected HF detail only.
// No browser-owned decisions, no Redis writes, no intent execution.

function unwrapObject(value) {
  if (!value) return {};

  if (
    typeof value === "object" &&
    value.value &&
    typeof value.value === "object" &&
    !Array.isArray(value.value)
  ) {
    return value.value;
  }

  if (typeof value === "object" && !Array.isArray(value)) return value;

  return {};
}

function unwrapItems(value) {
  const obj = unwrapObject(value);
  return Array.isArray(obj.items) ? obj.items : [];
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtHistoryLine(item) {
  const qsoUtc = String(item?.qso_utc || "").trim();
  const freq = String(item?.freq || "").trim();
  const mode = String(item?.mode || "").trim();

  let ts = qsoUtc;
  if (ts.endsWith(":00Z")) ts = ts.replace(":00Z", "Z");
  ts = ts.replace("T", " ");

  return [ts, freq, mode].filter(Boolean).join("  ");
}

function qrzMessage(qrz) {
  const status = String(qrz?.status || "").trim();

  if (status === "loading") return "QRZ lookup…";
  if (status === "not_configured") return "QRZ NOT CONFIGURED";
  if (status === "unavailable") return "QRZ NOT CURRENTLY AVAILABLE";
  if (status === "not_found") return "USER NOT IN QRZ";
  if (status === "no_callsign") return "No selected callsign";

  return String(qrz?.message || "").trim();
}

export function renderHfDetailSummary(container, panel, data) {
  const qrz = unwrapObject(data?.qrz);
  const spot = unwrapObject(data?.spot);
  const historyItems = unwrapItems(data?.qso_history);

  const callsign = String(qrz?.callsign || spot?.callsign || spot?.call || "").trim();

  if (!callsign) {
    container.innerHTML = `<div class="rt-muted">No selected callsign</div>`;
    container.__rtHfDetailInit = false;
    return;
  }

  const name = String(qrz?.name || "").trim();
  const address = String(qrz?.address || "").trim();
  const country = String(qrz?.country || "").trim();
  const grid = String(qrz?.grid || "").trim();
  const qrzStatusText = qrzMessage(qrz);

  const image = String(qrz?.image || "").trim();
  const licenseClass = String(qrz?.class || "").trim();
  const qslmgr = String(qrz?.qslmgr || "").trim();

  const freq = String(spot?.freq || "").trim();
  const mode = String(spot?.mode || "").trim();
  const band = String(spot?.band || "").trim();
  const status = String(spot?.status || "").trim();

  const subtitle =
    [name, country].filter(Boolean).join(" • ") ||
    qrzStatusText ||
    "No QRZ detail";

  const photoHtml = image
    ? `<img src="${esc(image)}" alt="" style="max-width:72px; max-height:72px; object-fit:cover; border-radius:8px; float:right; margin-left:.5rem;">`
    : "";

  container.innerHTML = `
    <div class="rt-detail">
      ${photoHtml}
      <div class="rt-hf-call" style="font-size:1.6em; font-weight:700;">${esc(callsign)}</div>
      <div class="rt-hf-subtitle rt-muted">${esc(subtitle)}</div>

      <table style="margin-top:.5rem;">
        <tbody>
          <tr><th>Freq</th><td>${esc(freq || "-")}</td></tr>
          <tr><th>Mode</th><td>${esc(mode || "-")}</td></tr>
          <tr><th>Band</th><td>${esc(band || "-")}</td></tr>
          <tr><th>Status</th><td>${esc(status || "-")}</td></tr>
          <tr><th>Grid</th><td>${esc(grid || "-")}</td></tr>
          <tr><th>QTH</th><td>${esc(address || "-")}</td></tr>
          <tr><th>Class</th><td>${esc(licenseClass || "-")}</td></tr>
          <tr><th>QSL</th><td>${esc(qslmgr || "-")}</td></tr>
        </tbody>
      </table>

      ${
        qrzStatusText && qrzStatusText !== "QRZ lookup…"
          ? `<div class="rt-muted" style="margin-top:.45rem;">${esc(qrzStatusText)}</div>`
          : ""
      }

      <div style="clear:both; margin-top:.65rem;">
        <div style="font-weight:700;">Last QSO:</div>
        <div class="rt-hf-qso-history">
          ${
            historyItems.length
              ? esc(fmtHistoryLine(historyItems[0]))
              : `<span class="rt-muted">FIRST QSO</span>`
          }
        </div>
      </div>
    </div>
  `;
}