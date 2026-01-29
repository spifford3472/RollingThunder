window.__rt_runtime_loaded = true;

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

function mkSlot(panelId) {
  const d = document.createElement("div");
  d.className = "rt-slot";
  d.dataset.panelId = String(panelId);
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

  const root = document.querySelector(".wrap") || document.body;

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

  root.querySelectorAll(".rt-slot").forEach((slot) => {
    const panelId = slot.dataset.panelId;
    const panel = bundle.panelsById[panelId];
    if (!panel) return renderPanelError(slot, { title: "Missing panel", detail: panelId });

    const renderer = registry.get(panel.type);
    if (!renderer) return renderPanelError(slot, { title: "No renderer", detail: panel.type });

    startPanelRefresh({
      slot,
      panel,
      bindings: coerceBindings(panel),
      store,
      render: (data) => renderer(slot, panel, data),
    });
  });
})();
