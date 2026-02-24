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
// - Intents are gated by page.controls.allowedIntents.
// - This file does NOT write Redis and does NOT implement business logic.
// - Keyboard is a dev stand-in for ESP32 controls.
//
// NAV v2:
// - LOCAL input capture state machine:
//     GLOBAL_FOCUS  -> (OK on browse-capable panel) -> PANEL_BROWSE
//     PANEL_BROWSE  -> (CANCEL) -> GLOBAL_FOCUS
// - Browse delivers per-tick deltas to the focused panel slot via:
//     slot.dispatchEvent(new CustomEvent("rt-browse-delta", { detail: { delta } }))
//
// Current browse-capable panel(s):
// - controller_services_summary  (windowed list scroll)
//
// Expand later by adding more types to isBrowseCapableType().

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

// Helper: normalize list of ids
function normIds(arr) {
  return Array.isArray(arr) ? arr.map((x) => String(x || "").trim()).filter(Boolean) : [];
}

function buildPresentPanelIds(layout) {
  const out = [];

  // top
  for (const id of normIds(layout?.top)) out.push(id);

  // middle columns (max 3)
  const mid = Array.isArray(layout?.middle) ? layout.middle.slice(0, 3) : [];
  for (const colPanels of mid) {
    for (const id of normIds(Array.isArray(colPanels) ? colPanels : [])) out.push(id);
  }

  // bottom
  for (const id of normIds(layout?.bottom)) out.push(id);

  return out;
}

function buildFocusModel({ page, bundle, presentPanelIds }) {
  // focusable panels present on the page
  const focusablesPresent = presentPanelIds.filter((panelId) => {
    const p = bundle.panelsById[panelId];
    return p && p.focusable === true;
  });

  const fp = page?.focusPolicy || null;
  const rotation = normIds(fp?.rotation);
  const def = String(fp?.defaultPanel || "").trim() || null;

  let focusOrder = focusablesPresent.slice(); // default: visual order
  let initialPanelId = null;

  if (fp) {
    if (rotation.length > 0) {
      // Focus order is authoritative rotation order, filtered to focusables-present.
      focusOrder = rotation.filter((id) => focusablesPresent.includes(id));

      // Initial focus: first focusable in rotation
      const rotFirst = rotation.find((id) => focusablesPresent.includes(id)) || null;
      initialPanelId = rotFirst;
    } else if (def) {
      // No rotation, but defaultPanel provided: focus it if it's focusable-present
      initialPanelId = focusablesPresent.includes(def) ? def : null;
      // Keep focusOrder as visual order
    } else {
      // focusPolicy exists but empty => explicit "no focus"
      initialPanelId = null;
    }
  } else {
    // No focusPolicy: legacy behavior—auto focus first focusable (if any)
    initialPanelId = focusOrder[0] || null;
  }

  return { focusOrder, initialPanelId };
}

function isBrowseCapableType(panelType) {
  const t = String(panelType || "").trim();
  // Today: only the services list is browse-scrollable.
  // Later: expand this list or move to a registry capability map.
  return (t === "controller_services_summary");
}

function dispatchBrowseDelta(slotEl, delta) {
  if (!slotEl) return;
  slotEl.dispatchEvent(new CustomEvent("rt-browse-delta", { detail: { delta } }));
}

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

  // top
  layout.top.forEach((id) => regions.top.appendChild(mkSlot(id)));

  // middle columns (max 3)
  layout.middle.slice(0, 3).forEach((colPanels) => {
    const col = document.createElement("div");
    col.className = "rt-col";
    (Array.isArray(colPanels) ? colPanels : []).forEach((id) => col.appendChild(mkSlot(id)));
    regions.mid.appendChild(col);
  });

  // bottom
  layout.bottom.forEach((id) => regions.bot.appendChild(mkSlot(id)));

  const registry = createRendererRegistry();
  const store = createBindingStore();

  // NAV: deterministic roving focus
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
        // keep deterministic; transport not present in this build
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

  // Default action for "action panels":
  // If panel declares exactly one action, OK triggers it (when allowed by page).
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
  // Local navigation mode (deterministic)
  // -------------------------
  let navMode = "GLOBAL_FOCUS"; // GLOBAL_FOCUS | PANEL_BROWSE | MODAL_DIALOG (reserved)

  function activePanelId() {
    const s = nav.getState();
    return s?.activePanelId || null;
  }

  function activeSlotEl() {
    const pid = activePanelId();
    return pid ? (slotByPanelId.get(pid) || null) : null;
  }

  function enterBrowse() {
    const pid = activePanelId();
    if (!pid) return false;

    const p = bundle.panelsById[pid];
    if (!p) return false;

    if (!isBrowseCapableType(p.type)) return false;

    navMode = "PANEL_BROWSE";
    return true;
  }

  function exitBrowse() {
    navMode = "GLOBAL_FOCUS";
  }

  // -------------------------
  // Keyboard mapping (dev stand-in)
  // -------------------------
  // Minimal mapping:
  //   [ ]    -> focus prev/next
  //   Enter  -> OK
  //   Escape -> CANCEL
  // Debug browse mapping:
  //   ArrowUp / ArrowDown -> browse delta (-1 / +1) in PANEL_BROWSE only
  function keyToIntent(e) {
    if (e.key === "]") return "ui.focus.next";
    if (e.key === "[") return "ui.focus.prev";
    if (e.key === "Enter") return "ui.ok";
    if (e.key === "Escape") return "ui.cancel";
    if (e.key === "ArrowDown") return "ui.browse.delta";
    if (e.key === "ArrowUp") return "ui.browse.delta";
    return null;
  }

  function handleGlobalFocus(intent) {
    if (!isAllowed(intent)) return;

    if (intent === "ui.focus.next") return nav.panelNext();
    if (intent === "ui.focus.prev") return nav.panelPrev();

    if (intent === "ui.cancel") {
      // Deterministic, low-risk default: cancel clears focus in GLOBAL_FOCUS.
      return nav.clearFocus();
    }

    if (intent === "ui.ok") {
      const pid = activePanelId();
      if (!pid) return;

      // Option A rule: browse-capable panels are browse-first.
      // OK enters browse mode even if the panel has a single default action.
      const p = bundle.panelsById[pid];
      if (p && isBrowseCapableType(p.type)) {
        enterBrowse();
        return;
      }

      // Non-browse panels: if exactly one action, OK triggers it (if allowed).
      const def = getPanelDefaultAction(pid);
      if (def && isAllowed(def.intent)) {
        return emitIntent(def.intent, def.params);
      }

      // Otherwise: no-op for now (or future modal entry for non-browse panels).
      return;  
    }
  }

  function handleBrowse(intent, rawKey) {
    if (!isAllowed(intent)) return;

    if (intent === "ui.cancel") return exitBrowse();

    if (intent === "ui.ok") {
      // Modal transactional flow is next. For now, OK is intentionally a no-op in browse.
      return;
    }

    if (intent === "ui.browse.delta") {
      const slot = activeSlotEl();
      const delta = (rawKey === "ArrowUp") ? -1 : +1;
      dispatchBrowseDelta(slot, delta);
      return;
    }
  }

  function handleModal(intent) {
    if (!isAllowed(intent)) return;
    // Reserved for transactional modal work; keep deterministic for now.
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
    const intent = keyToIntent(e);
    if (!intent) return;

    e.preventDefault();

    if (!isAllowed(intent)) return;

    if (navMode === "MODAL_DIALOG") return handleModal(intent);
    if (navMode === "PANEL_BROWSE") return handleBrowse(intent, e.key);
    return handleGlobalFocus(intent);
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
      render: (data) => renderer(bodyEl, panel, data),
    });
  });
})();