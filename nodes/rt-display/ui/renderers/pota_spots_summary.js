// pota_spots_summary.js
// PURE RENDERER — NO STATE, NO LOGIC

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

function mhz(freqHz) {
  const n = Number(freqHz || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  return (n / 1_000_000).toFixed(3);
}

function ageText(row) {
  const ts = Number(row?.spot_ts_epoch || 0);
  if (!Number.isFinite(ts) || ts <= 0) return "-";
  const age = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (age < 60) return `${age}s`;
  return `${Math.floor(age / 60)}m`;
}

export function renderPotaSpotsSummary(container, panel, data) {
  function spotFreqHz(item) {
    const n = Number(item?.freq_hz ?? item?.frequency ?? 0);
    return Number.isFinite(n) ? n : 0;
  }

  function spotSortKey(item) {
    const freq = spotFreqHz(item);
    const call = String(item?.callsign || item?.call || "").trim().toUpperCase();
    const park = String(item?.park_ref || item?.reference || "").trim().toUpperCase();
    return { freq, call, park };
  }

  function sortSpotsLikeController(items) {
    return [...items].sort((a, b) => {
      const aa = spotSortKey(a);
      const bb = spotSortKey(b);

      if (aa.freq !== bb.freq) return aa.freq - bb.freq;

      const callCmp = aa.call.localeCompare(bb.call);
      if (callCmp !== 0) return callCmp;

      return aa.park.localeCompare(bb.park);
    });
  }

  function buildSpotId(item) {
    const call = String(item.call || "").trim().toUpperCase();
    const park = String(item.park_ref || "").trim();
    const freq = String(item.freq_hz || "").trim();
    return `${call}|${park}|${freq}`;
  }
  
  const rawItems =
    Array.isArray(data.items) ? data.items :
    Array.isArray(data.spots) ? data.spots :
    Array.isArray(data.rows) ? data.rows :
    Array.isArray(data.value) ? data.value :
    [];

  const items = sortSpotsLikeController(rawItems);
  const browse = data.ui_browse || data.__ui?.browse || {};

  const selected = Number.isFinite(Number(data.selected_index))
    ? Number(data.selected_index)
    : Number.isFinite(Number(browse.selected_index))
      ? Number(browse.selected_index)
      : 0;

  const windowStart = Number.isFinite(Number(data.window_start))
    ? Number(data.window_start)
    : Number.isFinite(Number(browse.window_start))
      ? Number(browse.window_start)
      : 0;

  const windowSize = Number.isFinite(Number(data.window_size))
    ? Number(data.window_size)
    : Number.isFinite(Number(browse.window_size))
      ? Number(browse.window_size)
      : 8;

  const visible = items.slice(windowStart, windowStart + windowSize);
  const total = items.length;

  if (total === 0) {
    container.innerHTML = `<div class="rt-muted">No spots</div>`;
    return;
  }

  const spotStatuses =
    data.page_context?.spot_statuses ||
    data.ui_page_context?.spot_statuses ||
    data.context?.spot_statuses ||
    {};

  const rows = visible.map((item, i) => {
    const absoluteIndex = windowStart + i;
    const isSelected = absoluteIndex === selected;

    const call = String(item?.call || item?.callsign || "").trim() || "?";
    const park = String(item?.park_ref || item?.reference || "").trim() || "-";
    const freqHz = Number(item?.freq_hz ?? item?.frequency ?? 0);
    const freq = mhz(freqHz);
    const mode = String(item?.mode || "SSB").trim();
    const age = ageText(item);

    const spotId = `${call.toUpperCase()}|${park}|${String(freqHz)}`;
    const status = spotStatuses?.[spotId]?.status || null;

    const rowClasses = [];
    if (isSelected) rowClasses.push("rt-selected");
    if (status === "cannot_hear") rowClasses.push("rt-spot-cannot-hear");
    if (status === "heard_not_worked") rowClasses.push("rt-spot-heard-not-worked");
    if (status === "worked") rowClasses.push("rt-spot-worked");

    return `
      <tr class="${rowClasses.join(" ")}">
        <td><strong>${esc(call)}</strong></td>
        <td>${esc(freq)}</td>
        <td>${esc(park)}</td>
        <td>${esc(mode)}</td>
        <td>${esc(age)}</td>
      </tr>
    `;
  }).join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Call</th><th>MHz</th><th>Park</th><th>Mode</th><th>Age</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="rt-footer">
      <span class="rt-muted">
        ${selected + 1}/${total} &nbsp; • &nbsp; showing ${windowStart + 1}-${Math.min(windowStart + windowSize, total)}
      </span>
    </div>
  `;
}