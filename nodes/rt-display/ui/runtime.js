// runtime.js
//
// RollingThunder UI Runtime (rt-display)
//
// Controller-owned navigation version.
// The browser follows controller-projected UI state from Redis via the
// state batch API. The browser remains renderer-only.
//
// Responsibilities:
// - Load config bundle (pages + panels)
// - Build deterministic shell + slots from controller-selected page.layout
// - Mount panel renderers into slot bodies
// - Run refresh lifecycle for panels
// - Reflect controller-owned page/focus/browse/modal state
// - Emit intents only; never own UI meaning

import { createNavMachine } from "./nav_machine.js";
import { loadConfigBundle } from "./config_loader.js";
import { createRendererRegistry } from "./renderer_registry.js";
import { createBindingStore } from "./binding_store.js";
import { startPanelRefresh } from "./refresh.js";
import { renderPanelError } from "./renderers/panel_error.js";

// -----------------------------------------------------------------------------
// Overlay Asset Loader
// -----------------------------------------------------------------------------
(function loadOverlayAssets() {
  function loadCSS(href) {
    const existing = document.querySelector(`link[href="${href}"]`);
    if (existing) return;

    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  function loadJS(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing) {
        resolve();
        return;
      }

      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  loadCSS("/shared/controller-overlay.css");
  loadJS("/shared/controller-overlay.js")
    .then(() => console.log("[rt] controller overlay loaded"))
    .catch((err) => console.error("[rt] controller overlay failed to load", err));
})();

function rtShowControllerOverlay(message) {
  if (window.RTControllerOverlay) {
    window.RTControllerOverlay.show(
      message || "Lost connection to rt-controller. Reconnecting…"
    );
  }
}

function rtHideControllerOverlay() {
  if (window.RTControllerOverlay) {
    window.RTControllerOverlay.hide();
  }
}

// -----------------------------------------------------------------------------
// Runtime extension helpers
// -----------------------------------------------------------------------------

function findOwningSlot(node) {
  if (!node || !(node instanceof Element)) return null;
  return node.closest(".rt-slot");
}

function isPlainObject(x) {
  return !!x && typeof x === "object" && !Array.isArray(x);
}

function isValidBand(band) {
  return new Set([
    "160m", "80m", "60m", "40m", "30m", "20m",
    "17m", "15m", "12m", "10m", "6m", "2m", "70cm"
  ]).has(String(band || "").trim());
}

function modalIdsDiffer(a, b) {
  const aid = String(a?.id || "").trim();
  const bid = String(b?.id || "").trim();
  return aid !== bid;
}

function buildProjectedModalBodyHtml(modalObj) {
  const modalType = String(modalObj?.type || "").trim();

  if (modalType === "pota_spot_outcome") {
    const options = Array.isArray(modalObj?.options) ? modalObj.options : [];
    const selectedIndex = Number.isInteger(modalObj?.selected_option_index)
      ? modalObj.selected_option_index
      : Number.parseInt(modalObj?.selected_option_index ?? "0", 10) || 0;

    const callsign = String(modalObj?.callsign || "").trim();
    const parkRef = String(modalObj?.park_ref || "").trim();
    const band = String(modalObj?.band || "").trim();

    const meta = [callsign, parkRef, band].filter(Boolean).join(" • ");

    const optionsHtml = options.length
      ? options.map((opt, idx) => {
          const label = String(opt?.label || opt?.key || "").trim() || `Option ${idx + 1}`;
          const selected = idx === selectedIndex;
          return `
            <div class="rt-modal-option ${selected ? "is-selected" : ""}" data-option-index="${idx}">
              <span class="rt-modal-option-marker">${selected ? "▶" : " "}</span>
              <span class="rt-modal-option-label">${label}</span>
            </div>
          `;
        }).join("")
      : `<div class="rt-muted">No options available</div>`;

    return `
      <div class="rt-modal-body">
        ${meta ? `<div style="color:#fff; margin-bottom:8px;">${meta}</div>` : ``}
        <div class="rt-modal-options">
          ${optionsHtml}
        </div>
      </div>
    `;
  }

  const warning = String(modalObj?.warning || "").trim();
  const message = String(modalObj?.message || "").trim();
  const submessage = String(modalObj?.submessage || "").trim();

  return `
    <div class="rt-modal-body">
      ${warning ? `<div class="rt-modal-warning-title rt-modal-warning-red"><strong>${warning}</strong></div>` : ``}
      ${message ? `<div style="color:#fff; margin-top:6px;">${message}</div>` : ``}
      ${submessage ? `<div style="color:#fff; margin-top:6px;">${submessage}</div>` : ``}
      ${!warning && !message && !submessage ? `<div class="rt-muted">Controller-owned modal</div>` : ``}
    </div>
  `;
}

function isValidMode(mode) {
  return new Set([
    "AM", "FM", "CW", "USB", "LSB", "DIGU", "DIGL", "DATA", "FT8", "FT4"
  ]).has(String(mode || "").trim());
}

function validateIntent(intent, params) {
  const name = String(intent || "").trim();
  const p = isPlainObject(params) ? params : {};

  if (!name) return { ok: false, error: "missing-intent" };

  if (name === "radio.tune") {
    const freq_hz = Number(p.freq_hz);
    const band = String(p.band || "").trim();
    const mode = String(p.mode || "").trim();
    const autotune = Boolean(p.autotune);

    if (!Number.isInteger(freq_hz) || freq_hz < 1000000 || freq_hz > 60000000) {
      return { ok: false, error: "invalid-freq_hz" };
    }
    if (!isValidBand(band)) return { ok: false, error: "invalid-band" };
    if (!isValidMode(mode)) return { ok: false, error: "invalid-mode" };

    return {
      ok: true,
      intent: name,
      params: { freq_hz, band, mode, autotune },
    };
  }

  if (name === "radio.band") {
    const band = String(p.band || "").trim();
    const autotune = Boolean(p.autotune);

    if (!isValidBand(band)) return { ok: false, error: "invalid-band" };

    return {
      ok: true,
      intent: name,
      params: { band, autotune },
    };
  }

  if (name === "pota.select_band") {
    const band = String(p.band || "").trim();
    if (!isValidBand(band)) return { ok: false, error: "invalid-band" };
    return { ok: true, intent: name, params: { band } };
  }

  if (name === "pota.select_park") {
    const park_ref = String(p.park_ref ?? p.reference ?? "").trim();

    if (park_ref === "") {
      return { ok: true, intent: name, params: { park_ref: "" } };
    }

    if (!/^[A-Z]{2,}-\d+$/.test(park_ref)) {
      return { ok: false, error: "invalid-park_ref" };
    }

    return { ok: true, intent: name, params: { park_ref } };
  }

  if (name === "ui.open_modal") {
    const modal = String(p.modal || "").trim();
    const payload = isPlainObject(p.payload) ? p.payload : {};
    if (!modal) return { ok: false, error: "missing-modal" };
    return { ok: true, intent: name, params: { modal, payload } };
  }

  if (name === "radio.log_qso") {
    const call = String(p.call || "").trim().toUpperCase();
    const freq_hz = Number(p.freq_hz);
    const band = String(p.band || "").trim();

    let mode = String(p.mode || "").trim().toUpperCase();
    if (mode === "SSB") {
      if (Number.isInteger(freq_hz) && freq_hz > 0) {
        mode = freq_hz < 10_000_000 ? "LSB" : "USB";
      } else if (["160m", "80m", "60m", "40m"].includes(String(band).toLowerCase())) {
        mode = "LSB";
      } else {
        mode = "USB";
      }
    }

    const park_ref = String(p.park_ref || "").trim();
    const their_pota_ref = String(p.their_pota_ref || park_ref || "").trim();
    const my_pota_refs = Array.isArray(p.my_pota_refs)
      ? p.my_pota_refs.map((v) => String(v || "").trim().toUpperCase()).filter(Boolean)
      : [];

    if (!call) return { ok: false, error: "missing-call" };
    if (!/^[A-Z0-9/]+$/.test(call)) return { ok: false, error: "invalid-call" };
    if (!Number.isInteger(freq_hz) || freq_hz < 1000000 || freq_hz > 60000000) {
      return { ok: false, error: "invalid-freq_hz" };
    }
    if (!isValidBand(band)) return { ok: false, error: "invalid-band" };
    if (!isValidMode(mode)) return { ok: false, error: "invalid-mode" };

    return {
      ok: true,
      intent: name,
      params: {
        call,
        freq_hz,
        band,
        mode,
        park_ref,
        their_pota_ref,
        my_pota_refs,
      },
    };
  }

  if ([
    "ui.page.next",
    "ui.page.prev",
    "ui.page.goto",
    "ui.focus.next",
    "ui.focus.prev",
    "ui.focus.set",
    "ui.ok",
    "ui.cancel",
    "ui.back",
    "ui.browse.delta",
    "ui.browse.home",
    "ui.browse.end",
    "ui.modal.open",
    "ui.modal.close",
  ].includes(name)) {
    return { ok: true, intent: name, params: p };
  }

  return { ok: false, error: "unknown-intent" };
}

function handleRuntimeFocusRequest(ev, runtimeCtx) {
  const slot = findOwningSlot(ev.target);
  if (!slot) return;

  const panelId = String(slot.dataset.panelId || "").trim();
  if (!panelId) return;

  const nav = runtimeCtx?.nav || null;
  if (!nav || typeof nav.setActivePanel !== "function") return;

  nav.setActivePanel(panelId);
}

async function handleRuntimeIntentRequest(ev, runtimeCtx) {
  const slot = findOwningSlot(ev.target);
  if (!slot) return;

  const detail = ev.detail || {};
  const rawIntent = detail.intent || null;
  const rawParams = detail.params || {};

  const validation = validateIntent(rawIntent, rawParams);
  if (!validation.ok) {
    console.log("[rt] rt-emit-intent denied", {
      reason: validation.error,
      slotId: slot.dataset.slotId || null,
      panelId: slot.dataset.panelId || null,
      detail,
    });
    return;
  }

  const intent = validation.intent;
  const params = validation.params;

  if (typeof runtimeCtx?.isAllowedIntent === "function" && !runtimeCtx.isAllowedIntent(intent)) {
    console.log("[rt] rt-emit-intent denied", {
      reason: "page-intent-not-allowed",
      intent,
      params,
      slotId: slot.dataset.slotId || null,
      panelId: slot.dataset.panelId || null,
    });
    return;
  }

  if (intent === "ui.open_modal") {
    if (typeof runtimeCtx?.openUiModalIntent === "function") {
      runtimeCtx.openUiModalIntent(params, slot);
      return;
    }
    return;
  }

  if (typeof runtimeCtx?.emitIntent !== "function") return;
  await runtimeCtx.emitIntent(intent, params);
}

function installRuntimeExtensions(runtimeCtx) {
  const root = runtimeCtx?.root || null;
  if (!root) return;

  root.addEventListener("rt-request-focus", (ev) => {
    handleRuntimeFocusRequest(ev, runtimeCtx);
  });

  root.addEventListener("rt-emit-intent", (ev) => {
    void handleRuntimeIntentRequest(ev, runtimeCtx);
  });
}

// -----------------------------------------------------------------------------
// Layout + shell helpers
// -----------------------------------------------------------------------------

function normalizeLayout(layout) {
  return {
    top: Array.isArray(layout?.top) ? layout.top : [],
    middle: Array.isArray(layout?.middle) ? layout.middle : [],
    bottom: Array.isArray(layout?.bottom) ? layout.bottom : [],
  };
}

function buildRuntimeShell(root) {
  root.innerHTML = `
    <div class="rt-app">
      <div id="rt_top" class="rt-region rt-top"></div>
      <div id="rt_mid" class="rt-region rt-mid"></div>
      <div id="rt_bot" class="rt-region rt-bot"></div>
      <div id="rt_modal_root" class="rt-modal-root"></div>
    </div>
  `;
  return {
    top: root.querySelector("#rt_top"),
    mid: root.querySelector("#rt_mid"),
    bot: root.querySelector("#rt_bot"),
  };
}

function mkSlot(panelId) {
  const d = document.createElement("div");
  d.className = "rt-slot";
  d.dataset.panelId = String(panelId);
  d.dataset.panel = String(panelId);
  d.dataset.slotId = String(panelId);

  const hdr = document.createElement("div");
  hdr.className = "rt-slot-hdr";

  const body = document.createElement("div");
  body.className = "rt-slot-body";

  d.appendChild(hdr);
  d.appendChild(body);
  return d;
}

function coerceBindings(panel) {
  const b = panel?.bindings;
  if (Array.isArray(b)) return b;
  if (b && typeof b === "object") return Object.entries(b).map(([id, spec]) => ({ id, ...spec }));
  return [];
}

function normIds(arr) {
  return Array.isArray(arr) ? arr.map((x) => String(x || "").trim()).filter(Boolean) : [];
}

function buildPresentPanelIds(layout) {
  const out = [];
  for (const id of normIds(layout?.top)) out.push(id);

  const mid = Array.isArray(layout?.middle) ? layout.middle.slice(0, 3) : [];
  for (const colPanels of mid) {
    for (const id of normIds(Array.isArray(colPanels) ? colPanels : [])) out.push(id);
  }

  for (const id of normIds(layout?.bottom)) out.push(id);
  return out;
}

function buildFocusModel({ page, bundle, presentPanelIds }) {
  const focusablesPresent = presentPanelIds.filter((panelId) => {
    const p = bundle.panelsById[panelId];
    return p && p.focusable === true;
  });

  const fp = page?.focusPolicy || null;
  const rotation = normIds(fp?.rotation);
  const def = String(fp?.defaultPanel || "").trim() || null;

  let focusOrder = focusablesPresent.slice();
  let initialPanelId = null;

  if (fp) {
    if (rotation.length > 0) {
      focusOrder = rotation.filter((id) => focusablesPresent.includes(id));
      if (def && focusOrder.includes(def)) {
        initialPanelId = def;
      } else {
        initialPanelId = rotation.find((id) => focusablesPresent.includes(id)) || null;
      }
    } else if (def) {
      initialPanelId = focusablesPresent.includes(def) ? def : null;
    } else {
      initialPanelId = null;
    }
  } else {
    initialPanelId = focusOrder[0] || null;
  }

  return { focusOrder, initialPanelId };
}

function isBrowseCapableType(panelType) {
  const t = String(panelType || "").trim();
  return (
    t === "controller_services_summary" ||
    t === "node_health_summary" ||
    t === "pota_bands_summary" ||
    t === "pota_parks_summary" ||
    t === "pota_spots_summary"
  );
}

function dispatchBrowseDelta(slotEl, delta) {
  if (!slotEl) return;
  slotEl.dispatchEvent(new CustomEvent("rt-browse-delta", { detail: { delta } }));
}

function setBrowseIndicator(slotEl, enabled) {
  if (!slotEl) return;
  const hdr = slotEl.querySelector(".rt-slot-hdr");
  if (!hdr) return;

  let badge = hdr.querySelector('[data-rt-browse-indicator="1"]');

  if (enabled) {
    if (!badge) {
      badge = document.createElement("span");
      badge.setAttribute("data-rt-browse-indicator", "1");
      badge.className = "rt-pill warn";
      badge.textContent = "↕ BROWSE";
      hdr.appendChild(badge);
    }
    slotEl.classList.add("rt-browse-mode");
  } else {
    if (badge) badge.remove();
    slotEl.classList.remove("rt-browse-mode");
  }
}

function clearAllBrowseIndicators(rootEl) {
  if (!rootEl) return;
  rootEl.querySelectorAll(".rt-slot").forEach((slot) => {
    setBrowseIndicator(slot, false);
  });
}

function syncBrowseIndicator({ rootEl, browsePanelId, slotByPanelId }) {
  if (!rootEl) return;
  clearAllBrowseIndicators(rootEl);
  if (!browsePanelId) return;
  const slot = slotByPanelId?.get(browsePanelId) || null;
  setBrowseIndicator(slot, true);
}

function sameUiState(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function tryParseJSON(v) {
  if (!v || typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}

async function fetchUiProjectionState() {
  const resp = await fetch("/api/v1/ui/state/batch", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      keys: [
        "rt:ui:page",
        "rt:ui:focus",
        "rt:ui:layer",
        "rt:ui:browse",
        "rt:ui:modal",
        "rt:ui:authority",
        "rt:ui:page_context",
      ],
    }),
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }

  const payload = await resp.json();
  const values = payload?.data?.values || {};

  function getValue(key) {
    const entry = values[key];
    if (!entry || !entry.ok) return null;
    return entry.value ?? null;
  }

  return {
    page: getValue("rt:ui:page"),
    focus: getValue("rt:ui:focus"),
    layer: getValue("rt:ui:layer"),
    browse: tryParseJSON(getValue("rt:ui:browse")),
    modal: tryParseJSON(getValue("rt:ui:modal")),
    authority: tryParseJSON(getValue("rt:ui:authority")),
    page_context: tryParseJSON(getValue("rt:ui:page_context")),
  };
}

const UI_PROJECTION_TOPIC = "ui.projection.changed";

(async function main() {
  const params = new URLSearchParams(location.search);
  const root = document.getElementById("rt_mount") || document.body;
  const debug = params.get("debug") === "1" || window.RT_DEBUG === true;

  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "./runtime.css";
  document.head.appendChild(css);

  let bundle;
  try {
    bundle = await loadConfigBundle();
  } catch (e) {
    renderPanelError(root, { title: "Config load failed", detail: String(e?.message || e) });
    return;
  }

  const registry = createRendererRegistry();
  const store = createBindingStore();
  const nav = createNavMachine();

  let currentUiState = {
    page: null,
    focus: null,
    layer: null,
    browse: null,
    modal: null,
    authority: null,
    page_context: null,
  };

  let currentPage = null;
  let currentPageId = null;
  let slotByPanelId = new Map();
  let refreshHandles = [];
  let navMode = "GLOBAL_FOCUS";
  let browsePanelId = null;
  let activeModalSignature = null;
  let _activeModal = null;
  let panelLastData = new Map();
  let panelRerender = new Map();
  let uiProjectionUnsub = null;
  let uiProjectionPollTimer = null;
  let uiProjectionRetryTimer = null;
  let uiProjectionInflight = false;
  let uiProjectionNeedsRerun = false;
  let uiProjectionSubscribed = false;
  let uiProjectionRetryDelayMs = 1500;

  function stopAllRefresh() {
    for (const handle of refreshHandles) {
      try {
        if (handle && typeof handle.stop === "function") handle.stop();
      } catch (_) {}
    }
    refreshHandles = [];
  }

  function ensureModalRoot() {
    let m = root.querySelector("#rt_modal_root");
    if (!m) {
      m = document.createElement("div");
      m.id = "rt_modal_root";
      m.className = "rt-modal-root";
      root.appendChild(m);
    }
    return m;
  }

  function buildInjectedUiStateForPanel(panelId, uiState) {
    const browse =
      uiState?.browse &&
      typeof uiState.browse === "object" &&
      String(uiState.browse.panel || "") === String(panelId || "")
        ? uiState.browse
        : null;

    return {
      page: uiState?.page || null,
      focus: uiState?.focus || null,
      layer: uiState?.layer || null,
      browse,
      modal: uiState?.modal || null,
      authority: uiState?.authority || null,
      page_context: uiState?.page_context || null,
    };
  }

  function buildRenderDataForPanel(panelId, uiState) {
    const base = panelLastData.get(panelId) || {};
    const injectedUi = buildInjectedUiStateForPanel(panelId, uiState);

    return {
      ...base,
      ui_browse: injectedUi.browse,
      ui_page_context: injectedUi.page_context,
      __ui: injectedUi,
    };
  }

  function rerenderPanelsFromUiState() {
    for (const [panelId, rerender] of panelRerender.entries()) {
      try {
        rerender(buildRenderDataForPanel(panelId, currentUiState));
      } catch (e) {
        console.warn("[rt] rerenderPanelsFromUiState failed", panelId, e);
      }
    }
  }

  function closeLocalModal() {
    const mroot = ensureModalRoot();
    mroot.innerHTML = "";
    activeModalSignature = null;
    _activeModal = null;
  }

  function openProjectedModal(modalObj) {
    const mroot = ensureModalRoot();
    const sig = JSON.stringify(modalObj || {});
    if (sig === activeModalSignature) return;
    activeModalSignature = sig;

    const title = String(modalObj?.title || modalObj?.type || "Modal");
    const confirmable = Boolean(modalObj?.confirmable);
    const cancelable = Boolean(modalObj?.cancelable !== false);
    const destructive = Boolean(modalObj?.destructive);

    const bodyHtml = buildProjectedModalBodyHtml(modalObj);

    const confirmLabel = String(modalObj?.confirm_label || "OK").trim() || "OK";
    const cancelLabel = String(modalObj?.cancel_label || "Cancel").trim() || "Cancel";

    mroot.innerHTML = `
      <div class="rt-modal-backdrop"></div>
      <div class="rt-modal" role="dialog" aria-modal="true">
        <div class="rt-modal-title">${title}</div>
        ${bodyHtml}
        <div class="rt-modal-actions">
          ${cancelable ? `<button class="rt-btn rt-btn-cancel">${cancelLabel}</button>` : ``}
          ${confirmable ? `<button class="rt-btn rt-btn-ok ${destructive ? "rt-btn-danger" : ""}">${confirmLabel}</button>` : ``}
        </div>
      </div>
    `;

    const okBtn = mroot.querySelector(".rt-btn-ok");
    const cancelBtn = mroot.querySelector(".rt-btn-cancel");

    async function ok() {
      if (typeof emitIntent === "function") {
        await emitIntent("ui.ok", {});
      }
    }

    async function cancel() {
      if (typeof emitIntent === "function") {
        await emitIntent("ui.cancel", {});
      }
    }

    okBtn?.addEventListener("click", ok);
    cancelBtn?.addEventListener("click", cancel);

    _activeModal = { ok, cancel, close: closeLocalModal };
  }

  function updateLocalUiModeFromProjection(uiState) {
    const layer = String(uiState?.layer || "default");
    const browseObj = uiState?.browse && typeof uiState.browse === "object" ? uiState.browse : null;

    if (layer === "modal") {
      navMode = "MODAL_DIALOG";
      browsePanelId = null;
      if (uiState.modal && typeof uiState.modal === "object") {
        openProjectedModal(uiState.modal);
      }
      clearAllBrowseIndicators(root);
      return;
    }

    closeLocalModal();

    if (layer === "browse" && browseObj?.panel) {
      navMode = "PANEL_BROWSE";
      browsePanelId = String(browseObj.panel || "").trim() || null;
      syncBrowseIndicator({ rootEl: root, browsePanelId, slotByPanelId });
      return;
    }

    navMode = "GLOBAL_FOCUS";
    browsePanelId = null;
    clearAllBrowseIndicators(root);
  }

  function buildAllowedIntents(page) {
    return new Set(
      Array.isArray(page?.controls?.allowedIntents) ? page.controls.allowedIntents : []
    );
  }

  let allowedIntents = new Set();

  function isAllowed(intent) {
    return allowedIntents.has(intent);
  }

  function clearUiProjectionRetryTimer() {
    if (uiProjectionRetryTimer) {
      clearTimeout(uiProjectionRetryTimer);
      uiProjectionRetryTimer = null;
    }
  }

  function scheduleUiProjectionRetry(reason = "retry") {
    if (uiProjectionRetryTimer) return;

    uiProjectionRetryTimer = setTimeout(() => {
      uiProjectionRetryTimer = null;
      void refreshUiProjectionState(reason);
    }, uiProjectionRetryDelayMs);
  }

  async function emitIntent(intent, params = null) {
    try {
      if (typeof store.publishIntent !== "function") {
        return { ok: false, err: "publishIntent_not_available", meta: {} };
      }

      const s = nav.getState();
      const res = await store.publishIntent({
        intent,
        params,
        pageId: currentPage?.id || currentPageId || null,
        panelId: s.activePanelId || null,
        source: "rt-display",
      });

      if (!res.ok) console.warn("publishIntent failed", res.err, res.meta);
      return res;
    } catch (e) {
      console.warn("emitIntent exception", e);
      return { ok: false, err: String(e?.message || e), meta: {} };
    }
  }

  const runtimeCtx = {
    root,
    debug,
    bundle,
    nav,
    getNavMode: () => navMode,
    getBrowsePanelId: () => browsePanelId,
    isAllowedIntent: (intent) => isAllowed(intent),
    emitIntent: (intent, params) => emitIntent(intent, params),
    openUiModalIntent: async (params) => {
      const modal = String(params?.modal || "").trim();
      const payload = params?.payload || {};

      if (modal === "confirm") {
        const action = payload?.action || null;
        if (action?.intent && isAllowed("ui.modal.open")) {
          await emitIntent("ui.modal.open", {
            type: "confirm",
            title: payload?.title || "Confirm",
            confirmable: true,
            cancelable: true,
            destructive: Boolean(payload?.danger),
          });
        }
        return;
      }

      console.warn("[rt] unsupported ui.open_modal modal:", modal, payload);
    },
  };

  installRuntimeExtensions(runtimeCtx);

  function mountCurrentPage(pageId, focusId) {
    const page = bundle.pagesById[pageId];
    if (!page) {
      stopAllRefresh();
      panelLastData = new Map();
      panelRerender = new Map();
      buildRuntimeShell(root);
      renderPanelError(root, { title: "Unknown page", detail: `No page '${pageId}'` });
      return;
    }

    currentPage = page;
    currentPageId = pageId;
    allowedIntents = buildAllowedIntents(page);

    stopAllRefresh();

    const layout = normalizeLayout(page.layout);
    const regions = buildRuntimeShell(root);

    layout.top.forEach((id) => regions.top.appendChild(mkSlot(id)));

    layout.middle.slice(0, 3).forEach((colPanels) => {
      const col = document.createElement("div");
      col.className = "rt-col";
      (Array.isArray(colPanels) ? colPanels : []).forEach((id) => col.appendChild(mkSlot(id)));
      regions.mid.appendChild(col);
    });

    layout.bottom.forEach((id) => regions.bot.appendChild(mkSlot(id)));

    slotByPanelId = new Map();
    root.querySelectorAll(".rt-slot").forEach((slot) => {
      const pid = String(slot.dataset.panelId || "").trim();
      if (pid) slotByPanelId.set(pid, slot);
    });

    const presentPanelIds = buildPresentPanelIds(layout);
    const { focusOrder, initialPanelId } = buildFocusModel({ page, bundle, presentPanelIds });
    const effectiveFocus = focusId && focusOrder.includes(focusId) ? focusId : initialPanelId;

    nav.setPageModel({
      pageId: page.id || pageId,
      focusablePanelIds: focusOrder,
      slotByPanelId,
      initialPanelId: effectiveFocus,
      rememberFocus: false,
    });

    installRuntimeExtensions(runtimeCtx);

    root.querySelectorAll(".rt-slot").forEach((slot) => {
      const panelId = slot.dataset.panelId;
      const bodyEl = slot.querySelector(".rt-slot-body") || slot;

      const panel = bundle.panelsById[panelId];
      if (!panel) {
        renderPanelError(bodyEl, { title: "Missing panel", detail: panelId });
        return;
      }

      const renderer = registry.get(panel.type);
      if (!renderer) {
        renderPanelError(bodyEl, { title: "No renderer", detail: panel.type });
        return;
      }

      const doRender = (renderData) => {
        renderer(bodyEl, panel, renderData);

        if (navMode === "PANEL_BROWSE" && browsePanelId === panelId) {
          queueMicrotask(() =>
            syncBrowseIndicator({ rootEl: root, browsePanelId, slotByPanelId })
          );
        }
      };

      panelRerender.set(panelId, doRender);

      const handle = startPanelRefresh({
        slot,
        panel,
        bindings: coerceBindings(panel),
        store,
        render: (data) => {
          panelLastData.set(panelId, data || {});
          doRender(buildRenderDataForPanel(panelId, currentUiState));
        },
      });

      refreshHandles.push(handle);
    });

    updateLocalUiModeFromProjection(currentUiState);
  }

  function maybeApplyUiState(uiState) {
    currentUiState = uiState;

    const pageId = String(uiState?.page || "").trim() || "home";
    const focusId = String(uiState?.focus || "").trim() || null;

    // Always remount the visible page on projection changes.
    // This forces panel bindings to be re-fetched instead of reusing stale panelLastData.
    mountCurrentPage(pageId, focusId);

    if (currentPage && focusId) {
      nav.setActivePanel(focusId);
    }

    updateLocalUiModeFromProjection(uiState);
  }

  async function refreshUiProjectionState(reason = "unknown") {
    if (uiProjectionInflight) {
      uiProjectionNeedsRerun = true;
      return;
    }

    uiProjectionInflight = true;
    try {
      const uiState = await fetchUiProjectionState();

      clearUiProjectionRetryTimer();
      uiProjectionRetryDelayMs = 1500;
      rtHideControllerOverlay();

      maybeApplyUiState(uiState);
    } catch (e) {
      rtShowControllerOverlay("Lost connection to rt-controller. Reconnecting…");

      if (debug) {
        console.warn("[rt] refreshUiProjectionState error", reason, e);
      }

      scheduleUiProjectionRetry("retry-after-error");
      uiProjectionRetryDelayMs = Math.min(uiProjectionRetryDelayMs * 2, 5000);
    } finally {
      uiProjectionInflight = false;
      if (uiProjectionNeedsRerun) {
        uiProjectionNeedsRerun = false;
        queueMicrotask(() => {
          void refreshUiProjectionState("rerun");
        });
      }
    }
  }

  function startUiProjectionUpdates() {
    if (uiProjectionSubscribed) return;
    uiProjectionSubscribed = true;

    store.subscribe(UI_PROJECTION_TOPIC);
    uiProjectionUnsub = store.on(UI_PROJECTION_TOPIC, () => {
      clearUiProjectionRetryTimer();
      void refreshUiProjectionState("bus");
    });

    // FALLBACK POLLING DISABLED - event driven only
    //uiProjectionPollTimer = setInterval(() => {
    //  void refreshUiProjectionState("fallback");
    //}, 10000);
  }

  function keyToIntent(e) {
    if (e.key === "]") return { intent: "ui.focus.next", params: {} };
    if (e.key === "[") return { intent: "ui.focus.prev", params: {} };
    if (e.key === "Enter") return { intent: "ui.ok", params: {} };
    if (e.key === "Escape") return { intent: "ui.cancel", params: {} };
    if (e.key === "ArrowDown") return { intent: "ui.browse.delta", params: { delta: +1 } };
    if (e.key === "ArrowUp") return { intent: "ui.browse.delta", params: { delta: -1 } };
    if (e.key === "PageDown") return { intent: "ui.page.next", params: {} };
    if (e.key === "PageUp") return { intent: "ui.page.prev", params: {} };
    return null;
  }

  window.addEventListener("keydown", async (e) => {
    const mapped = keyToIntent(e);
    if (!mapped) return;
    e.preventDefault();

    const validation = validateIntent(mapped.intent, mapped.params || {});
    if (!validation.ok) return;
    if (!isAllowed(validation.intent)) return;

    await emitIntent(validation.intent, validation.params);
  });

  await refreshUiProjectionState("initial");
  if (!currentPageId) {
    mountCurrentPage("home", null);
  }

  startUiProjectionUpdates();

  window.addEventListener("beforeunload", () => {
    try {
      if (typeof uiProjectionUnsub === "function") {
        uiProjectionUnsub();
      }
      store.unsubscribe(UI_PROJECTION_TOPIC);
      clearUiProjectionRetryTimer();
      if (uiProjectionPollTimer) clearInterval(uiProjectionPollTimer);
    } catch (_) {}
  });
})();