// hf_bands_summary.js
// PURE RENDERER — displays controller/projector-owned HF band model only.
// No browser-owned decisions, no Redis writes, no intent execution.

const DEFAULT_WINDOW = 8;

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
  if (Array.isArray(obj.bands)) return obj.bands;
  if (Array.isArray(obj.rows)) return obj.rows;

  return [];
}

function getBrowse(data) {
  return unwrapObject(data?.ui_browse || data?.__ui?.browse || {});
}

function bandId(item) {
  return String(item?.id || item?.band || item?.label || item?.name || "").trim();
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

export function renderHfBandsSummary(container, panel, data) {
  const bandsModel = unwrapObject(data?.bands);
  const context = unwrapObject(data?.context);
  const browse = getBrowse(data);

  const items = unwrapItems(data?.bands);
  const total = items.length;

  if (total === 0) {
    container.innerHTML = `<div class="rt-muted">No HF bands</div>`;
    container.__rtHfBandsInit = false;
    return;
  }

  const selectedId = String(
    context?.selected_band ||
    bandsModel?.selected_id ||
    ""
  ).trim();

  let selectedIndex = Number.isFinite(Number(browse?.selected_index))
    ? Number(browse.selected_index)
    : items.findIndex((item) => bandId(item) === selectedId);

  if (!Number.isFinite(selectedIndex) || selectedIndex < 0) selectedIndex = 0;
  selectedIndex = clamp(selectedIndex, 0, Math.max(0, total - 1));

  const modelWindowStart = Number.isFinite(Number(browse?.window_start))
    ? Number(browse.window_start)
    : Number.isFinite(Number(bandsModel?.window_start))
      ? Number(bandsModel.window_start)
      : 0;

  const modelWindowSize = Number.isFinite(Number(browse?.window_size))
    ? Number(browse.window_size)
    : Number.isFinite(Number(bandsModel?.window_size))
      ? Number(bandsModel.window_size)
      : DEFAULT_WINDOW;

  const windowSize = clamp(
    Math.floor(Number(modelWindowSize) || DEFAULT_WINDOW),
    1,
    DEFAULT_WINDOW
  );

  let windowStart = clamp(
    Math.floor(Number(modelWindowStart) || 0),
    0,
    Math.max(0, total - windowSize)
  );

  if (selectedIndex < windowStart) windowStart = selectedIndex;
  if (selectedIndex >= windowStart + windowSize) {
    windowStart = selectedIndex - windowSize + 1;
  }

  windowStart = clamp(windowStart, 0, Math.max(0, total - windowSize));

  const visible = items.slice(windowStart, windowStart + windowSize);
  const browseActive = !!browse?.active;

  if (!container.__rtHfBandsInit) {
    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Band</th>
            <th>Spots</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="rt-footer">
        <span class="rt-muted"></span>
      </div>
    `;
    container.__rtHfBandsInit = true;
  }

  const tbody = container.querySelector("tbody");
  const footer = container.querySelector(".rt-footer .rt-muted");

  while (tbody.children.length < visible.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <span style="display:flex; align-items:center;">
          <span class="rt-icon" style="width:1.6em; text-align:center;"></span>
          <strong class="rt-band"></strong>
        </span>
      </td>
      <td class="rt-count"></td>
    `;
    tbody.appendChild(tr);
  }

  while (tbody.children.length > visible.length) {
    tbody.removeChild(tbody.lastChild);
  }

  visible.forEach((item, i) => {
    const tr = tbody.children[i];
    const absoluteIndex = windowStart + i;

    const id = bandId(item);
    const label = String(item?.label || id || "-");
    const count = Number(item?.count || 0);

    const isCursor = browseActive && absoluteIndex === selectedIndex;
    const isSelectedBand = selectedId && id === selectedId;

    tr.className = [
      "sev-ok",
      isCursor ? "rt-selected" : "",
      isSelectedBand ? "rt-pota-band-selected" : "",
    ].filter(Boolean).join(" ");

    tr.querySelector(".rt-icon").innerHTML = isSelectedBand ? "▶" : "&nbsp;";
    tr.querySelector(".rt-band").textContent = label;
    tr.querySelector(".rt-count").textContent = String(count);
  });

  footer.textContent = browseActive
    ? `Cursor ${selectedIndex + 1}/${total}`
    : selectedId
      ? `Selected band: ${selectedId}`
      : `Bands: ${total}`;
}