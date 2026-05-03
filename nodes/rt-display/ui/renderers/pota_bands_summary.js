// pota_bands_summary.js
// Renderer-only POTA SSB bands panel.
// Uses controller/projector-owned browse state when active.

const DEFAULT_WINDOW = 7;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function unwrapBinding(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    if (Array.isArray(value.value)) return value.value;
    if (Array.isArray(value.bands)) return value.bands;
    if (Array.isArray(value.items)) return value.items;
    if (Array.isArray(value.rows)) return value.rows;
  }
  return [];
}

function unwrapObject(value) {
  if (!value) return {};
  if (value && typeof value === "object" && value.value && typeof value.value === "object") {
    return value.value;
  }
  if (typeof value === "object") return value;
  return {};
}

function bandName(item) {
  return String(item?.band || item?.id || item?.name || item || "").trim();
}

function bandSortKey(item) {
  const raw = bandName(item).toLowerCase();
  if (!raw) return [9999, ""];

  if (raw.endsWith("m")) {
    const meters = Number.parseInt(raw.slice(0, -1), 10);
    if (Number.isFinite(meters)) return [meters, raw];
  }

  return [9999, raw];
}

function getBrowseForPanel(data, panelId) {
  const browse = unwrapObject(data?.ui_browse || data?.__ui?.browse || {});
  if (!browse?.active) return {};
  if (String(browse.panel || "") !== panelId) return {};
  return browse;
}

function getWindowState(data, panelId, total, fallbackSelectedIndex = 0) {
  const browse = getBrowseForPanel(data, panelId);

  const selected = Number.isFinite(Number(browse.selected_index))
    ? Number(browse.selected_index)
    : fallbackSelectedIndex;

  const windowSizeRaw = Number(browse.window_size);
  const windowSize = Number.isFinite(windowSizeRaw) && windowSizeRaw > 0
    ? Math.floor(windowSizeRaw)
    : DEFAULT_WINDOW;

  let windowStart = Number.isFinite(Number(browse.window_start))
    ? Number(browse.window_start)
    : 0;

  const clampedSelected = clamp(selected, 0, Math.max(0, total - 1));
  const maxStart = Math.max(0, total - windowSize);

  windowStart = clamp(windowStart, 0, maxStart);

  if (total > 0) {
    if (clampedSelected < windowStart) windowStart = clampedSelected;
    if (clampedSelected >= windowStart + windowSize) {
      windowStart = clampedSelected - windowSize + 1;
    }
  }

  windowStart = clamp(windowStart, 0, maxStart);

  return {
    browse,
    selectedIndex: clampedSelected,
    windowStart,
    windowSize,
  };
}

export function renderPotaBandsSummary(container, panel, data) {
  const bandsRaw = unwrapBinding(data?.bands);
  const context = unwrapObject(data?.context || {});

  const bands = bandsRaw
    .filter(Boolean)
    .slice()
    .sort((a, b) => {
      const [am, as] = bandSortKey(a);
      const [bm, bs] = bandSortKey(b);
      if (am !== bm) return am - bm;
      return as.localeCompare(bs);
    });

  if (bands.length === 0) {
    container.innerHTML = `<div class="muted">No POTA SSB bands available.</div>`;
    return;
  }

  const selectedBandFromContext = String(context?.selected_band || "").trim();

  const fallbackSelectedIndex = selectedBandFromContext
    ? Math.max(0, bands.findIndex((item) => bandName(item) === selectedBandFromContext))
    : 0;

  const { browse, selectedIndex, windowStart, windowSize } =
    getWindowState(data, "pota_bands_summary", bands.length, fallbackSelectedIndex);

  const view = bands.slice(windowStart, windowStart + windowSize);
  const browseActive = !!browse?.active;

  const rows = view.map((item, i) => {
    const absoluteIndex = windowStart + i;
    const band = bandName(item);
    const count = Number(item?.count || 0);

    const isCursor = browseActive && absoluteIndex === selectedIndex;
    const isActiveBand = selectedBandFromContext && band === selectedBandFromContext;

    const trClass = [
      "sev-ok",
      isCursor ? "rt-selected" : "",
      isActiveBand ? "rt-pota-band-selected" : "",
    ].filter(Boolean).join(" ");

    const icon = isActiveBand ? "▶" : "&nbsp;";

    return `
      <tr class="${trClass}">
        <td>
          <span style="display:flex; align-items:center;">
            <span style="width:1.6em; text-align:center;">${icon}</span>
            <strong>${esc(band)}</strong>
          </span>
        </td>
        <td>${esc(String(count))}</td>
      </tr>
    `;
  }).join("");

  const footerLeft = browseActive
    ? `Cursor ${selectedIndex + 1}/${bands.length}`
    : selectedBandFromContext
      ? `Selected band: ${esc(selectedBandFromContext)}`
      : `Bands: ${bands.length}`;

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Band</th><th>Spots</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="rt-footer">
      <span class="rt-muted">${footerLeft}</span>
    </div>
  `;
}