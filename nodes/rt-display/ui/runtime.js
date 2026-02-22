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
  layout.top.forEach(id => regions.top.appendChild(mkSlot(id)));

  // middle columns (max 3)
  layout.middle.slice(0, 3).forEach(colPanels => {
    const col = document.createElement("div");
    col.className = "rt-col";
    (Array.isArray(colPanels) ? colPanels : []).forEach(id => col.appendChild(mkSlot(id)));
    regions.mid.appendChild(col);
  });

  // bottom
  layout.bottom.forEach(id => regions.bot.appendChild(mkSlot(id)));

  const registry = createRendererRegistry();
  const store = createBindingStore();

  // --- NAV v1: roving focus (panel highlight only) ---
  const nav = createNavMachine();

  // Build slot map panelId -> slot element
  const slotByPanelId = new Map();
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const pid = String(slot.dataset.panelId || "").trim();
    if (pid) slotByPanelId.set(pid, slot);
  });

  // Focusable panels in deterministic visual order:
  // Use DOM order of slots (top -> mid columns -> bottom, already built that way)
  const focusablePanelIds = [];
  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const panelId = String(slot.dataset.panelId || "").trim();
    const panel = bundle.panelsById[panelId];
    if (panel && panel.focusable === true) focusablePanelIds.push(panelId);
  });

  // Determine initial focus using page.focusPolicy
  const fp = page.focusPolicy || null;

  // Helper: normalize list of ids
  const normIds = (arr) => (Array.isArray(arr) ? arr.map(x => String(x||"").trim()).filter(Boolean) : []);

  let initialPanelId = null;

  if (fp) {
    const rotation = normIds(fp.rotation);
    const def = String(fp.defaultPanel || "").trim();

    if (rotation.length > 0) {
      // Prefer rotation order: pick first focusable in that rotation
      const rotFirst = rotation.find(id => focusablePanelIds.includes(id));
      initialPanelId = rotFirst || null;

      // Also: override focus order itself to the rotation (filtered to focusables)
      const rotated = rotation.filter(id => focusablePanelIds.includes(id));
      // If rotated is empty, we still start with no focus.
      // Swap focusablePanelIds to the rotated list.
      focusablePanelIds.length = 0;
      rotated.forEach(id => focusablePanelIds.push(id));
    } else if (def) {
      // defaultPanel provided: focus it if it is focusable
      initialPanelId = focusablePanelIds.includes(def) ? def : null;
    } else {
      // focusPolicy exists but is empty => explicit "no focus"
      initialPanelId = null;
      // keep focusablePanelIds as-is, but don't auto-focus
    }
  } else {
    // No focusPolicy: keep v1 behavior of auto-focusing first focusable
    initialPanelId = focusablePanelIds[0] || null;
  }

  nav.setPageModel({ focusablePanelIds, slotByPanelId, initialPanelId });

  // Keyboard: [ and ] as prev/next panel for now (won't collide much)
  window.addEventListener("keydown", (e) => {
    if (e.key === "]") { e.preventDefault(); nav.panelNext(); }
    if (e.key === "[") { e.preventDefault(); nav.panelPrev(); }
  });
  // --- end NAV v1 ---

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
