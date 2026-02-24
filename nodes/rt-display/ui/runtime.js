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

  // middle columns (max 3), keep declared order col0 -> col1 -> col2, and within col keep order
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

      // Initial focus: first focusable in rotation (unless explicit defaultPanel also present)
      // (If you want defaultPanel to override rotation-first, flip this priority.)
      const rotFirst = rotation.find((id) => focusablesPresent.includes(id)) || null;
      initialPanelId = rotFirst;

      // If rotation exists but yields empty focusOrder, that means "no focusables here"
      // and nav will start with no focus.
    } else if (def) {
      // No rotation, but defaultPanel provided: focus it if it's focusable-present
      initialPanelId = focusablesPresent.includes(def) ? def : null;
      // Keep focusOrder as visual order
    } else {
      // focusPolicy exists but empty => explicit "no focus"
      initialPanelId = null;
      // Keep focusOrder as visual order (still useful for manual [ ] cycling if user wants)
    }
  } else {
    // No focusPolicy: legacy behavior—auto focus first focusable (if any)
    initialPanelId = focusOrder[0] || null;
  }

  return { focusOrder, initialPanelId };
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

  // Build slots deterministically from declared layout
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

  // NAV: deterministic roving focus + (future) browse/modal capture
  const nav = createNavMachine();

  // Build slot map panelId -> slot element
  const slotByPanelId = new Map();
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const pid = String(slot.dataset.panelId || "").trim();
    if (pid) slotByPanelId.set(pid, slot);
  });

  // Present panel ids from page.layout in deterministic visual order
  const presentPanelIds = buildPresentPanelIds(layout);

  // Focus order + initial focus from focusPolicy (authoritative)
  const { focusOrder, initialPanelId } = buildFocusModel({ page, bundle, presentPanelIds });

  // Set page model (pageId enables per-page remembered focus if nav_machine supports it)
  nav.setPageModel({
    pageId: page.id || pageId,
    focusablePanelIds: focusOrder,
    slotByPanelId,
    initialPanelId,
    rememberFocus: true,
  });

  // -------------------------
  // Input routing (intent-based)
  // -------------------------

  // Page-declared allowed intents (authoritative)
  const allowedIntents = new Set(
    Array.isArray(page?.controls?.allowedIntents) ? page.controls.allowedIntents : []
  );

  function isAllowed(intent) {
    return allowedIntents.has(intent);
  }

  // Minimal key -> intent mapping (keyboard is a dev stand-in for ESP32 controls)
  function keyToIntent(e) {
    if (e.key === "]") return "ui.focus.next";
    if (e.key === "[") return "ui.focus.prev";
    if (e.key === "Enter") return "ui.ok";
    if (e.key === "Escape") return "ui.cancel";
    return null;
  }

  // Emit an intent to controller path.
  // NOTE: transport is intentionally NOT invented here.
  async function emitIntent(intent, params = null) {
    const s = nav.getState();
    const res = await store.publishIntent({
      intent,
      params,
      pageId: s.pageId || (page.id || pageId),
      panelId: s.activePanelId || null,
      source: "rt-display"
    });

    if (!res.ok) console.warn("publishIntent failed", res.err, res.meta);
    return res;
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

  function handleNavIntent(intent) {
    if (!isAllowed(intent)) return;

    if (intent === "ui.focus.next") return nav.panelNext();
    if (intent === "ui.focus.prev") return nav.panelPrev();

    if (intent === "ui.cancel") {
      // Deterministic, low-risk default:
      // Cancel clears focus when in global focus.
      // (If you prefer cancel to be a no-op here, swap this out.)
      return nav.clearFocus();
    }

    if (intent === "ui.ok") {
      const s = nav.getState();
      const active = s.activePanelId;

      if (!active) return;

      // Action panels: if exactly one action, run it (if allowed).
      const def = getPanelDefaultAction(active);
      if (def && isAllowed(def.intent)) {
        return emitIntent(def.intent, def.params);
      }

      // Otherwise, enter browse mode (panel capture).
      // Browse semantics are panel-owned and will be wired later.
      nav.beginBrowse?.();
      return;
    }
  }

  function handlePanelIntent(panelId, intent) {
    if (!isAllowed(intent)) return;

    // Minimal browse semantics until panel controllers exist:
    if (intent === "ui.cancel") return nav.endBrowse?.();

    if (intent === "ui.ok") {
      console.debug("[panel]", panelId, "OK (browse) — TODO: dispatch to panel controller / open modal");
      return;
    }

    // Future: route rotary/up/down etc. through the same intent path once defined.
  }

  function handleModalIntent(modalId, intent) {
    if (!isAllowed(intent)) return;

    if (intent === "ui.ok") {
      console.debug("[modal]", modalId, "commit — TODO: modal commit semantics");
      return nav.closeModal?.();
    }
    if (intent === "ui.cancel") {
      console.debug("[modal]", modalId, "cancel — TODO: modal rollback semantics");
      return nav.closeModal?.();
    }
  }

  window.addEventListener("keydown", (e) => {
    const intent = keyToIntent(e);
    if (!intent) return;

    // prevent browser default behavior for our control keys
    e.preventDefault();

    // Gate early by allowedIntents (authoritative)
    if (!isAllowed(intent)) return;

    const owner = nav.getInputOwner?.() || { owner: "nav", id: nav.getState().activePanelId, state: "GLOBAL_FOCUS" };

    if (owner.owner === "modal") return handleModalIntent(owner.id, intent);
    if (owner.owner === "panel") return handlePanelIntent(owner.id, intent);
    return handleNavIntent(intent);
  });

  // -------------------------
  // Panel mounting + refresh
  // -------------------------

  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const panelId = slot.dataset.panelId;

    // Renderer-owned body (never render into slot root)
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