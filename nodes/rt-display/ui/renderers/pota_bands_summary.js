// pota_bands_summary.js
//
// Browse-capable POTA SSB bands panel.
// Reads:
//   - data.bands   <- rt:pota:ui:ssb:bands
//   - data.context <- rt:pota:context
//
// Emits on OK:
//   - rt-emit-intent { type:"pota.select_band", band:"20m" }
//   - rt-request-focus { panelId:"pota_spots_summary" }

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

function getModel(container) {
  if (!container.__rtPotaBandsModel) {
    container.__rtPotaBandsModel = {
      cursor: 0,
      offset: 0,
      selectedBand: null,
      lastKey: "",
      lastList: [],
    };
  }
  return container.__rtPotaBandsModel;
}

function computeStableKey(list) {
  return list.map(x => String(x?.band || "")).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
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

  const rows = view.map((item, i) => {
    const absoluteIndex = off + i;
    const band = String(item?.band || "").trim();
    const count = Number(item?.count || 0);

    const isCursor = absoluteIndex === m.cursor;
    const isSelectedBand = band === String(selectedBandFromContext || "");

    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
      !browseMode && isSelectedBand ? "rt-selected" : "",
    ].filter(Boolean).join(" ");

    return `
      <tr class="${trClass}" data-band="${esc(band)}">
        <td><strong>${esc(band)}</strong></td>
        <td>${esc(String(count))}</td>
      </tr>
    `;
  }).join("");

  //Readd once working, and remove the method directly below
  //let footerLeft = `Showing ${Math.min(WINDOW, total)}/${total}`;
  //if (browseMode) {
  //  footerLeft = `Selected Band #${clamp(m.cursor, 0, total - 1) + 1} of ${total}`;
  //} else if (selectedBandFromContext) {
  //  footerLeft = `Active band: ${esc(selectedBandFromContext)}`;
  //}

  //Temp: show cursor position even when not in browse mode, to help debugging and because it can be useful info for users too
  let footerLeft = `Cursor ${clamp(m.cursor, 0, Math.max(0, total - 1)) + 1}/${total}`;

  if (!browseMode && selectedBandFromContext) {
    footerLeft += ` • Active band: ${esc(selectedBandFromContext)}`;
  }

  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

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

function attachBrowseHandlersOnce(container) {
  const slot = container.closest(".rt-slot");
  const targets = [container, slot].filter(Boolean);

  if (container.__rtPotaBandsBrowseAttached) return;
  container.__rtPotaBandsBrowseAttached = true;

  const onDelta = (ev) => {
    console.log("[pota_bands] rt-browse-delta", ev?.detail);

    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);
    const cur = list[m.cursor];
    m.selectedBand = cur ? String(cur?.band || "") : null;

    ensureCursorInWindow(m, total);

    const selectedBandFromContext = container.__rtPotaBandsSelectedBandFromContext || "";
    renderBandsWindow(container, list, m, selectedBandFromContext);
  };

  const onOk = () => {
    console.log("[pota_bands] rt-browse-ok");

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = list[m.cursor];
    const band = String(cur?.band || "").trim();
    if (!band) return;

    (slot || container).dispatchEvent(new CustomEvent("rt-emit-intent", {
      bubbles: true,
      detail: {
        type: "pota.select_band",
        band
      }
    }));
  };

  for (const t of targets) {
    t.addEventListener("rt-browse-delta", onDelta);
    t.addEventListener("rt-browse-ok", onOk);
  }
}

export function renderPotaBandsSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);

  const bandsRaw = data?.bands;
  const context = data?.context || {};

  const bands = Array.isArray(bandsRaw)
    ? bandsRaw.filter(Boolean).slice().sort((a, b) =>
        String(a?.band || "").localeCompare(String(b?.band || ""))
      )
    : [];

  const selectedBandFromContext = String(context?.selected_band || "").trim();
  container.__rtPotaBandsSelectedBandFromContext = selectedBandFromContext;

  const m = getModel(container);

  const key = computeStableKey(bands);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;

    if (selectedBandFromContext) {
      const idx = bands.findIndex(x => String(x?.band || "") === selectedBandFromContext);
      m.cursor = idx >= 0 ? idx : 0;
      m.selectedBand = idx >= 0 ? selectedBandFromContext : (bands[0] ? String(bands[0].band || "") : null);
    } else if (m.selectedBand) {
      const idx = bands.findIndex(x => String(x?.band || "") === String(m.selectedBand || ""));
      m.cursor = idx >= 0 ? idx : 0;
    } else {
      m.cursor = 0;
      m.selectedBand = bands[0] ? String(bands[0].band || "") : null;
    }
  } else {
    if (selectedBandFromContext) {
      const idx = bands.findIndex(x => String(x?.band || "") === selectedBandFromContext);
      if (idx >= 0) {
        m.cursor = idx;
        m.selectedBand = selectedBandFromContext;
      }
    }
  }

  m.lastList = bands;

  if (bands.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedBand = null;
  } else {
    ensureCursorInWindow(m, bands.length);
    const cur = bands[m.cursor];
    m.selectedBand = cur ? String(cur?.band || "") : null;
  }

  renderBandsWindow(container, bands, m, selectedBandFromContext);
}