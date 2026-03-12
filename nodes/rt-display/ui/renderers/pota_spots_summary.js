// pota_spots_summary.js

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

function getModel(container) {
  if (!container.__rtPotaSpotsModel) {
    container.__rtPotaSpotsModel = {
      cursor: 0,
      offset: 0,
      selectedSpotId: null,
      lastKey: "",
      lastList: [],
    };
  }
  return container.__rtPotaSpotsModel;
}

function computeStableKey(list) {
  return list.map(x => String(x?.member || x?.spot_id || `${x?.call}:${x?.freq_hz}:${x?.park_ref}`)).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function renderSpotsWindow(container, list, m, context) {
  const total = list.length;
  const selectedBand = String(context?.selected_band || "").trim();

  if (total === 0) {
    container.innerHTML = `
      <div class="muted">No spots${selectedBand ? ` for ${esc(selectedBand)}` : ""}.</div>
    `;
    return;
  }

  ensureCursorInWindow(m, total);
  const off = m.offset;
  const view = list.slice(off, off + WINDOW);

  const slot = container.closest(".rt-slot");
  const browseMode = !!(slot && slot.classList.contains("rt-browse-mode"));

  const rows = view.map((item, i) => {
    const absoluteIndex = off + i;
    const call = String(item?.call || "").trim() || "?";
    const parkRef = String(item?.park_ref || "").trim() || "-";
    const freq = mhz(item?.freq_hz);
    const age = ageText(item);
    const mode = String(item?.mode || "SSB").trim();

    const isCursor = absoluteIndex === m.cursor;

    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
    ].filter(Boolean).join(" ");

    return `
      <tr class="${trClass}">
        <td><strong>${esc(call)}</strong></td>
        <td>${esc(freq)}</td>
        <td>${esc(parkRef)}</td>
        <td>${esc(mode)}</td>
        <td>${esc(age)}</td>
      </tr>
    `;
  }).join("");

  let footerLeft = `Showing ${Math.min(WINDOW, total)}/${total}`;
  if (browseMode) {
    footerLeft = `Cursor ${clamp(m.cursor, 0, total - 1) + 1}/${total}`;
  } else if (selectedBand) {
    footerLeft = `Band: ${esc(selectedBand)} • ${total} spots`;
  }

  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Call</th><th>MHz</th><th>Park</th><th>Mode</th><th>Age</th></tr>
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
  if (slot.__rtPotaSpotsBrowseAttached) return;
  slot.__rtPotaSpotsBrowseAttached = true;

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);
    const cur = list[m.cursor];
    m.selectedSpotId = cur ? String(cur?.member || cur?.spot_id || "") : null;

    ensureCursorInWindow(m, total);
    renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});
  };

  const onOk = () => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = list[m.cursor];
    if (!cur) return;

    const freq_hz = Number(cur?.freq_hz || 0);
    const band = String(container.__rtPotaSpotsContext?.selected_band || "").trim();
    const mode = String(cur?.mode || "SSB").trim();

    if (!Number.isFinite(freq_hz) || freq_hz <= 0) return;
    if (!band) return;
    if (!mode) return;

    console.log("[pota_spots] emitting radio.tune", { freq_hz, band, mode });

    slot.dispatchEvent(new CustomEvent("rt-emit-intent", {
      bubbles: true,
      detail: {
        intent: "radio.tune",
        params: {
          freq_hz,
          band,
          mode,
          autotune: true
        }
      }
    }));
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);
}

function attachBrowseModeObserverOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;
  if (slot.__rtPotaSpotsBrowseObserverAttached) return;
  slot.__rtPotaSpotsBrowseObserverAttached = true;

  const obs = new MutationObserver(() => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});
  });

  obs.observe(slot, {
    attributes: true,
    attributeFilter: ["class"],
  });

  slot.__rtPotaSpotsBrowseObserver = obs;
}

export function renderPotaSpotsSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);
  attachBrowseModeObserverOnce(container);

  const spotsRaw = data?.spots;
  const context = data?.context || {};
  container.__rtPotaSpotsContext = context;

  const spots = Array.isArray(spotsRaw) ? spotsRaw.filter(Boolean).slice() : [];
  const m = getModel(container);

  const key = computeStableKey(spots);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;

    if (m.selectedSpotId) {
      const idx = spots.findIndex(x => String(x?.member || x?.spot_id || "") === String(m.selectedSpotId));
      m.cursor = idx >= 0 ? idx : 0;
    } else {
      m.cursor = 0;
    }
  }

  m.lastList = spots;

  if (spots.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedSpotId = null;
  } else {
    ensureCursorInWindow(m, spots.length);
    const cur = spots[m.cursor];
    m.selectedSpotId = cur ? String(cur?.member || cur?.spot_id || "") : null;
  }

  renderSpotsWindow(container, spots, m, context);
}