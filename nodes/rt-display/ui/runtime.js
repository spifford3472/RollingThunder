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

function syncBrowseIndicator() {
  // If not browsing, ensure nothing claims browse
  if (navMode !== "PANEL_BROWSE" || !browsePanelId) {
    clearAllBrowseIndicators(root);
    return;
  }

  // Re-assert pill on the browse owner slot (even if header was rebuilt)
  clearAllBrowseIndicators(root);
  const slot = slotByPanelId.get(browsePanelId) || null;
  setBrowseIndicator(slot, true);
}
// ---------------------------------------------------------------

(async function main() {
  const params = new URLSearchParams(location.search);
  const pageId = params.get("page") || "home";

  const root = document.getElementById("rt_mount") || document.body;

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

  function getPanelDefaultAction(panelId) {
    const panel = bundle.panelsById[panelId];
    const actions = Array.isArray(panel?.actions) ? panel.actions : [];
    if (actions.length !== 1) return null;
    const a = actions[0];
    const intent = String(a?.intent || "").trim();
    if (!intent) return null;
    return { intent, params: a?.params || null };
  }

  // -------------------------
  // Local nav state machine (Option A)
  // -------------------------
  let navMode = "GLOBAL_FOCUS"; // GLOBAL_FOCUS | PANEL_BROWSE | MODAL_DIALOG (reserved)
  let browsePanelId = null; // <-- NEW: which panel owns browse right now
  
  function getActivePanelId() {
    const s = nav.getState();
    return s?.activePanelId || null;
  }

  function getActiveSlotEl() {
    const pid = getActivePanelId();
    return pid ? (slotByPanelId.get(pid) || null) : null;
  }

  function enterBrowseIfCapable() {
    const pid = getActivePanelId();
    if (!pid) return false;

    const p = bundle.panelsById[pid];
    if (!p) return false;
    if (!isBrowseCapableType(p.type)) return false;

    navMode = "PANEL_BROWSE";
    browsePanelId = pid;

    syncBrowseIndicator();
    return true;
  }

  function exitBrowse() {
    navMode = "GLOBAL_FOCUS";
    browsePanelId = null;
    syncBrowseIndicator();
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

    return null;
  }

  const LOCAL_INTENTS = new Set([
    "ui.focus.next",
    "ui.focus.prev",
    "ui.ok",
    "ui.cancel",
    "ui.browse.delta",
  ]);

  function handleGlobalFocusIntent(intent, params) {
    if (intent === "ui.focus.next") {
      nav.panelNext();
      // If we were browsing, moving focus should also clear browse indicator; but in GLOBAL_FOCUS it should be off.
      syncBrowseIndicator();
      return;
    }
    if (intent === "ui.focus.prev") {
      nav.panelPrev();
      syncBrowseIndicator();
      return;
    }

    if (intent === "ui.cancel") {
      syncBrowseIndicator();
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
    if (intent === "ui.ok") return;

    if (intent === "ui.browse.delta") {
      const delta = Number(params?.delta ?? 0);
      if (!Number.isFinite(delta) || delta === 0) return;

      const slot = browsePanelId ? (slotByPanelId.get(browsePanelId) || null) : null;
      dispatchBrowseDelta(slot, delta);
      syncBrowseIndicator(); // optional, but nice: keeps pill asserted if DOM changed
      return;
    }

    // ignore focus.next/prev while browsing
  }

  function handleModalIntent(intent, params) {
    if (intent === "ui.cancel") {
      navMode = "PANEL_BROWSE";
      return;
    }
    if (intent === "ui.ok") {
      navMode = "PANEL_BROWSE";
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

        // BUGFIX: panel refreshes may rebuild DOM; re-assert runtime-owned browse pill
        // only when that panel is the current browse owner
        if (navMode === "PANEL_BROWSE" && browsePanelId === panelId) {
          // Defer one tick in case renderer’s update is multi-phase
          queueMicrotask(() => syncBrowseIndicator());
        }
      },
    });
  });
})();