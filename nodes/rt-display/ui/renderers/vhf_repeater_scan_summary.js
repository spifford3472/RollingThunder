// vhf_repeater_scan_summary.js
// PURE RENDERER — displays controller/projector-owned VHF models only.
// No Redis access, no SQLite reads, no repeater filtering/sorting, no distance calculation,
// no radio control, no intent execution.

const DISPLAY_CAP = 5;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

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

  if (Array.isArray(value)) return value;
  if (Array.isArray(obj.items)) return obj.items;
  if (Array.isArray(obj.repeaters)) return obj.repeaters;
  if (Array.isArray(obj.rows)) return obj.rows;
  if (Array.isArray(obj.nearby)) return obj.nearby;

  return [];
}

function text(value, fallback = "-") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

function statusText(model, fallback = "unknown") {
  return text(model?.status || model?.state || model?.availability, fallback);
}

function radioAvailableLabel(radio) {
  const status = String(radio?.status || "").trim();
  const reason = text(radio?.reason || radio?.message, "");

  if (!Object.keys(radio || {}).length) return "VHF radio status unknown";
  if (status) return reason ? `${status} — ${reason}` : status;

  return reason || "VHF radio status unknown";
}

function repeaterName(item) {
  return text(
    item?.callsign ||
      item?.call ||
      item?.name ||
      item?.label ||
      item?.repeater ||
      item?.id,
    "Repeater"
  );
}

function repeaterFrequency(item) {
  const direct = text(item?.frequency || item?.freq || item?.frequency_mhz, "");
  if (direct) return direct;

  const hz = Number(item?.freq_hz || item?.rx_freq_hz || item?.output_freq_hz || 0);
  if (Number.isFinite(hz) && hz > 0) return (hz / 1000000).toFixed(5);

  const mhz = Number(item?.freq_mhz || item?.rx_freq_mhz || item?.output_freq_mhz || 0);
  if (Number.isFinite(mhz) && mhz > 0) return mhz.toFixed(5);

  return "-";
}

function repeaterMeta(item) {
  const parts = [];

  const offset = text(item?.offset || item?.offset_mhz || item?.shift, "");
  const tone = text(item?.tone || item?.ctcss || item?.pl || item?.tone_hz, "");
  const mode = text(item?.mode || item?.band || item?.service, "");

  if (offset) parts.push(`Offset ${offset}`);
  if (tone) parts.push(`Tone ${tone}`);
  if (mode) parts.push(mode);

  return parts.join(" • ") || "-";
}

function repeaterRange(item) {
  const dist = text(item?.distance_miles, "");
  const bearing = text(item?.bearing || item?.cardinal || item?.bearing_cardinal, "");

  if (dist && bearing) return `${dist} mi ${bearing}`;
  if (dist) return `${dist} mi`;
  if (bearing) return bearing;

  return "-";
}

function skywarnText(item) {
  const raw =
    item?.skywarn ??
    item?.skywarn_flag ??
    item?.is_skywarn ??
    item?.weather_net;

  if (raw === true || raw === 1 || raw === "1" || String(raw).toLowerCase() === "true") {
    return "SkyWarn";
  }

  return "";
}

function activeMemoryText(model) {
  if (!Object.keys(model || {}).length) return "No active memory model yet";

  const channel = text(model?.channel || model?.memory || model?.slot || model?.id, "");
  const label = text(model?.label || model?.name || model?.callsign || model?.call, "");
  const freq = text(model?.frequency || model?.freq || model?.frequency_mhz, "");

  const head = [channel, label].filter(Boolean).join(" • ");
  const tail = freq ? ` ${freq}` : "";

  return (head || statusText(model, "Active memory model present")) + tail;
}

function scanText(model) {
  if (!Object.keys(model || {}).length) return "Scan manager not active";

  const status = statusText(model, "");
  const mode = text(model?.mode || model?.scan_mode || model?.profile, "");
  const message = text(model?.reason || model?.message, "");

  return [status, mode, message].filter(Boolean).join(" • ") || "Scan model present";
}

export function renderVhfRepeaterScanSummary(container, panel, data) {
  const radio = unwrapObject(data?.radio);
  const nearby = unwrapObject(data?.nearby);
  const scan = unwrapObject(data?.scan);
  const activeMemory = unwrapObject(data?.active_memory);

  const items = unwrapItems(data?.nearby);
  const visible = items.slice(0, DISPLAY_CAP);

  const nearbyStatus = statusText(nearby, items.length ? "ok" : "unknown");
  const radius = text(nearby?.radius_miles, "");
  const gpsStatus = text(nearby?.gps_status, "");

  const count =
    Number.isFinite(Number(nearby?.count)) ? Number(nearby.count) :
    Number.isFinite(Number(nearby?.total)) ? Number(nearby.total) :
    items.length;

  const rows = visible.map((item) => {
    const sky = skywarnText(item);

    return `
      <tr>
        <td style="font-size:1.25rem;"><strong>${esc(repeaterName(item))}</strong></td>
        <td style="font-size:1.25rem; font-weight:800;">${esc(repeaterFrequency(item))}</td>
        <td style="font-size:1.05rem;">${esc(repeaterMeta(item))}</td>
        <td style="font-size:1.1rem; font-weight:750;">${esc(repeaterRange(item))}</td>
        <td style="font-size:1.0rem;">${sky ? `<span class="rt-muted">${esc(sky)}</span>` : ""}</td>
      </tr>
    `;
  }).join("");

  const listHtml = visible.length
    ? `
      <table style="font-size:1.15rem;">
        <thead>
          <tr>
            <th style="font-size:1.05rem;">Repeater</th>
            <th style="font-size:1.05rem;">Freq</th>
            <th style="font-size:1.05rem;">Access</th>
            <th style="font-size:1.05rem;">Range</th>
            <th style="font-size:1.05rem;">Flag</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `
    : `<div class="rt-muted" style="font-size:1.45rem;">No nearby repeater model rows available.</div>`;

  const footerBits = [];
  footerBits.push(`Nearby: ${count}`);
  if (radius) footerBits.push(`Radius ${radius} mi`);
  if (gpsStatus) footerBits.push(`GPS ${gpsStatus}`);
  footerBits.push(`Model ${nearbyStatus}`);

  container.innerHTML = `
    <div style="
      height:100%;
      box-sizing:border-box;
      display:grid;
      grid-template-rows:auto auto 1fr auto;
      gap:.65rem;
      overflow:hidden;
    ">
      <div style="
        display:grid;
        grid-template-columns:1fr 1fr;
        gap:.7rem;
        min-height:0;
      ">
        <div style="
          border:1px solid rgba(255,255,255,.10);
          border-radius:14px;
          padding:.7rem .8rem;
          background:rgba(255,255,255,.035);
          overflow:hidden;
        ">
          <div class="rt-muted" style="font-size:1.1rem;">VHF Radio</div>
          <div style="font-size:1.45rem; line-height:1.12; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            ${esc(radioAvailableLabel(radio))}
          </div>
        </div>

        <div style="
          border:1px solid rgba(255,255,255,.10);
          border-radius:14px;
          padding:.7rem .8rem;
          background:rgba(255,255,255,.035);
          overflow:hidden;
        ">
          <div class="rt-muted" style="font-size:1.1rem;">Scan</div>
          <div style="font-size:1.45rem; line-height:1.12; font-weight:850; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            ${esc(scanText(scan))}
          </div>
        </div>
      </div>

      <div style="
        border:1px solid rgba(255,255,255,.10);
        border-radius:14px;
        padding:.65rem .8rem;
        background:rgba(255,255,255,.025);
        overflow:hidden;
        font-size:1.35rem;
        line-height:1.15;
      ">
        <span class="rt-muted">Active Memory</span>
        <span style="font-weight:850;"> ${esc(activeMemoryText(activeMemory))}</span>
      </div>

      <div style="min-height:0; overflow:hidden;">
        ${listHtml}
      </div>

      <div class="rt-footer">
        <span class="rt-muted" style="font-size:1.15rem;">${esc(footerBits.join(" • "))}</span>
      </div>
    </div>
  `;
}
