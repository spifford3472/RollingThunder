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
  return t === "controller_services_summary";
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
      badge.className = "rt-pill warn"; // reuses existing pill styles
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

  // Not browsing? Ensure nothing claims browse.
  if (navMode !== "PANEL_BROWSE" || !browsePanelId) {
    clearAllBrowseIndicators(rootEl);
    return;
  }

  // Browsing: clear then re-assert on the browse owner slot.
  clearAllBrowseIndicators(rootEl);
  const slot = slotByPanelId?.get(browsePanelId) || null;
  setBrowseIndicator(slot, true);
}
// ---------------------------------------------------------------

(async function main() {
  const params = new URLSearchParams(location.search);
  const pageId = params.get("page") || "home";

  const root = document.getElementById("rt_mount") || document.body;

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

  function openConfirmModal({ title, body, confirmLabel = "OK", cancelLabel = "Cancel", onConfirm, onCancel }) {
    const mroot = ensureModalRoot();
    mroot.innerHTML = `
      <div class="rt-modal-backdrop"></div>
      <div class="rt-modal" role="dialog" aria-modal="true">
        <div class="rt-modal-title">${title}</div>
        <div class="rt-modal-body">${body}</div>
        <div class="rt-modal-actions">
          <button class="rt-btn rt-btn-cancel">${cancelLabel}</button>
          <button class="rt-btn rt-btn-ok">${confirmLabel}</button>
        </div>
      </div>
    `;

    const prevNavMode = navMode;
    navMode = "MODAL_DIALOG";

    const okBtn = mroot.querySelector(".rt-btn-ok");
    const cancelBtn = mroot.querySelector(".rt-btn-cancel");

    function close() {
      mroot.innerHTML = "";
      _activeModal = null;

      // Restore mode we came from (browse stays browse, global stays global)
      navMode = prevNavMode;

      // Re-assert browse indicator if appropriate
      syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId });
    }

    async function ok() {
      try { await onConfirm?.(); } finally { close(); }
    }

    function cancel() {
      try { onCancel?.(); } finally { close(); }
    }

    _activeModal = { ok, cancel, close, prevNavMode };

    okBtn?.addEventListener("click", ok);
    cancelBtn?.addEventListener("click", cancel);

    okBtn?.focus?.();
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

  // Build slot map panelId -> slot element
  const slotByPanelId = new Map();
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const pid = String(slot.dataset.panelId || "").trim();
    if (pid) slotByPanelId.set(pid, slot);
  });

  const presentPanelIds = buildPresentPanelIds(layout);
  const { focusOrder, initialPanelId } = buildFocusModel({ page, bundle, presentPanelIds });

  nav.setPageModel({
    focusablePanelIds: focusOrder,
    slotByPanelId,
    initialPanelId,
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
  root.addEventListener("rt-open-modal", (ev) => {
    const d = ev?.detail || {};
    if (d.kind !== "confirm") return;

    openConfirmModal({
      title: String(d.title || "Confirm"),
      body: String(d.body || ""),
      confirmLabel: String(d.confirmLabel || "OK"),
      cancelLabel: String(d.cancelLabel || "Cancel"),
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

    // NEW: modal focus cycling
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
    "ui.modal.focus", // NEW
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
    if (intent === "ui.cancel") return _activeModal?.cancel?.();
    if (intent === "ui.ok") return _activeModal?.ok?.();

    if (intent === "ui.modal.focus") {
      const dir = Number(params?.dir ?? 0);
      const mroot = document.getElementById("rt_modal_root");
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

        // Re-assert runtime-owned browse pill if panel refresh rebuilds DOM
        if (navMode === "PANEL_BROWSE" && browsePanelId === panelId) {
          queueMicrotask(() =>
            syncBrowseIndicator({ rootEl: root, navMode, browsePanelId, slotByPanelId })
          );
        }
      },
    });
  });
})();