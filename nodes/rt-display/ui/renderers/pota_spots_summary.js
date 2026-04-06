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
  if (score >= 75) {
    return { icon: "↑", label: "Very High", cls: "rt-pressure-very-high" };
  }
  if (score >= 50) {
    return { icon: "↗", label: "High", cls: "rt-pressure-high" };
  }
  if (score >= 25) {
    return { icon: "→", label: "Medium", cls: "rt-pressure-medium" };
  }
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

        // 3 kHz neighborhood works well enough as an SSB crowding proxy.
        if (Math.abs(otherFreq - freq) <= 3000) {
          neighbors += 1;
        }
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
  const id = item?.member || item?.spot_id;
  if (id !== undefined && id !== null && String(id).trim()) return String(id).trim();

  const call = String(item?.call || "").trim();
  const freq = String(item?.freq_hz || "").trim();
  const park = String(item?.park_ref || "").trim();
  const ts = String(item?.spot_ts_epoch || "").trim();

  return `${call}|${freq}|${park}|${ts}`;
}

function getBandKey(context) {
  return String(context?.selected_band || "").trim().toLowerCase() || "";
}

function makeEmptyBandSession() {
  return {
    disabledSpotIds: Object.create(null),
    workedSpotIds: Object.create(null),
  };
}

function getBandSession(model, bandKey) {
  if (!model.bandSessions[bandKey]) {
    model.bandSessions[bandKey] = makeEmptyBandSession();
  }
  return model.bandSessions[bandKey];
}

function isSpotDisabled(model, bandKey, item) {
  if (!bandKey) return false;
  const session = getBandSession(model, bandKey);
  return !!session.disabledSpotIds[getSpotId(item)];
}

function isSpotWorked(model, bandKey, item) {
  if (!bandKey) return false;
  const session = getBandSession(model, bandKey);
  return !!session.workedSpotIds[getSpotId(item)];
}

function markSpotDisabled(model, bandKey, item) {
  if (!bandKey) return;
  const session = getBandSession(model, bandKey);
  session.disabledSpotIds[getSpotId(item)] = true;
}

function markSpotWorked(model, bandKey, item) {
  if (!bandKey) return;
  const session = getBandSession(model, bandKey);
  session.workedSpotIds[getSpotId(item)] = true;
}

function clearDisabledForBand(model, bandKey) {
  if (!bandKey) return;
  const session = getBandSession(model, bandKey);
  session.disabledSpotIds = Object.create(null);
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
      modalOpen: false,
      modalSpotId: null,
      modalCursor: 1,
      modalCleanup: null,
      bandSessions: Object.create(null),
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

function getRenderableSpots(rawSpots, model, context) {
  const bandKey = getBandKey(context);
  const inList = Array.isArray(rawSpots) ? rawSpots.filter(Boolean) : [];

  const filtered = inList
    .filter((item) => !isSpotWorked(model, bandKey, item))
    .map((item) => ({
      ...item,
      __spotId: getSpotId(item),
      __disabled: isSpotDisabled(model, bandKey, item),
    }));

  return annotatePileupPressure(filtered);
}

function emitIntent(slot, intent, params) {
  if (!slot) return;
  slot.dispatchEvent(new CustomEvent("rt-emit-intent", {
    bubbles: true,
    detail: {
      intent,
      params: params || {},
    },
  }));
}

function emitLogQsoForSpot(container, item) {
  const slot = container.closest(".rt-slot");
  if (!slot || !item) return false;

  const context = container.__rtPotaSpotsContext || {};
  const selectedRefs = Array.isArray(context?.selected_park_refs)
    ? context.selected_park_refs
    : [];

  const mode = normalizeTuneMode(
    item?.mode || "SSB",
    item?.freq_hz,
    context?.selected_band
  );

  console.log("EMIT LOG QSO", {
    call: String(item?.call || "").trim(),
    freq_hz: Number(item?.freq_hz || 0),
    band: String(context?.selected_band || "").trim(),
    rawMode: String(item?.mode || ""),
    normalizedMode: mode,
    park_ref: String(item?.park_ref || "").trim(),
    their_pota_ref: String(item?.park_ref || "").trim(),
    my_pota_refs: selectedRefs,
  });

  emitIntent(slot, "radio.log_qso", {
    call: String(item?.call || "").trim(),
    freq_hz: Number(item?.freq_hz || 0),
    band: String(context?.selected_band || "").trim(),
    mode,
    park_ref: String(item?.park_ref || "").trim(),
    their_pota_ref: String(item?.park_ref || "").trim(),
    my_pota_refs: selectedRefs,
  });

  return true;
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
  if (item.__disabled) return false;

  const m = getModel(container);
  const spotId = getSpotId(item);
  const fingerprint = `${spotId}|${freq_hz}|${band}|${mode}`;

  if (!force && m.lastTuneFingerprint === fingerprint) {
    return false;
  }

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
    if (item && !item.__disabled) return i;
  }

  return -1;
}

function findFirstSelectableIndex(list) {
  const total = Array.isArray(list) ? list.length : 0;
  for (let i = 0; i < total; i++) {
    if (list[i] && !list[i].__disabled) return i;
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
    ? `No selectable spots for ${esc(selectedBand)}.`
    : "No selectable spots.";

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
      </div>
    ` : "";

    container.innerHTML = reminderOnlyHtml || `
      <div class="muted">${noSpotsText}</div>
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
    const mode = String(item?.mode || "SSB").trim();
    const age = ageText(item);
    const pressureIcon = String(item?.__pressureIcon || "-");
    const pressureLabelText = String(item?.__pressureLabel || "Unknown");
    const pressureClass = String(item?.__pressureClass || "");
    const isCursor = absoluteIndex === m.cursor;
    const disabled = !!item?.__disabled;

    const trClass = [
      "sev-ok",
      browseMode && isCursor ? "rt-selected" : "",
      disabled ? "rt-disabled" : "",
    ].filter(Boolean).join(" ");

    const callMarkup = disabled
      ? `<span class="rt-disabled-text">${esc(call)}</span>`
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

  const modalOptions = [
    { key: "cannot_hear", label: "Cannot Hear Station" },
    { key: "success", label: "Successful QSO" },
    { key: "heard_not_worked", label: "Heard but Could Not Work" },
  ];

  const modalHtml = m.modalOpen ? `
    <div class="rt-pota-modal-backdrop">
      <div class="rt-pota-modal" role="dialog" aria-modal="true" aria-labelledby="rt-pota-modal-title">
        <div class="rt-pota-modal-title" id="rt-pota-modal-title">Station Outcome</div>
        <div class="rt-pota-modal-body">
          ${modalOptions.map((opt, idx) => `
            <div class="rt-pota-modal-option ${m.modalCursor === idx ? "rt-pota-modal-option-selected" : ""}">
              <span class="rt-pota-modal-key">${idx + 1}</span>
              <span>${esc(opt.label)}</span>
            </div>
          `).join("")}
          <div class="rt-pota-modal-hint">Browse to choose • ENTER accept • ESC cancel</div>
        </div>
      </div>
    </div>
  ` : "";

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
      .rt-disabled {
        opacity: 0.45;
      }
      .rt-disabled .rt-disabled-text {
        text-decoration: line-through;
      }
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
      .rt-pota-modal-backdrop {
        position: absolute;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 20;
      }
      .rt-pota-modal {
        min-width: 320px;
        max-width: 92%;
        background: #111;
        color: #eee;
        border: 1px solid rgba(255,255,255,0.20);
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.40);
        padding: 12px 14px;
      }
      .rt-pota-modal-title {
        font-weight: 700;
        margin-bottom: 8px;
      }
      .rt-pota-modal-option {
        padding: 8px 8px;
        margin: 4px 0;
        border-radius: 6px;
        border: 2px solid transparent;
      }
      .rt-pota-modal-option-selected {
        background: #dff6ff;
        color: #000;
        border: 2px solid #38bdf8;
        box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.35);
        font-weight: 700;
      }
      .rt-pota-modal-key {
        display: inline-block;
        min-width: 20px;
        font-weight: 700;
      }
      .rt-pota-modal-hint {
        margin-top: 10px;
        opacity: 0.85;
        font-size: 0.92em;
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
      ${modalHtml}
    </div>
  `;
}

function closeOutcomeModal(container) {
  const m = getModel(container);
  m.modalOpen = false;
  m.modalSpotId = null;
  m.modalCursor = 1;

  if (typeof m.modalCleanup === "function") {
    try {
      m.modalCleanup();
    } catch {
      // ignore cleanup errors
    }
  }
  m.modalCleanup = null;
}

function rerenderFromModel(container) {
  const m = getModel(container);
  const context = container.__rtPotaSpotsContext || {};
  const list = getRenderableSpots(m.lastRawList, m, context);

  m.lastList = list;
  syncSelectedSpotId(m, list);
  renderSpotsWindow(container, list, m, context);
}

function advanceAfterAction(container) {
  const m = getModel(container);
  const list = Array.isArray(m.lastList) ? m.lastList : [];
  const total = list.length;

  if (total <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedSpotId = null;
    renderSpotsWindow(container, [], m, container.__rtPotaSpotsContext || {});
    return;
  }

  const current = clamp(m.cursor ?? 0, 0, total - 1);
  let next = -1;

  for (let i = current; i < total; i++) {
    const item = list[i];
    if (item && !item.__disabled) {
      next = i;
      break;
    }
  }

  if (next < 0) {
    for (let i = 0; i < current; i++) {
      const item = list[i];
      if (item && !item.__disabled) {
        next = i;
        break;
      }
    }
  }

  if (next < 0) {
    m.selectedSpotId = null;
    m.cursor = 0;
    m.offset = 0;
    renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});
    return;
  }

  m.cursor = next;
  m.selectedSpotId = getSpotId(list[next]);
  ensureCursorInWindow(m, list.length);
  renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});
  emitTuneForSpot(container, list[next], { force: true });
}

function applyOutcome(container, outcome) {
  const m = getModel(container);
  const context = container.__rtPotaSpotsContext || {};
  const bandKey = getBandKey(context);
  const spotId = String(m.modalSpotId || "").trim();

  const currentList = Array.isArray(m.lastList) ? m.lastList : [];
  const current = currentList.find((x) => x && getSpotId(x) === spotId);

  if (!current || !bandKey) {
    closeOutcomeModal(container);
    returnToBrowseMode(container);
    rerenderFromModel(container);
    return;
  }

  if (outcome === "cannot_hear") {
    markSpotDisabled(m, bandKey, current);
    closeOutcomeModal(container);
    returnToBrowseMode(container);
    rerenderFromModel(container);
    advanceAfterAction(container);
    return;
  }

  if (outcome === "success") {
    console.log("SUCCESS OUTCOME", current);
    emitLogQsoForSpot(container, current);
    markSpotWorked(m, bandKey, current);
    closeOutcomeModal(container);
    returnToBrowseMode(container);
    rerenderFromModel(container);
    advanceAfterAction(container);
    return;
  }

  if (outcome === "heard_not_worked") {
    closeOutcomeModal(container);
    returnToBrowseMode(container);
    rerenderFromModel(container);
    return;
  }

  closeOutcomeModal(container);
  returnToBrowseMode(container);
  rerenderFromModel(container);
}

function openOutcomeModal(container, item) {
  const m = getModel(container);
  if (!item || item.__disabled) return;

  closeOutcomeModal(container);

  m.modalOpen = true;
  m.modalSpotId = getSpotId(item);
  m.modalCursor = 1;
  renderSpotsWindow(container, m.lastList, m, container.__rtPotaSpotsContext || {});

  m.modalCleanup = null;
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

    if (m.modalOpen) {
      const dir = delta > 0 ? 1 : -1;
      const optionCount = 3;
      m.modalCursor = (m.modalCursor + dir + optionCount) % optionCount;
      renderSpotsWindow(container, m.lastList, m, container.__rtPotaSpotsContext || {});
      return;
    }

    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    const moved = moveCursor(m, list, delta > 0 ? 1 : -1);
    renderSpotsWindow(container, list, m, container.__rtPotaSpotsContext || {});

    const cur = list[m.cursor];
    if (cur && !cur.__disabled) {
      emitTuneForSpot(container, cur, { force: moved });
    }
  };

  const onOk = () => {
    const m = getModel(container);

    if (m.modalOpen) {
      if (m.modalCursor === 0) {
        applyOutcome(container, "cannot_hear");
        return;
      }
      if (m.modalCursor === 1) {
        applyOutcome(container, "success");
        return;
      }
      if (m.modalCursor === 2) {
        applyOutcome(container, "heard_not_worked");
        return;
      }
      return;
    }

    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = list[m.cursor];
    if (!cur || cur.__disabled) return;

    openOutcomeModal(container, cur);
  };

  const onCancel = () => {
    const m = getModel(container);
    if (!m.modalOpen) return;
    closeOutcomeModal(container);
    returnToBrowseMode(container);
    renderSpotsWindow(container, m.lastList, m, container.__rtPotaSpotsContext || {});
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);
  slot.addEventListener("rt-browse-cancel", onCancel);
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
  const context = data?.context || {};
  const m = getModel(container);

  const oldBandKey = String(m.lastBandKey || "");
  const newBandKey = getBandKey(context);

  container.__rtPotaSpotsContext = context;

  if (oldBandKey && newBandKey && oldBandKey !== newBandKey) {
    clearDisabledForBand(m, oldBandKey);
    m.pendingBandTune = true;
    closeOutcomeModal(container);
  }

  m.lastBandKey = newBandKey;
  m.lastRawList = Array.isArray(spotsRaw) ? spotsRaw.filter(Boolean).slice() : [];

  const spots = getRenderableSpots(m.lastRawList, m, context);
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
    return;
  }
}