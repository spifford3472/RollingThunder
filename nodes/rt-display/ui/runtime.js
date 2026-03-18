// runtime.js
//
// RollingThunder UI Runtime (rt-display)
//
// Responsibilities:
// - Load config bundle (pages + panels)
// - Build deterministic shell + slots from page.layout
// - Mount panel renderers into slot bodies
// - Run refresh lifecycle for panels
// - Provide deterministic navigation + input routing (read-only)
//
// Notes:
// - UI is representational only (ARCHITECTURE.md / UI_SEMANTICS.md).
// - Controller-bound intents are gated by page.controls.allowedIntents.
// - This file does NOT write Redis and does NOT implement business logic.
// - Keyboard is a dev stand-in for ESP32 controls.
//
// NAV v2 (Option A):
// - LOCAL input capture state machine:
//     GLOBAL_FOCUS  -> (OK on browse-capable panel) -> PANEL_BROWSE
//     PANEL_BROWSE  -> (CANCEL) -> GLOBAL_FOCUS
// - Browse delivers per-tick deltas to the focused panel slot via:
//     slot.dispatchEvent(new CustomEvent("rt-browse-delta", { detail: { delta } }))
//
// Browse-capable panel types (today):
// - controller_services_summary  (windowed list scroll)

import { createNavMachine } from "./nav_machine.js";
import { loadConfigBundle } from "./config_loader.js";
import { createRendererRegistry } from "./renderer_registry.js";
import { createBindingStore } from "./binding_store.js";
import { startPanelRefresh } from "./refresh.js";
import { renderPanelError } from "./renderers/panel_error.js";

// -----------------------------------------------------------------------------
// Runtime extension helpers (Step 1: observe only; no validation/dispatch yet)
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

function isValidMode(mode) {
  return new Set([
    "AM", "FM", "CW", "USB", "LSB", "DIGU", "DIGL", "DATA", "FT8", "FT4"
  ]).has(String(mode || "").trim());
}

function validateIntent(intent, params) {
  const name = String(intent || "").trim();
  const p = isPlainObject(params) ? params : {};

  if (!name) {
    return { ok: false, error: "missing-intent" };
  }

  if (name === "radio.tune") {
    const freq_hz = Number(p.freq_hz);
    const band = String(p.band || "").trim();
    const mode = String(p.mode || "").trim();
    const autotune = Boolean(p.autotune);

    if (!Number.isInteger(freq_hz) || freq_hz < 1000000 || freq_hz > 60000000) {
      return { ok: false, error: "invalid-freq_hz" };
    }
    if (!isValidBand(band)) {
      return { ok: false, error: "invalid-band" };
    }
    if (!isValidMode(mode)) {
      return { ok: false, error: "invalid-mode" };
    }

    return {
      ok: true,
      intent: name,
      params: { freq_hz, band, mode, autotune },
    };
  }

  if (name === "radio.band") {
    const band = String(p.band || "").trim();
    const autotune = Boolean(p.autotune);

    if (!isValidBand(band)) {
      return { ok: false, error: "invalid-band" };
    }

    return {
      ok: true,
      intent: name,
      params: { band, autotune },
    };
  }

  if (name === "pota.select_band") {
    const band = String(p.band || "").trim();

    if (!isValidBand(band)) {
      return { ok: false, error: "invalid-band" };
    }

    return {
      ok: true,
      intent: name,
      params: { band },
    };
  }

  if (name === "pota.select_park") {
    const park_ref = String(p.park_ref ?? p.reference ?? "").trim();

    // Allow empty string for the synthetic "Not in a park" selection.
    if (park_ref === "") {
      return {
        ok: true,
        intent: name,
        params: { park_ref: "" },
      };
    }

    // Accept POTA-style refs like US-1940, K-1234, VE-0001, etc.
    // Format: 2+ uppercase letters, dash, 1+ digits.
    if (!/^[A-Z]{2,}-\d+$/.test(park_ref)) {
      return { ok: false, error: "invalid-park_ref" };
    }

    return {
      ok: true,
      intent: name,
      params: { park_ref },
    };
  }

  if (name === "ui.open_modal") {
    const modal = String(p.modal || "").trim();
    const payload = isPlainObject(p.payload) ? p.payload : {};

    if (!modal) {
      return { ok: false, error: "missing-modal" };
    }

    return {
      ok: true,
      intent: name,
      params: { modal, payload },
    };
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

    if (!call) {
      return { ok: false, error: "missing-call" };
    }
    if (!/^[A-Z0-9/]+$/.test(call)) {
      return { ok: false, error: "invalid-call" };
    }
    if (!Number.isInteger(freq_hz) || freq_hz < 1000000 || freq_hz > 60000000) {
      return { ok: false, error: "invalid-freq_hz" };
    }
    if (!isValidBand(band)) {
      return { ok: false, error: "invalid-band" };
    }
    if (!isValidMode(mode)) {
      return { ok: false, error: "invalid-mode" };
    }

    return {
      ok: true,
      intent: name,
      params: {
        call,
        freq_hz,
        band,
        mode,
        park_ref,
      },
    };
  }
  
  return { ok: false, error: "unknown-intent" };
}

function handleRuntimeFocusRequest(ev, runtimeCtx) {
  const slot = findOwningSlot(ev.target);
  if (!slot) {
    console.warn("[rt] rt-request-focus ignored: no owning slot");
    return;
  }

  const panelId = String(slot.dataset.panelId || "").trim();
  if (!panelId) {
    console.warn("[rt] rt-request-focus ignored: slot missing panelId");
    return;
  }

  const nav = runtimeCtx?.nav || null;
  if (!nav || typeof nav.getState !== "function") {
    console.warn("[rt] rt-request-focus ignored: nav.getState unavailable", {
      panelId,
    });
    return;
  }

  const navState = nav.getState();
  const stateName = String(navState?.state || "GLOBAL_FOCUS");

  if (stateName !== "GLOBAL_FOCUS") {
    console.log("[rt] rt-request-focus denied", {
      reason: "nav-state-blocked",
      state: stateName,
      panelId,
      detail: ev.detail || {},
    });
    return;
  }

  if (typeof nav.setActivePanel !== "function") {
    console.warn("[rt] rt-request-focus ignored: nav.setActivePanel unavailable", {
      panelId,
    });
    return;
  }

  const ok = nav.setActivePanel(panelId);

  console.log(ok ? "[rt] rt-request-focus granted" : "[rt] rt-request-focus denied", {
    reason: ok ? "focused" : "panel-not-focusable",
    panelId,
    detail: ev.detail || {},
    navStateAfter: typeof nav.getState === "function" ? nav.getState() : null,
  });
}

async function handleRuntimeIntentRequest(ev, runtimeCtx) {
  const slot = findOwningSlot(ev.target);
  if (!slot) {
    console.warn("[rt] rt-emit-intent ignored: no owning slot");
    return;
  }

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
      console.log("[rt] rt-emit-intent handled locally", {
        intent,
        params,
        slotId: slot.dataset.slotId || null,
        panelId: slot.dataset.panelId || null,
      });
      return;
    }

    console.warn("[rt] ui.open_modal requested but no local modal handler is installed");
    return;
  }

  if (typeof runtimeCtx?.emitIntent !== "function") {
    console.warn("[rt] rt-emit-intent ignored: emitIntent unavailable", {
      intent,
      params,
    });
    return;
  }

  const res = await runtimeCtx.emitIntent(intent, params);

  console.log("[rt] rt-emit-intent forwarded", {
    intent,
    params,
    slotId: slot.dataset.slotId || null,
    panelId: slot.dataset.panelId || null,
    result: res,
  });
}

function installRuntimeExtensions(runtimeCtx) {
  // Temp Debug
  console.log("[rt] runtimeCtx nav methods", {
    hasGetState: typeof runtimeCtx?.nav?.getState === "function",
    hasSetActivePanel: typeof runtimeCtx?.nav?.setActivePanel === "function",
  });

  const root = runtimeCtx?.root || null;
  if (!root) {
    console.warn("[rt] installRuntimeExtensions: missing root element");
    return;
  }

  root.addEventListener("rt-request-focus", (ev) => {
    handleRuntimeFocusRequest(ev, runtimeCtx);
  });

  root.addEventListener("rt-emit-intent", (ev) => {
    void handleRuntimeIntentRequest(ev, runtimeCtx);
  });

  if (runtimeCtx?.debug) {
    console.debug("[rt] runtime extensions installed");
  }
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
    </div>
  `;
  return {
    top: root.querySelector("#rt_top"),
    mid: root.querySelector("#rt_mid"),
    bot: root.querySelector("#rt_bot"),
  };
}

// slot has a header + body so diagnostics can’t be overwritten by renderer.
function mkSlot(panelId) {
  const d = document.createElement("div");
  d.className = "rt-slot";
  d.dataset.panelId = String(panelId);

  // Helpful aliases for future runtime extension work.
  // Safe even if not used yet.
  d.dataset.panel = String(panelId);
  d.dataset.slotId = String(panelId);

  // runtime-owned header (never rendered by panels)
  const hdr = document.createElement("div");
  hdr.className = "rt-slot-hdr";

  // renderer-owned body (panel renders only here)
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
      const rotFirst = rotation.find((id) => focusablesPresent.includes(id)) || null;
      initialPanelId = rotFirst;
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

// ---------- Browse indicator (runtime-owned, header-only) ----------
function setBrowseIndicator(slotEl, enabled) {
  if (!slotEl) return;
  const hdr = slotEl.querySelector(".rt-slot-hdr");
  if (!hdr) return;

  // Keep it minimal and deterministic; do not stomp other header contents.
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

function syncBrowseIndicator({ rootEl, navMode, browsePanelId, slotByPanelId }) {
  if (!rootEl) return;

  if (navMode !== "PANEL_BROWSE" || !browsePanelId) {
    clearAllBrowseIndicators(rootEl);
    return;
  }

  clearAllBrowseIndicators(rootEl);
  const slot = slotByPanelId?.get(browsePanelId) || null;
  setBrowseIndicator(slot, true);
}
// ---------------------------------------------------------------

(async function main() {
  const params = new URLSearchParams(location.search);
  const pageId = params.get("page") || "home";

  const root = document.getElementById("rt_mount") || document.body;
  const debug =
    params.get("debug") === "1" ||
    window.RT_DEBUG === true;

  // Local nav state
  let navMode = "GLOBAL_FOCUS"; // GLOBAL_FOCUS | PANEL_BROWSE | MODAL_DIALOG
  let browsePanelId = null;     // which panel owns browse right now

  // Current modal hooks so key handler can drive the active modal
  // { ok: fn, cancel: fn, close: fn, prevNavMode: string }
  let _activeModal = null;

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

  function openConfirmModal({
    title,
    body,
    confirmLabel = "OK",
    cancelLabel = "Cancel",
    onConfirm,
    onCancel,
    twoStep = false,
    armLabel = "CONFIRM",
    timeoutMs = 5000,
    danger = false,
    warningHtml = "",
  }) {
    const mroot = ensureModalRoot();

    let armed = false;
    let timerId = null;
    let armExpiresAt = 0;

    function render() {
      const okText = armed ? armLabel : confirmLabel;

      mroot.innerHTML = `
        <div class="rt-modal-backdrop"></div>
        <div class="rt-modal" role="dialog" aria-modal="true">
          <div class="rt-modal-title">${title}</div>
          <div class="rt-modal-body">
            ${body}
            ${warningHtml ? `<div class="rt-modal-warning">${warningHtml}</div>` : ``}
            ${armed ? `<div class="rt-modal-countdown rt-muted" style="margin-top:10px;"></div>` : ``}
          </div>
          <div class="rt-modal-actions">
            <button class="rt-btn rt-btn-cancel">${cancelLabel}</button>
            <button class="rt-btn rt-btn-ok ${armed && danger ? "rt-btn-danger" : ""}">${okText}</button>
          </div>
        </div>
      `;

      const okBtn = mroot.querySelector(".rt-btn-ok");
      const cancelBtn = mroot.querySelector(".rt-btn-cancel");

      okBtn?.addEventListener("click", ok);
      cancelBtn?.addEventListener("click", cancel);
      okBtn?.focus?.();
    }

    function updateCountdown() {
      const el = mroot.querySelector(".rt-modal-countdown");
      if (!el) return;
      const left = Math.max(0, armExpiresAt - Date.now());
      el.textContent = left <= 0 ? "" : `Confirm within ${Math.ceil(left / 1000)}s`;
    }

    const prevNavMode = navMode;
    navMode = "MODAL_DIALOG";

    function close() {
      if (timerId) {
        try { clearInterval(timerId); } catch (_) {}
        timerId = null;
      }
      mroot.innerHTML = "";
      _activeModal = null;
      navMode = prevNavMode;
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
    }

    async function ok() {
      if (twoStep) {
        if (!armed) {
          armed = true;
          armExpiresAt = Date.now() + timeoutMs;

          render();
          updateCountdown();

          timerId = setInterval(() => {
            updateCountdown();
            if (Date.now() >= armExpiresAt) {
              close();
            }
          }, 200);

          return;
        }

        try { await onConfirm?.(); } finally { close(); }
        return;
      }

      try { await onConfirm?.(); } finally { close(); }
    }

    function cancel() {
      try { onCancel?.(); } finally { close(); }
    }

    _activeModal = { ok, cancel, close, prevNavMode };
    render();
  }

  // attach runtime css
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

  const page = bundle.pagesById[pageId];
  if (!page) {
    renderPanelError(root, { title: "Unknown page", detail: `No page '${pageId}'` });
    return;
  }

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

  const registry = createRendererRegistry();
  const store = createBindingStore();
  const nav = createNavMachine();

  // Step 1 runtime extension context
  const runtimeCtx = {
    root,
    debug,
    pageId,
    page,
    bundle,
    nav,
    getNavMode: () => navMode,
    getBrowsePanelId: () => browsePanelId,
    isAllowedIntent: (intent) => isAllowed(intent),
    emitIntent: (intent, params) => emitIntent(intent, params),
    openUiModalIntent: (params) => {
      const modal = String(params?.modal || "").trim();
      const payload = params?.payload || {};

      if (modal === "confirm") {
        root.dispatchEvent(new CustomEvent("rt-open-modal", {
          detail: {
            kind: "confirm",
            ...payload,
          }
        }));
        return;
      }

      console.warn("[rt] unsupported ui.open_modal modal:", modal, payload);
    },
  };

  installRuntimeExtensions(runtimeCtx);

  // Build slot map panelId -> slot element
  const slotByPanelId = new Map();
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const pid = String(slot.dataset.panelId || "").trim();
    if (pid) slotByPanelId.set(pid, slot);
  });

  const presentPanelIds = buildPresentPanelIds(layout);
  const { focusOrder, initialPanelId } = buildFocusModel({ page, bundle, presentPanelIds });

  nav.setPageModel({
    pageId: page.id || pageId,
    focusablePanelIds: focusOrder,
    slotByPanelId,
    initialPanelId,
    rememberFocus: true,
  });

  // -------------------------
  // Input gating (authoritative)
  // -------------------------
  const allowedIntents = new Set(
    Array.isArray(page?.controls?.allowedIntents) ? page.controls.allowedIntents : []
  );

  function isAllowed(intent) {
    return allowedIntents.has(intent);
  }

  // -------------------------
  // Intent transport (read-only)
  // -------------------------
  async function emitIntent(intent, params = null) {
    try {
      if (typeof store.publishIntent !== "function") {
        return { ok: false, err: "publishIntent_not_available", meta: {} };
      }

      const s = nav.getState();
      const res = await store.publishIntent({
        intent,
        params,
        pageId: page.id || pageId,
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

  // -------------------------
  // Modal open contract (panel -> runtime)
  // -------------------------
  function openNodeRestartModal({ nodeId, onRequestRestart }) {
    const mroot = ensureModalRoot();

    const isController = String(nodeId) === "rt-controller";
    const title = "Node action";

    let armed = false;
    let timeoutHandle = null;

    const warnHtml = isController
      ? `<div class="rt-modal-warn rt-blink-warn">WARNING - THIS WILL RESTART THE SYSTEM</div>`
      : "";

    function render() {
      const okLabel = isController ? (armed ? "CONFIRM" : "OK") : "OK";
      const okClass = isController && armed ? "rt-btn-ok rt-btn-danger" : "rt-btn-ok";

      mroot.innerHTML = `
        <div class="rt-modal-backdrop"></div>
        <div class="rt-modal" role="dialog" aria-modal="true">
          <div class="rt-modal-title">${title}</div>
          <div class="rt-modal-body">
            <div>Selected node: <strong>${String(nodeId)}</strong></div>
            ${warnHtml}
            <div class="small" style="margin-top:8px; opacity:.8;">
              ${isController
                ? "Press OK to arm, then CONFIRM to reboot controller."
                : "Press OK to reboot selected node."}
            </div>
          </div>
          <div class="rt-modal-actions">
            <button class="rt-btn rt-btn-cancel">Exit</button>
            <button class="rt-btn ${okClass}">${okLabel}</button>
          </div>
        </div>
      `;

      const okBtn = mroot.querySelector(".rt-btn-ok");
      const cancelBtn = mroot.querySelector(".rt-btn-cancel");

      cancelBtn?.addEventListener("click", cancel);
      okBtn?.addEventListener("click", ok);
      okBtn?.focus?.();
    }

    const prevNavMode = navMode;
    navMode = "MODAL_DIALOG";

    function clearTimer() {
      if (timeoutHandle) {
        try { clearTimeout(timeoutHandle); } catch (_) {}
        timeoutHandle = null;
      }
    }

    function startConfirmTimeout() {
      clearTimer();
      timeoutHandle = setTimeout(() => {
        cancel();
      }, 5000);
    }

    function close() {
      clearTimer();
      mroot.innerHTML = "";
      _activeModal = null;
      navMode = prevNavMode;
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
    }

    async function ok() {
      if (isController) {
        if (!armed) {
          armed = true;
          render();
          startConfirmTimeout();
          return;
        }
        try { await onRequestRestart?.(); } finally { close(); }
        return;
      }

      try { await onRequestRestart?.(); } finally { close(); }
    }

    function cancel() {
      close();
    }

    _activeModal = { ok, cancel, close, prevNavMode };
    render();
  }

  root.addEventListener("rt-open-modal", (ev) => {
    const d = ev?.detail || {};
    const kind = String(d.kind || "").trim();

    if (kind === "confirm") {
      return openConfirmModal({
        title: String(d.title || "Confirm"),
        body: String(d.body || ""),
        confirmLabel: String(d.confirmLabel || "OK"),
        cancelLabel: String(d.cancelLabel || "Cancel"),
        twoStep: Boolean(d.twoStep),
        armLabel: String(d.armLabel || "CONFIRM"),
        timeoutMs: Number.isFinite(Number(d.timeoutMs)) ? Number(d.timeoutMs) : 5000,
        danger: Boolean(d.danger),
        warningHtml: String(d.warningHtml || ""),
        onConfirm: async () => {
          const intent = String(d?.action?.intent || "").trim();
          const params = d?.action?.params || null;

          if (!intent) return;
          if (!isAllowed(intent)) {
            console.warn("Intent not allowed on this page:", intent);
            return;
          }
          await emitIntent(intent, params);
        },
        onCancel: () => {},
      });
    }

    if (kind === "node_restart") {
      const nodeId = String(d.nodeId || "").trim();
      const intent = String(d?.action?.intent || "").trim();
      const params = d?.action?.params || null;

      if (!nodeId || !intent) return;

      return openNodeRestartModal({
        nodeId,
        onRequestRestart: async () => {
          if (!isAllowed(intent)) {
            console.warn("Intent not allowed on this page:", intent);
            return;
          }
          await emitIntent(intent, params);
        },
      });
    }
  });

  function getPanelDefaultAction(panelId) {
    const panel = bundle.panelsById[panelId];
    const actions = Array.isArray(panel?.actions) ? panel.actions : [];
    if (actions.length !== 1) return null;
    const a = actions[0];
    const intent = String(a?.intent || "").trim();
    if (!intent) return null;
    return { intent, params: a?.params || null };
  }

  function getActivePanelId() {
    const s = nav.getState();
    return s?.activePanelId || null;
  }

  function enterBrowseIfCapable() {
    const pid = getActivePanelId();
    if (!pid) return false;

    const p = bundle.panelsById[pid];
    if (!p) return false;
    if (!isBrowseCapableType(p.type)) return false;

    navMode = "PANEL_BROWSE";
    browsePanelId = pid;

    syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
    return true;
  }

  function exitBrowse() {
    navMode = "GLOBAL_FOCUS";
    browsePanelId = null;
    syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
  }

  // -------------------------
  // Keyboard mapping (dev stand-in)
  // -------------------------
  function keyToLocal(e) {
    if (e.key === "]") return { intent: "ui.focus.next" };
    if (e.key === "[") return { intent: "ui.focus.prev" };
    if (e.key === "Enter") return { intent: "ui.ok" };
    if (e.key === "Escape") return { intent: "ui.cancel" };

    if (e.key === "ArrowDown") return { intent: "ui.browse.delta", params: { delta: +1 } };
    if (e.key === "ArrowUp") return { intent: "ui.browse.delta", params: { delta: -1 } };

    if (e.key === "ArrowLeft") return { intent: "ui.modal.focus", params: { dir: -1 } };
    if (e.key === "ArrowRight") return { intent: "ui.modal.focus", params: { dir: +1 } };
    if (e.key === "Tab") return { intent: "ui.modal.focus", params: { dir: e.shiftKey ? -1 : +1 } };

    return null;
  }

  const LOCAL_INTENTS = new Set([
    "ui.focus.next",
    "ui.focus.prev",
    "ui.ok",
    "ui.cancel",
    "ui.browse.delta",
    "ui.modal.focus",
  ]);

  function handleGlobalFocusIntent(intent, params) {
    if (intent === "ui.focus.next") {
      nav.panelNext();
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
      return;
    }
    if (intent === "ui.focus.prev") {
      nav.panelPrev();
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
      return;
    }

    if (intent === "ui.cancel") {
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
      return nav.clearFocus();
    }

    if (intent === "ui.ok") {
      const pid = getActivePanelId();
      if (!pid) return;

      const p = bundle.panelsById[pid];
      if (p && isBrowseCapableType(p.type)) {
        enterBrowseIfCapable();
        return;
      }

      const def = getPanelDefaultAction(pid);
      if (def && isAllowed(def.intent)) {
        return emitIntent(def.intent, def.params);
      }

      return;
    }
  }

  function handleBrowseIntent(intent, params) {
    if (intent === "ui.cancel") return exitBrowse();

    if (intent === "ui.ok") {
      const slot = browsePanelId ? (slotByPanelId.get(browsePanelId) || null) : null;
      if (slot) slot.dispatchEvent(new CustomEvent("rt-browse-ok"));
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
      return;
    }

    if (intent === "ui.browse.delta") {
      const delta = Number(params?.delta ?? 0);
      if (!Number.isFinite(delta) || delta === 0) return;

      const slot = browsePanelId ? (slotByPanelId.get(browsePanelId) || null) : null;
      dispatchBrowseDelta(slot, delta);
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
      return;
    }
  }

  function handleModalIntent(intent, params) {
    const mroot = document.getElementById("rt_modal_root");

    if (intent === "ui.cancel") return _activeModal?.cancel?.();

    if (intent === "ui.ok") {
      if (!mroot) return _activeModal?.ok?.();

      const active = document.activeElement;

      if (active && active.closest && active.closest("#rt_modal_root")) {
        if (active.classList?.contains("rt-btn-cancel")) return _activeModal?.cancel?.();
        if (active.classList?.contains("rt-btn-ok")) return _activeModal?.ok?.();
      }

      return _activeModal?.ok?.();
    }

    if (intent === "ui.modal.focus") {
      const dir = Number(params?.dir ?? 0);
      if (!mroot) return;

      const btns = Array.from(mroot.querySelectorAll("button.rt-btn"));
      if (btns.length === 0) return;

      const active = document.activeElement;
      let idx = btns.findIndex((b) => b === active);
      if (idx < 0) idx = btns.findIndex((b) => b.classList.contains("rt-btn-ok"));
      if (idx < 0) idx = 0;

      const next = (idx + (dir > 0 ? 1 : -1) + btns.length) % btns.length;
      btns[next]?.focus?.();
      return;
    }
  }

  window.addEventListener("keydown", (e) => {
    const mapped = keyToLocal(e);
    if (!mapped) return;

    const intent = mapped.intent;
    const params = mapped.params || null;

    e.preventDefault();

    if (!LOCAL_INTENTS.has(intent) && !isAllowed(intent)) return;

    if (navMode === "MODAL_DIALOG") return handleModalIntent(intent, params);
    if (navMode === "PANEL_BROWSE") return handleBrowseIntent(intent, params);
    return handleGlobalFocusIntent(intent, params);
  });

  // -------------------------
  // Panel mounting + refresh
  // -------------------------
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const panelId = slot.dataset.panelId;
    const bodyEl = slot.querySelector(".rt-slot-body") || slot;

    const panel = bundle.panelsById[panelId];
    if (!panel) return renderPanelError(bodyEl, { title: "Missing panel", detail: panelId });

    const renderer = registry.get(panel.type);
    if (!renderer) return renderPanelError(bodyEl, { title: "No renderer", detail: panel.type });

    startPanelRefresh({
      slot,
      panel,
      bindings: coerceBindings(panel),
      store,
      render: (data) => {
        renderer(bodyEl, panel, data);

        if (navMode === "PANEL_BROWSE" && browsePanelId === panelId) {
          queueMicrotask(() =>
            syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId })
          );
        }
      },
    });
  });
})();