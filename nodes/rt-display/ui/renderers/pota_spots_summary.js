// pota_spots_summary.js
// PURE RENDERER — NO STATE, NO LOGIC

const WINDOW = 7;

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

  const projectedWindowSize = Number.isFinite(Number(data.window_size))
    ? Number(data.window_size)
    : Number.isFinite(Number(browse.window_size))
      ? Number(browse.window_size)
      : WINDOW;

  // UI display cap only (does NOT change controller state)
  const windowSize = Math.max(1, Math.min(projectedWindowSize, WINDOW));

  let displayWindowStart = windowStart;

  if (selected < displayWindowStart) {
    displayWindowStart = selected;
  }

  if (selected >= displayWindowStart + windowSize) {
    displayWindowStart = selected - windowSize + 1;
  }

  displayWindowStart = Math.max(
    0,
    Math.min(displayWindowStart, Math.max(0, items.length - windowSize))
  );

  const visible = items.slice(displayWindowStart, displayWindowStart + windowSize);
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

  // Initialize DOM once; after that, only update rows/classes/text.
  if (!container.__rtPotaSpotsInit) {
    container.innerHTML = `
      <table>
        <thead>
          <tr><th>Call</th><th>MHz</th><th>Park</th><th>Mode</th><th>Age</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="rt-footer">
        <span class="rt-muted"></span>
      </div>
    `;
    container.__rtPotaSpotsInit = true;
  }

  const tbody = container.querySelector("tbody");
  const footer = container.querySelector(".rt-footer .rt-muted");

  // Ensure enough reusable rows.
  while (tbody.children.length < visible.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong class="rt-spot-call"></strong></td>
      <td class="rt-spot-freq"></td>
      <td class="rt-spot-park"></td>
      <td class="rt-spot-mode"></td>
      <td class="rt-spot-age"></td>
    `;
    tbody.appendChild(tr);
  }

  // Remove extra rows if the visible window shrinks.
  while (tbody.children.length > visible.length) {
    tbody.removeChild(tbody.lastChild);
  }

  // Update existing rows instead of rebuilding the table.
  visible.forEach((item, i) => {
    const tr = tbody.children[i];
    const absoluteIndex = displayWindowStart + i;
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

    tr.className = rowClasses.join(" ");

    tr.querySelector(".rt-spot-call").textContent = call;
    tr.querySelector(".rt-spot-freq").textContent = freq;
    tr.querySelector(".rt-spot-park").textContent = park;
    tr.querySelector(".rt-spot-mode").textContent = mode;
    tr.querySelector(".rt-spot-age").textContent = age;
  });

  footer.textContent =
    `${selected + 1}/${total} • showing ${displayWindowStart + 1}-${Math.min(displayWindowStart + windowSize, total)}`;
}