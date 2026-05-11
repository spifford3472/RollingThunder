// hf_spots_summary.js
// PURE RENDERER — displays controller/projector-owned HF spot model only.
// No browser-owned decisions, no Redis writes, no intent execution.

const DEFAULT_WINDOW = 10;
const DISPLAY_CAP = 8;

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
  if (Array.isArray(obj.spots)) return obj.spots;
  if (Array.isArray(obj.rows)) return obj.rows;

  return [];
}

function getBrowse(data) {
  return unwrapObject(data?.ui_browse || data?.__ui?.browse || {});
}

function mhz(item) {
  if (item?.freq) return String(item.freq);

  const hz = Number(item?.freq_hz || item?.frequency || 0);
  if (!Number.isFinite(hz) || hz <= 0) return "-";

  return (hz / 1000000).toFixed(3);
}

function rowClassForStyle(style) {
  const s = String(style || "").trim();

  if (s === "worked") return "rt-spot-worked";
  if (s === "heard_not_worked") return "rt-spot-heard-not-worked";
  if (s === "cannot_hear") return "rt-spot-cannot-hear";

  return "";
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

export function renderHfSpotsSummary(container, panel, data) {
  const spotsModel = unwrapObject(data?.spots);
  const context = unwrapObject(data?.context);
  const browse = getBrowse(data);

  const items = unwrapItems(data?.spots);
  const total = items.length;

  if (total === 0) {
    container.innerHTML = `<div class="rt-muted">No HF spots</div>`;
    container.__rtHfSpotsInit = false;
    return;
  }

  const selectedId = String(
    context?.selected_spot_id ||
    spotsModel?.selected_id ||
    ""
  ).trim();

  let selectedIndex = Number.isFinite(Number(browse?.selected_index))
    ? Number(browse.selected_index)
    : items.findIndex((item) => String(item?.id || "") === selectedId);

  if (!Number.isFinite(selectedIndex) || selectedIndex < 0) selectedIndex = 0;
  selectedIndex = clamp(selectedIndex, 0, Math.max(0, total - 1));

  const modelWindowStart = Number.isFinite(Number(browse?.window_start))
    ? Number(browse.window_start)
    : Number.isFinite(Number(spotsModel?.window_start))
      ? Number(spotsModel.window_start)
      : 0;

  const modelWindowSize = Number.isFinite(Number(browse?.window_size))
    ? Number(browse.window_size)
    : Number.isFinite(Number(spotsModel?.window_size))
      ? Number(spotsModel.window_size)
      : DEFAULT_WINDOW;

  const windowSize = clamp(
    Math.floor(Number(modelWindowSize) || DEFAULT_WINDOW),
    1,
    DISPLAY_CAP
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

  if (!container.__rtHfSpotsInit) {
    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Call</th>
            <th>MHz</th>
            <th>Mode</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="rt-footer">
        <span class="rt-muted"></span>
      </div>
    `;
    container.__rtHfSpotsInit = true;
  }

  const tbody = container.querySelector("tbody");
  const footer = container.querySelector(".rt-footer .rt-muted");

  while (tbody.children.length < visible.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong class="rt-call"></strong></td>
      <td class="rt-freq"></td>
      <td class="rt-mode"></td>
      <td class="rt-status"></td>
    `;
    tbody.appendChild(tr);
  }

  while (tbody.children.length > visible.length) {
    tbody.removeChild(tbody.lastChild);
  }

  visible.forEach((item, i) => {
    const tr = tbody.children[i];
    const absoluteIndex = windowStart + i;

    const isSelected = absoluteIndex === selectedIndex;
    const call = String(item?.callsign || item?.call || "?").trim();
    const mode = String(item?.mode || "-").trim();
    const status = String(item?.status || "").trim();
    const style = String(item?.row_style || status || "").trim();

    tr.className = [
      isSelected ? "rt-selected" : "",
      rowClassForStyle(style),
    ].filter(Boolean).join(" ");

    tr.querySelector(".rt-call").textContent = call;
    tr.querySelector(".rt-freq").textContent = mhz(item);
    tr.querySelector(".rt-mode").textContent = mode;
    tr.querySelector(".rt-status").textContent = status || "-";
  });

  footer.textContent =
    `${selectedIndex + 1}/${total} • showing ${windowStart + 1}-${Math.min(windowStart + windowSize, total)}`;
}