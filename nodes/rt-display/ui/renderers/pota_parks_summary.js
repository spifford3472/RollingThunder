// pota_parks_summary.js

const WINDOW = 6;

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

function applyProjectedBrowseCursor(container, list, m, browse, expectedPanelId) {
  const slot = container.closest(".rt-slot");
  const browseMode = !!(slot && slot.classList.contains("rt-browse-mode"));
  if (!browseMode) return;

  if (!browse || typeof browse !== "object") return;
  if (String(browse.panel || "") !== expectedPanelId) return;

  const idx = Number(browse.selected_index);
  if (!Number.isFinite(idx)) return;

  m.cursor = clamp(idx, 0, Math.max(0, list.length - 1));
}

function getModel(container) {
  if (!container.__rtPotaParksModel) {
    container.__rtPotaParksModel = {
      cursor: 0,
      offset: 0,
      selectedRef: null,
      lastKey: "",
      lastList: [],
    };
  }
  return container.__rtPotaParksModel;
}

function computeStableKey(list) {
  return list.map(x => String(x?.reference || "")).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function renderParksWindow(container, list, m, context) {
  const total = list.length;
  const selectedRefs = Array.isArray(context?.selected_park_refs)
    ? context.selected_park_refs.map(x => String(x || "").trim()).filter(Boolean)
    : [];
  const selectedRef = String(context?.selected_park_ref || "").trim();

  if (total === 0) {
    container.innerHTML = `<div class="muted">No nearby parks.</div>`;
    return;
  }

  ensureCursorInWindow(m, total);
  const off = m.offset;
  const view = list.slice(off, off + WINDOW);

  const slot = container.closest(".rt-slot");
  const browseMode = !!(slot && slot.classList.contains("rt-browse-mode"));

  const rows = view.map((item, i) => {
    const absoluteIndex = off + i;
    const ref = String(item?.reference || "").trim();
    const name = String(item?.name || "").trim() || "(unnamed)";
    const synthetic = !!item?.synthetic;
    const dist = item?.distance_miles == null ? "" : `${Number(item.distance_miles).toFixed(1)} mi`;

    const isCursor = absoluteIndex === m.cursor;
    const isSelected = selectedRefs.includes(ref);

    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
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

  let footerLeft = `Showing ${Math.min(WINDOW, total)}/${total}`;
  if (browseMode) {
    footerLeft = `Cursor ${clamp(m.cursor, 0, total - 1) + 1}/${total}`;
  } else if (selectedRefs.length > 0) {
    footerLeft = `Selected parks: ${esc(selectedRefs.join(", "))}`;
  } else if (selectedRef) {
    footerLeft = `Selected park: ${esc(selectedRef)}`;
  } else {
    footerLeft = `Selected park: Not in a park`;
  }

  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Park</th><th>Ref</th><th>Dist</th></tr>
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
  if (!slot) return;
  if (slot.__rtPotaParksBrowseAttached) return;
  slot.__rtPotaParksBrowseAttached = true;

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);
    const cur = list[m.cursor];
    m.selectedRef = cur ? String(cur?.reference || "") : null;

    ensureCursorInWindow(m, total);
    renderParksWindow(container, list, m, container.__rtPotaParksContext || {});
  };

  const onOk = () => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = list[m.cursor];
    const park_ref = String(cur?.reference || "").trim();

    console.log("[pota_parks] emitting pota.select_park", { reference: park_ref });

    slot.dispatchEvent(new CustomEvent("rt-emit-intent", {
      bubbles: true,
      detail: {
        intent: "pota.select_park",
        params: { reference: park_ref }
      }
    }));
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);
}

function attachBrowseModeObserverOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;
  if (slot.__rtPotaParksBrowseObserverAttached) return;
  slot.__rtPotaParksBrowseObserverAttached = true;

  const obs = new MutationObserver(() => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    renderParksWindow(container, list, m, container.__rtPotaParksContext || {});
  });

  obs.observe(slot, {
    attributes: true,
    attributeFilter: ["class"],
  });

  slot.__rtPotaParksBrowseObserver = obs;
}

export function renderPotaParksSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);
  attachBrowseModeObserverOnce(container);

  const context = data?.context || {};
  const nearby = data?.nearby || {};
  container.__rtPotaParksContext = context;

  const choices = Array.isArray(nearby?.choices) ? nearby.choices.filter(Boolean).slice() : [];
  const m = getModel(container);

  const key = computeStableKey(choices);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;

    const selectedRef = String(context?.selected_park_ref || "").trim();
    if (selectedRef) {
      const idx = choices.findIndex(x => String(x?.reference || "").trim() === selectedRef);
      m.cursor = idx >= 0 ? idx : 0;
      m.selectedRef = selectedRef;
    } else {
      m.cursor = 0;
      m.selectedRef = "";
    }
  }

  m.lastList = choices;
  const browse = data?.ui_browse || null;
  applyProjectedBrowseCursor(container, choices, m, browse, "pota_parks_summary");

  if (choices.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedRef = null;
  } else {
    ensureCursorInWindow(m, choices.length);
    const cur = choices[m.cursor];
    m.selectedRef = cur ? String(cur?.reference || "") : null;
  }

  renderParksWindow(container, choices, m, context);
}