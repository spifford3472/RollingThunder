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

function returnToBrowseMode(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;
  slot.classList.add("rt-browse-mode");
}

function dedupeSpots(items) {
  const seen = new Set();
  const out = [];

  for (const item of Array.isArray(items) ? items : []) {
    if (!item) continue;
    const id = getSpotId(item);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(item);
  }

  return out;
}

function normalizeProjectedPageContext(raw) {
  if (!raw) return {};

  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  if (typeof raw === "object") {
    return raw;
  }

  return {};
}

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

function spotAgeSec(item) {
  const ts = Number(item?.spot_ts_epoch || 0);
  if (!Number.isFinite(ts) || ts <= 0) return 999999;
  return Math.max(0, Math.floor(Date.now() / 1000) - ts);
}

function pileupAgeScore(ageSec) {
  if (ageSec <= 60) return 35;
  if (ageSec <= 180) return 28;
  if (ageSec <= 300) return 20;
  if (ageSec <= 600) return 10;
  return 2;
}

function pileupRepeatCallScore(callCount) {
  if (callCount >= 4) return 30;
  if (callCount === 3) return 22;
  if (callCount === 2) return 12;
  return 0;
}

function pileupCrowdingScore(neighborCount) {
  if (neighborCount >= 4) return 25;
  if (neighborCount === 3) return 18;
  if (neighborCount === 2) return 10;
  if (neighborCount === 1) return 4;
  return 0;
}

function pressureLabel(score) {
  if (score >= 75) return { icon: "↑", label: "Very High", cls: "rt-pressure-very-high" };
  if (score >= 50) return { icon: "↗", label: "High", cls: "rt-pressure-high" };
  if (score >= 25) return { icon: "→", label: "Medium", cls: "rt-pressure-medium" };
  return { icon: "↓", label: "Low", cls: "rt-pressure-low" };
}

function annotatePileupPressure(list) {
  const items = Array.isArray(list) ? list.slice() : [];
  const callCounts = Object.create(null);

  for (const item of items) {
    const call = String(item?.call || "").trim().toUpperCase();
    if (!call) continue;
    callCounts[call] = (callCounts[call] || 0) + 1;
  }

  return items.map((item) => {
    const ageSec = spotAgeSec(item);
    const call = String(item?.call || "").trim().toUpperCase();
    const freq = Number(item?.freq_hz || 0);

    let neighbors = 0;
    if (Number.isFinite(freq) && freq > 0) {
      for (const other of items) {
        if (other === item) continue;
        const otherFreq = Number(other?.freq_hz || 0);
        if (!Number.isFinite(otherFreq) || otherFreq <= 0) continue;
        if (Math.abs(otherFreq - freq) <= 3000) neighbors += 1;
      }
    }

    const score =
      pileupAgeScore(ageSec) +
      pileupRepeatCallScore(callCounts[call] || 0) +
      pileupCrowdingScore(neighbors);

    const pressure = pressureLabel(score);

    return {
      ...item,
      __pressureScore: score,
      __pressureIcon: pressure.icon,
      __pressureLabel: pressure.label,
      __pressureClass: pressure.cls,
    };
  });
}

function getSpotId(item) {
  const explicitId = item?.spot_id ?? item?.id;
  if (explicitId !== undefined && explicitId !== null && String(explicitId).trim()) {
    return String(explicitId).trim();
  }

  const call = String(item?.callsign || item?.call || "").trim();
  const park = String(item?.park_ref || item?.reference || "").trim();
  const freq = String(item?.freq_hz ?? item?.frequency ?? "").trim();

  if (call || park || freq) {
    return `${call}|${park}|${freq}`.replace(/^\|+|\|+$/g, "");
  }

  return "";
}

function getBandKey(context) {
  return String(context?.selected_band || context?.band || "").trim().toLowerCase() || "";
}

function getSpotFreqHz(item) {
  const raw = item?.freq_hz ?? item?.frequency ?? 0;
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function sortSpotsByFrequency(items) {
  return [...items].sort((a, b) => {
    const fa = getSpotFreqHz(a);
    const fb = getSpotFreqHz(b);
    if (fa !== fb) return fa - fb;

    const ca = String(a?.call || a?.callsign || "").trim();
    const cb = String(b?.call || b?.callsign || "").trim();
    const callCmp = ca.localeCompare(cb);
    if (callCmp !== 0) return callCmp;

    const pa = String(a?.park_ref || a?.reference || "").trim();
    const pb = String(b?.park_ref || b?.reference || "").trim();
    return pa.localeCompare(pb);
  });
}

function getProjectedSpotStatus(context, item) {
  const statuses = context?.spot_statuses;
  if (!statuses || typeof statuses !== "object") return null;

  const id = getSpotId(item);
  const entry = statuses[id];

  if (typeof entry === "string") return entry || null;
  if (entry && typeof entry === "object") {
    const status = String(entry.status || "").trim();
    return status || null;
  }

  return null;
}

function computeStableKey(list) {
  return list.map((x) => getSpotId(x)).join("|");
}

function getModel(container) {
  if (!container.__rtPotaSpotsModel) {
    container.__rtPotaSpotsModel = {
      cursor: 0,
      offset: 0,
      selectedSpotId: null,
      lastKey: "",
      lastRawList: [],
      lastList: [],
      lastBandKey: "",
      lastTunedSpotId: "",
      lastTuneFingerprint: "",
      pendingBandTune: false,
      bandReminderOpen: false,
      bandReminderText: "",
      bandReminderTimer: null,
    };
  }
  return container.__rtPotaSpotsModel;
}

function ensureCursorInWindow(m, total) {
  if (total <= 0) {
    m.cursor = 0;
    m.offset = 0;
    return;
  }

  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function radioHasTuner() {
  try {
    const cfg =
      window?.RT_APP_CONFIG ||
      window?.__RT_APP_CONFIG ||
      window?.RollingThunderConfig ||
      {};
    return cfg?.globals?.radio?.has_tuner ?? false;
  } catch {
    return false;
  }
}

function normalizeTuneMode(rawMode, freqHz, band) {
  const mode = String(rawMode || "").trim().toUpperCase();
  const freq = Number(freqHz || 0);
  const b = String(band || "").trim().toLowerCase();

  if (mode === "SSB") {
    if (Number.isFinite(freq) && freq > 0) {
      return freq < 10_000_000 ? "LSB" : "USB";
    }
    if (["160m", "80m", "60m", "40m"].includes(b)) return "LSB";
    return "USB";
  }

  if (mode === "LSB" || mode === "USB" || mode === "CW" || mode === "AM" || mode === "FM" || mode === "DIGI") {
    return mode;
  }

  return mode || "USB";
}

function getRenderableSpots(rawSpots, context) {
  const inList = Array.isArray(rawSpots) ? rawSpots.filter(Boolean) : [];
  const deduped = dedupeSpots(inList);
  const sorted = sortSpotsByFrequency(deduped);

  const withStatus = sorted.map((item) => ({
    ...item,
    __spotId: getSpotId(item),
    __status: getProjectedSpotStatus(context, item),
  }));

  return annotatePileupPressure(withStatus);
}

function emitIntent(slot, intent, params) {
  if (!slot) return;
  slot.dispatchEvent(new CustomEvent("rt-emit-intent", {
    bubbles: true,
    detail: { intent, params: params || {} },
  }));
}

function emitTuneForSpot(container, item, { force = false } = {}) {
  const slot = container.closest(".rt-slot");
  if (!slot || !item) return false;

  const context = container.__rtPotaSpotsContext || {};
  const band = String(context?.selected_band || "").trim();
  const freq_hz = Number(item?.freq_hz || 0);
  const mode = normalizeTuneMode(item?.mode || "SSB", freq_hz, band);

  if (!Number.isFinite(freq_hz) || freq_hz <= 0) return false;
  if (!band) return false;
  if (!mode) return false;

  const m = getModel(container);
  const spotId = getSpotId(item);
  const fingerprint = `${spotId}|${freq_hz}|${band}|${mode}`;

  if (!force && m.lastTuneFingerprint === fingerprint) return false;

  m.lastTunedSpotId = spotId;
  m.lastTuneFingerprint = fingerprint;

  emitIntent(slot, "radio.tune", {
    freq_hz,
    band,
    mode,
    autotune: true,
  });

  return true;
}

function emitTunerActionForBand(container, band) {
  const slot = container.closest(".rt-slot");
  if (!slot || !band) return;

  if (radioHasTuner()) {
    emitIntent(slot, "radio.atas_tune", { band });
  } else {
    showBandReminder(container, band);
  }
}

function findNextSelectableIndex(list, startIndex, direction) {
  const total = Array.isArray(list) ? list.length : 0;
  if (total <= 0) return -1;

  const dir = direction >= 0 ? 1 : -1;
  let i = clamp(startIndex, 0, total - 1);

  for (let step = 0; step < total; step++) {
    i = (i + dir + total) % total;
    const item = list[i];
    if (item) return i;
  }

  return -1;
}

function findFirstSelectableIndex(list) {
  const total = Array.isArray(list) ? list.length : 0;
  for (let i = 0; i < total; i++) {
    if (list[i]) return i;
  }
  return -1;
}

function syncSelectedSpotId(model, list) {
  if (!Array.isArray(list) || list.length <= 0) {
    model.cursor = 0;
    model.offset = 0;
    model.selectedSpotId = null;
    return;
  }

  if (model.selectedSpotId) {
    const idx = list.findIndex((x) => x && getSpotId(x) === String(model.selectedSpotId));
    if (idx >= 0) {
      model.cursor = idx;
      ensureCursorInWindow(model, list.length);
      model.selectedSpotId = getSpotId(list[model.cursor]);
      return;
    }
  }

  const firstSelectable = findFirstSelectableIndex(list);
  model.cursor = firstSelectable >= 0 ? firstSelectable : 0;
  ensureCursorInWindow(model, list.length);
  model.selectedSpotId = getSpotId(list[model.cursor]);
}

function moveCursor(model, list, direction) {
  const total = Array.isArray(list) ? list.length : 0;
  if (total <= 0) return false;

  const current = clamp(model.cursor ?? 0, 0, total - 1);
  const next = findNextSelectableIndex(list, current, direction);

  if (next < 0) return false;
  if (next === current) {
    model.selectedSpotId = getSpotId(list[next]);
    ensureCursorInWindow(model, total);
    return false;
  }

  model.cursor = next;
  model.selectedSpotId = getSpotId(list[next]);
  ensureCursorInWindow(model, total);
  return true;
}

function clearBandReminder(container) {
  const m = getModel(container);
  m.bandReminderOpen = false;
  m.bandReminderText = "";

  if (m.bandReminderTimer) {
    clearTimeout(m.bandReminderTimer);
    m.bandReminderTimer = null;
  }
}

function showBandReminder(container, band) {
  const m = getModel(container);

  clearBandReminder(container);

  m.bandReminderOpen = true;
  m.bandReminderText = band
    ? `Band changed to ${band}. Tune the antenna now.`
    : "Band changed. Tune the antenna now.";

  renderSpotsWindow(
    container,
    Array.isArray(m.lastList) ? m.lastList : [],
    m,
    container.__rtPotaSpotsContext || {}
  );

  m.bandReminderTimer = setTimeout(() => {
    m.bandReminderOpen = false;
    m.bandReminderText = "";
    m.bandReminderTimer = null;
    renderSpotsWindow(
      container,
      Array.isArray(m.lastList) ? m.lastList : [],
      m,
      container.__rtPotaSpotsContext || {}
    );
  }, 5000);
}

function renderSpotsWindow(container, list, m, context) {
  const total = Array.isArray(list) ? list.length : 0;
  const selectedBand = String(context?.selected_band || "").trim();
  const noSpotsText = selectedBand
    ? `No spots for ${esc(selectedBand)}.`
    : "No spots.";

  if (total === 0) {
    const reminderOnlyHtml = m.bandReminderOpen ? `
      <style>
        .rt-pota-band-reminder-backdrop {
          position: absolute;
          inset: 0;
          background: rgba(0, 0, 0, 0.45);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 18;
        }
        .rt-pota-band-reminder {
          min-width: 280px;
          max-width: 90%;
          background: #1a1a1a;
          color: #f5f5f5;
          border: 1px solid rgba(255,255,255,0.20);
          border-radius: 8px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.40);
          padding: 14px 16px;
          text-align: center;
        }
        .rt-pota-band-reminder-title {
          font-weight: 700;
          margin-bottom: 8px;
        }
        .rt-pota-band-reminder-body {
          font-size: 1rem;
        }
      </style>
      <div style="position: relative;">
        <div class="muted">${noSpotsText}</div>
        <div class="rt-pota-band-reminder-backdrop">
          <div class="rt-pota-band-reminder" role="alert" aria-live="assertive">
            <div class="rt-pota-band-reminder-title">Antenna Tune Reminder</div>
            <div class="rt-pota-band-reminder-body">${esc(m.bandReminderText || "Tune the antenna now.")}</div>
          </div>
        </div>
        <div class="rt-muted" style="font-size:0.75rem; margin-top:4px;">${esc(debugStatusSummary)}</div>
      </div>
    ` : "";
//    const debugStatusSummary = view.map((x) => `${getSpotId(x)} => ${x.__status || "-"}`).join(" | ");
//    container.innerHTML = reminderOnlyHtml || `<div class="muted">${noSpotsText}</div>`;
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
    const mode = String(item?.mode || "SSB").trim();
    const age = ageText(item);
    const pressureIcon = String(item?.__pressureIcon || "-");
    const pressureLabelText = String(item?.__pressureLabel || "Unknown");
    const pressureClass = String(item?.__pressureClass || "");
    const isCursor = absoluteIndex === m.cursor;
    const status = String(item?.__status || "").trim();
    const isCannotHear = status === "cannot_hear";
    const isWorked = status === "worked";
    const isHeardNotWorked = status === "heard_not_worked";

    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
      isCannotHear ? "rt-status-cannot-hear" : "",
      isWorked ? "rt-status-worked" : "",
      isHeardNotWorked ? "rt-status-heard-not-worked" : "",
    ].filter(Boolean).join(" ");

    const callMarkup = isCannotHear
      ? `<strong class="rt-status-cannot-hear-text">${esc(call)}</strong>`
      : isWorked
        ? `<strong class="rt-status-worked-text">${esc(call)}</strong>`
        : isHeardNotWorked
          ? `<strong class="rt-status-heard-not-worked-text">${esc(call)}</strong>`
          : `<strong>${esc(call)}</strong>`;

    return `
      <tr class="${trClass}" data-spot-id="${esc(getSpotId(item))}">
        <td>${callMarkup}</td>
        <td>${esc(freq)}</td>
        <td>${esc(parkRef)}</td>
        <td>${esc(mode)}</td>
        <td><span class="rt-pressure-pill ${pressureClass}" title="${esc(pressureLabelText)}">${esc(pressureIcon)}</span></td>
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

  const bandReminderHtml = m.bandReminderOpen ? `
    <div class="rt-pota-band-reminder-backdrop">
      <div class="rt-pota-band-reminder" role="alert" aria-live="assertive">
        <div class="rt-pota-band-reminder-title">Antenna Tune Reminder</div>
        <div class="rt-pota-band-reminder-body">${esc(m.bandReminderText || "Tune the antenna now.")}</div>
      </div>
    </div>
  ` : "";

  container.innerHTML = `
    <style>
      .rt-pressure-pill {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 1.8em;
        height: 1.6em;
        border-radius: 6px;
        font-weight: 900;
        font-size: 1.05em;
        border: 2px solid rgba(255,255,255,0.25);
      }
      .rt-pressure-low {
        background: rgba(34,197,94,0.15);
        color: #86efac;
      }
      .rt-pressure-medium {
        background: rgba(250,204,21,0.15);
        color: #fde68a;
      }
      .rt-pressure-high {
        background: rgba(249,115,22,0.15);
        color: #fdba74;
      }
      .rt-pressure-very-high {
        background: rgba(239,68,68,0.18);
        color: #fca5a5;
        box-shadow: 0 0 6px rgba(239,68,68,0.5);
      }
      .rt-pota-band-reminder-backdrop {
        position: absolute;
        inset: 0;
        background: rgba(0, 0, 0, 0.45);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 18;
      }
      .rt-pota-band-reminder {
        min-width: 280px;
        max-width: 90%;
        background: #1a1a1a;
        color: #f5f5f5;
        border: 1px solid rgba(255,255,255,0.20);
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.40);
        padding: 14px 16px;
        text-align: center;
      }
      .rt-pota-band-reminder-title {
        font-weight: 700;
        margin-bottom: 8px;
      }
      .rt-pota-band-reminder-body {
        font-size: 1rem;
      }
    </style>
    <div style="position: relative;">
      <table>
        <thead>
          <tr><th>Call</th><th>MHz</th><th>Park</th><th>Mode</th><th>P</th><th>Age</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="rt-footer">
        <span class="rt-muted">${footerLeft}</span>${hint}
      </div>
      ${bandReminderHtml}
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

    const moved = moveCursor(m, list, delta > 0 ? 1 : -1);
    renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});

    const cur = list[m.cursor];
    if (cur) {
      emitTuneForSpot(container, cur, { force: moved });
    }
  };

  slot.addEventListener("rt-browse-delta", onDelta);
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

function applyProjectedBrowseCursorToSpots(data, spots, m) {
  const browse = data?.ui_browse || data?.__ui?.browse || null;
  if (!browse || typeof browse !== "object") return;
  if (String(browse.panel || "") !== "pota_spots_summary") return;

  const idx = Number(browse.selected_index);
  if (!Number.isFinite(idx)) return;

  if (!Array.isArray(spots) || spots.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedSpotId = null;
    return;
  }

  m.cursor = clamp(idx, 0, Math.max(0, spots.length - 1));
  m.selectedSpotId = getSpotId(spots[m.cursor]);
  ensureCursorInWindow(m, spots.length);
}

export function renderPotaSpotsSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);
  attachBrowseModeObserverOnce(container);

  const spotsRaw = data?.spots;
  const bindings = data?.__rt?.bindings || {};

  const projectedPageContext = normalizeProjectedPageContext(
    data?.ui_page_context ??
    data?.page_context ??
    data?.__ui?.page_context ??
    data?.__ui?.pageContext ??
    data?.__rt?.page_context ??
    data?.__rt?.pageContext ??
    data?.__rt?.ui_page_context ??
    bindings["rt:ui:page_context"] ??
    bindings.page_context ??
    null
  );

  const baseContext = data?.context || {};
  const context = {
    ...baseContext,
    ...projectedPageContext,
  };

  const m = getModel(container);

  const oldBandKey = String(m.lastBandKey || "");
  const newBandKey = getBandKey(context);

  container.__rtPotaSpotsContext = context;

  if (oldBandKey && newBandKey && oldBandKey !== newBandKey) {
    m.pendingBandTune = true;
  }

  m.lastBandKey = newBandKey;
  m.lastRawList = Array.isArray(spotsRaw) ? spotsRaw.filter(Boolean).slice() : [];

  const spots = getRenderableSpots(m.lastRawList, context);
  const key = computeStableKey(spots);

  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;
  }

  m.lastList = spots;
  syncSelectedSpotId(m, spots);
  applyProjectedBrowseCursorToSpots(data, spots, m);

  renderSpotsWindow(container, spots, m, context);

  if (spots.length <= 0) {
    m.lastTuneFingerprint = "";
    return;
  }

  const cur = spots[m.cursor];
  if (!cur) return;

  if (m.pendingBandTune) {
    m.pendingBandTune = false;
    emitTunerActionForBand(container, String(context?.selected_band || "").trim());
    emitTuneForSpot(container, cur, { force: true });
  }
}