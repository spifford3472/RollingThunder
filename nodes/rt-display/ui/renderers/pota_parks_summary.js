// pota_parks_summary.js
// Renderer-only POTA nearby parks panel.
// Park selection and browse movement are controller-owned.

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

function unwrapObject(value) {
  if (!value) return {};
  if (value && typeof value === "object" && value.value && typeof value.value === "object") {
    return value.value;
  }
  if (typeof value === "object") return value;
  return {};
}

function parkRef(item) {
  return String(item?.reference || item?.park_ref || item?.id || "").trim();
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

function extractChoices(nearby) {
  if (Array.isArray(nearby)) return nearby.filter(Boolean).slice();

  if (nearby && typeof nearby === "object") {
    if (Array.isArray(nearby.choices)) return nearby.choices.filter(Boolean).slice();
    if (Array.isArray(nearby.parks)) return nearby.parks.filter(Boolean).slice();
    if (Array.isArray(nearby.items)) return nearby.items.filter(Boolean).slice();
    if (Array.isArray(nearby.nearby)) return nearby.nearby.filter(Boolean).slice();
    if (Array.isArray(nearby.value)) return nearby.value.filter(Boolean).slice();
  }

  return [];
}

export function renderPotaParksSummary(container, panel, data) {
  const context = unwrapObject(data?.context || {});
  const nearby = data?.nearby || {};
  const choices = extractChoices(nearby);

  const total = choices.length;
  const selectedRefs = Array.isArray(context?.selected_park_refs)
    ? context.selected_park_refs.map((x) => String(x || "").trim()).filter(Boolean)
    : [];

  const selectedRef = String(context?.selected_park_ref || "").trim();

  if (total === 0) {
    container.innerHTML = `<div class="muted">No nearby parks.</div>`;
    return;
  }

  const primarySelectedRef = selectedRefs[0] || selectedRef;
  const fallbackSelectedIndex = primarySelectedRef
    ? Math.max(0, choices.findIndex((item) => parkRef(item) === primarySelectedRef))
    : 0;

  const { browse, selectedIndex, windowStart, windowSize } =
    getWindowState(data, "pota_parks_summary", total, fallbackSelectedIndex);

  const browseActive = !!browse?.active;
  const view = choices.slice(windowStart, windowStart + windowSize);

  const rows = view.map((item, i) => {
    const absoluteIndex = windowStart + i;
    const ref = parkRef(item);
    const name = String(item?.name || item?.park_name || "").trim() || "(unnamed)";
    const synthetic = !!item?.synthetic;
    const dist = item?.distance_miles == null ? "" : `${Number(item.distance_miles).toFixed(1)} mi`;

    const isCursor = browseActive && absoluteIndex === selectedIndex;
    const isSelected = selectedRefs.includes(ref) || (!!selectedRef && ref === selectedRef);

    const trClass = [
      "sev-ok",
      isCursor ? "rt-selected" : "",
      isSelected ? "rt-pota-park-selected" : "",
    ].filter(Boolean).join(" ");

    const labelHtml = isSelected
      ? `<strong>📡 ${esc(name)}</strong>`
      : esc(name);

    const refHtml = synthetic ? "" : esc(ref);

    return `
      <tr class="${trClass}">
        <td>${labelHtml}</td>
        <td>${refHtml}</td>
        <td>${esc(dist)}</td>
      </tr>
    `;
  }).join("");

  let footerLeft = `Showing ${Math.min(windowSize, total)}/${total}`;

  if (browseActive) {
    footerLeft = `Cursor ${selectedIndex + 1}/${total}`;
  } else if (selectedRefs.length > 0) {
    footerLeft = `Selected parks: ${esc(selectedRefs.join(", "))}`;
  } else if (selectedRef) {
    footerLeft = `Selected park: ${esc(selectedRef)}`;
  } else {
    footerLeft = `Selected park: Not in a park`;
  }

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Park</th><th>Ref</th><th>Dist</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="rt-footer">
      <span class="rt-muted">${footerLeft}</span>
    </div>
  `;
}