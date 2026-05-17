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

function qsoHistoryText(historyItems) {
  if (!historyItems.length) return "FIRST QSO";
  return fmtHistoryLine(historyItems[0]) || "FIRST QSO";
}

function qthLine(qrz) {
  const address = String(qrz?.address || "").trim();
  if (address) return address;

  const city = String(qrz?.addr2 || "").trim();
  const state = String(qrz?.state || "").trim();
  const zip = String(qrz?.zip || "").trim();
  const country = String(qrz?.country || "").trim();

  const cityState = [city, state].filter(Boolean).join(", ");
  const cityStateZip = [cityState, zip].filter(Boolean).join(" ");

  return [cityStateZip, country].filter(Boolean).join(" • ");
}

export function renderHfDetailSummary(container, panel, data) {
  const qrz = unwrapObject(data?.qrz);
  const spot = unwrapObject(data?.spot);
  const historyItems = unwrapItems(data?.qso_history);

  const callsign = String(qrz?.callsign || spot?.callsign || spot?.call || "").trim();

  if (!callsign) {
    container.innerHTML = `<div class="rt-muted" style="font-size:1.25rem;">No selected callsign</div>`;
    return;
  }

  const name = String(qrz?.name || "").trim();
  const country = String(qrz?.country || "").trim();
  const grid = String(qrz?.grid || "").trim();
  const image = String(qrz?.image || "").trim();
  const licenseClass = String(qrz?.class || "").trim();
  const qslmgr = String(qrz?.qslmgr || "").trim();
  const qrzStatusText = qrzMessage(qrz);

  const freq = String(spot?.freq || "").trim();
  const mode = String(spot?.mode || "").trim();
  const band = String(spot?.band || "").trim();
  const status = String(spot?.status || "").trim();

  const subtitle =
    [name, country].filter(Boolean).join(" • ") ||
    qrzStatusText ||
    "No QRZ detail";

  const qth = qthLine(qrz);
  const lastQso = qsoHistoryText(historyItems);
  const firstQso = !historyItems.length;

  const photoHtml = image
    ? `
      <div style="
        width:96px;
        height:96px;
        border-radius:14px;
        overflow:hidden;
        flex:0 0 auto;
        background:rgba(255,255,255,.08);
      ">
        <img
          src="${esc(image)}"
          alt=""
          style="width:100%; height:100%; object-fit:cover; display:block;"
        >
      </div>
    `
    : `
      <div style="
        width:96px;
        height:96px;
        border-radius:14px;
        display:flex;
        align-items:center;
        justify-content:center;
        flex:0 0 auto;
        background:rgba(255,255,255,.08);
        font-size:2.1rem;
        font-weight:800;
        opacity:.55;
      ">
        ${esc(callsign.slice(0, 2))}
      </div>
    `;

  container.innerHTML = `
    <div class="rt-detail" style="
      height:100%;
      box-sizing:border-box;
      display:grid;
      grid-template-rows: 1fr auto;
      gap:.65rem;
      overflow:hidden;
    ">
      <div style="
        min-height:0;
        display:grid;
        grid-template-columns: 1fr 42%;
        gap:.85rem;
        overflow:hidden;
      ">
        <div style="
          min-width:0;
          min-height:0;
          display:flex;
          flex-direction:column;
          gap:.45rem;
          overflow:hidden;
        ">
          <div style="
            font-size:2.1rem;
            line-height:1.02;
            font-weight:900;
            letter-spacing:.03em;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(callsign)}</div>

          <div class="rt-muted" style="
            font-size:1.05rem;
            line-height:1.14;
            max-height:2.35em;
            overflow:hidden;
          ">${esc(subtitle)}</div>

          <div style="
            margin-top:.15rem;
            display:flex;
            gap:.45rem;
            flex-wrap:wrap;
            align-items:baseline;
            font-weight:800;
            line-height:1.05;
          ">
            <span style="font-size:1.5rem;">${esc(freq || "-")}</span>
            <span style="font-size:1.12rem;">${esc(mode || "-")}</span>
            <span style="font-size:1.12rem;">${esc(band || "-")}</span>
          </div>

          ${
            status
              ? `<div class="rt-muted" style="font-size:1rem; line-height:1.1;">Status: ${esc(status)}</div>`
              : ""
          }

          <div style="
            margin-top:.1rem;
            font-size:1.04rem;
            line-height:1.18;
            overflow:hidden;
          ">
            <div>
              <span class="rt-muted">Grid</span>
              <span style="font-weight:850;"> ${esc(grid || "-")}</span>
            </div>

            ${
              licenseClass
                ? `<div><span class="rt-muted">Class</span> <span style="font-weight:850;">${esc(licenseClass)}</span></div>`
                : ""
            }

            <div style="
              margin-top:.28rem;
              max-height:3.55em;
              overflow:hidden;
            ">
              <span class="rt-muted">QTH</span>
              <span style="font-weight:700;"> ${esc(qth || "-")}</span>
            </div>

            ${
              qslmgr
                ? `<div style="margin-top:.28rem;"><span class="rt-muted">QSL</span> <span style="font-weight:700;">${esc(qslmgr)}</span></div>`
                : ""
            }
          </div>

          ${
            qrzStatusText && qrzStatusText !== "QRZ lookup…"
              ? `<div class="rt-muted" style="
                   margin-top:auto;
                   font-size:.92rem;
                   line-height:1.08;
                   max-height:2.2em;
                   overflow:hidden;
                 ">${esc(qrzStatusText)}</div>`
              : ""
          }
        </div>

        <div style="
          min-width:0;
          min-height:0;
          height:100%;
          border-radius:16px;
          overflow:hidden;
          background:rgba(255,255,255,.08);
          display:flex;
          align-items:center;
          justify-content:center;
        ">
          ${
            image
              ? `<img
                   src="${esc(image)}"
                   alt=""
                   style="
                     width:100%;
                     height:100%;
                     object-fit:cover;
                     display:block;
                   "
                 >`
              : `<div style="
                   width:100%;
                   height:100%;
                   display:flex;
                   align-items:center;
                   justify-content:center;
                   font-size:3.2rem;
                   font-weight:900;
                   opacity:.55;
                 ">${esc(callsign.slice(0, 2))}</div>`
          }
        </div>
      </div>

      <div style="
        padding-top:.5rem;
        border-top:1px solid rgba(255,255,255,.16);
        overflow:hidden;
      ">
        <div class="rt-muted" style="
          font-size:.92rem;
          line-height:1;
          font-weight:800;
          text-transform:uppercase;
          letter-spacing:.05em;
        ">Last QSO</div>

        <div style="
          margin-top:.18rem;
          font-size:${firstQso ? "1.48rem" : "1.12rem"};
          line-height:1.12;
          font-weight:${firstQso ? "950" : "800"};
          white-space:nowrap;
          overflow:hidden;
          text-overflow:ellipsis;
        ">${esc(lastQso)}</div>
      </div>
    </div>
  `;
}