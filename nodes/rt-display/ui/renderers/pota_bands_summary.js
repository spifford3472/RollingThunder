// pota_bands_summary.js
//
// Browse-capable POTA SSB bands panel.
// Renderer-only.
// Authoritative selected band comes from data.context.selected_band.

const WINDOW = 8;

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

function getModel(container) {
  if (!container.__rtPotaBandsModel) {
    container.__rtPotaBandsModel = {
      cursor: 0,
      offset: 0,
      lastKey: "",
      lastList: [],
    };
  }
  return container.__rtPotaBandsModel;
}

function computeStableKey(list) {
  return list.map((x) => bandName(x)).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function syncCursorToSelectedBand(m, bands, selectedBand) {
  if (!selectedBand || !Array.isArray(bands) || bands.length <= 0) return;

  const idx = bands.findIndex((x) => bandName(x) === selectedBand);
  if (idx >= 0) {
    m.cursor = idx;
    ensureCursorInWindow(m, bands.length);
  }
}

function applyProjectedBrowseCursorToBands(data, bands, m) {
  const browse = data?.ui_browse || data?.__ui?.browse || null;
  if (!browse || typeof browse !== "object") return;
  if (String(browse.panel || "") !== "pota_bands_summary") return;

  const idx = Number(browse.selected_index);
  if (!Number.isFinite(idx)) return;

  if (!Array.isArray(bands) || bands.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    return;
  }

  m.cursor = clamp(idx, 0, Math.max(0, bands.length - 1));
  ensureCursorInWindow(m, bands.length);
}

function renderBandsWindow(container, list, m, selectedBandFromContext) {
  const total = list.length;

  if (total === 0) {
    container.innerHTML = `<div class="muted">No POTA SSB bands available.</div>`;
    return;
  }

  ensureCursorInWindow(m, total);

  const off = m.offset;
  const view = list.slice(off, off + WINDOW);

  const slot = container.closest(".rt-slot");
  const browseMode = !!(slot && slot.classList.contains("rt-browse-mode"));

  const activeBand = String(selectedBandFromContext || "").trim();

  const rows = view.map((item, i) => {
    const absoluteIndex = off + i;
    const band = bandName(item);
    const count = Number(item?.count || 0);

    const isCursor = absoluteIndex === m.cursor;
    const isActiveBand = activeBand && band === activeBand;

    /*
      Important:
      - In browse mode, rt-selected follows the cursor.
      - Outside browse mode, rt-selected follows selected_band from context.
      - rt-pota-band-selected always marks the authoritative selected band.
    */
    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
      !browseMode && isActiveBand ? "rt-selected" : "",
      isActiveBand ? "rt-pota-band-selected" : "",
    ].filter(Boolean).join(" ");

    const labelHtml = isActiveBand
      ? `<span class="rt-pota-band-label"><strong>📡 ${esc(band)}</strong></span>`
      : `<span>${esc(band)}</span>`;

    return `
      <tr class="${trClass}" data-band="${esc(band)}">
        <td>${labelHtml}</td>
        <td>${esc(String(count))}</td>
      </tr>
    `;
  }).join("");

  let footerLeft = `Cursor ${clamp(m.cursor, 0, Math.max(0, total - 1)) + 1}/${total}`;

  if (activeBand) {
    footerLeft += ` • Active band: ${esc(activeBand)}`;
  }

  const hint = total > WINDOW
    ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>`
    : "";

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Band</th><th>Spots</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="rt-footer">
      <span class="rt-muted">${footerLeft}</span>${hint}
    </div>
  `;
}

function attachBrowseModeObserverOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;
  if (slot.__rtPotaBandsBrowseObserverAttached) return;
  slot.__rtPotaBandsBrowseObserverAttached = true;

  const obs = new MutationObserver(() => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const selectedBandFromContext = container.__rtPotaBandsSelectedBandFromContext || "";
    renderBandsWindow(container, list, m, selectedBandFromContext);
  });

  obs.observe(slot, {
    attributes: true,
    attributeFilter: ["class"],
  });

  slot.__rtPotaBandsBrowseObserver = obs;
}

function attachBrowseHandlersOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;
  if (slot.__rtPotaBandsBrowseAttached) return;
  slot.__rtPotaBandsBrowseAttached = true;

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);
    ensureCursorInWindow(m, total);

    const selectedBandFromContext = container.__rtPotaBandsSelectedBandFromContext || "";
    renderBandsWindow(container, list, m, selectedBandFromContext);
  };

  const onOk = () => {
    slot.dispatchEvent(new CustomEvent("rt-emit-intent", {
      bubbles: true,
      detail: {
        intent: "ui.ok",
        params: {},
      },
    }));
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);
}

export function renderPotaBandsSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);
  attachBrowseModeObserverOnce(container);

  const bandsRaw = data?.bands;
  const context = data?.context || {};

  const bands = Array.isArray(bandsRaw)
    ? bandsRaw.filter(Boolean).slice().sort((a, b) => {
        const [am, as] = bandSortKey(a);
        const [bm, bs] = bandSortKey(b);
        if (am !== bm) return am - bm;
        return as.localeCompare(bs);
      })
    : [];

  const selectedBandFromContext = String(context?.selected_band || "").trim();
  container.__rtPotaBandsSelectedBandFromContext = selectedBandFromContext;

  const m = getModel(container);

  const key = computeStableKey(bands);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;
    m.cursor = 0;
  }

  m.lastList = bands;

  const browse = data?.ui_browse || data?.__ui?.browse || null;
  const browsingThisPanel =
    browse &&
    typeof browse === "object" &&
    String(browse.panel || "") === "pota_bands_summary";

  if (browsingThisPanel) {
    applyProjectedBrowseCursorToBands(data, bands, m);
  } else {
    syncCursorToSelectedBand(m, bands, selectedBandFromContext);
  }

  if (bands.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
  } else {
    ensureCursorInWindow(m, bands.length);
  }

  renderBandsWindow(container, bands, m, selectedBandFromContext);
}