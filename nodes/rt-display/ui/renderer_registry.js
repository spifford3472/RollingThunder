import { renderPanelError } from "./renderers/panel_error.js";
import { renderDeployDriftSummary } from "./renderers/deploy_drift_summary.js";
import { renderTopbarCore } from "./renderers/topbar_core.js";
import { renderAlertsOverlay } from "./renderers/alerts_overlay.js";
import { renderNodeHealthSummary } from "./renderers/node_health_summary.js";
import { renderRadioStatus } from "./renderers/radio_status.js";
import { renderControllerServicesSummary } from  "./renderers/controller_services_summary.js";
import { renderWpsdStatus } from "./wpsd_status.js";

/**
 * createRendererRegistry()
 * Returns a Map of panel.type -> renderer(container, panel, data).
 * Runtime uses this to look up renderers. Unknown types fall back to panel_error.
 */
export function createRendererRegistry() {
  /** @type {Map<string, Function>} */
  const map = new Map();

  // Core
  map.set("topbar_core", (container, panel, data) =>
    renderTopbarCore(container, panel, data)
  );

  // Panels
  map.set("deploy_drift_summary", (container, panel, data) =>
    renderDeployDriftSummary(container, panel, data)
  );

  map.set("alerts_overlay", (container, panel, data) =>
    renderAlertsOverlay(container, panel, data)
  );

  map.set("controller_services_summary", (container, panel, data) =>
    renderControllerServicesSummary(container, panel, data)
  );

  // Optional aliases (if config uses shorter type strings)
  map.set("topbar", (container, panel, data) =>
    renderTopbarCore(container, panel, data)
  );
  map.set("alerts", (container, panel, data) =>
    renderAlertsOverlay(container, panel, data)
  );

  map.set("node_health_summary", (container, panel, data) =>
    renderNodeHealthSummary(container, panel, data)
  );

  map.set("node_health", (container, panel, data) =>
    renderNodeHealthSummary(container, panel, data)
  );

  map.set("radio_status", (container, panel, data) =>
    renderRadioStatus(container, panel, data)
);

  map.set("wpsd_status", (container, panel, data) =>
    renderRadioStatus(container, panel, data)
);

  return map;
}

/**
 * Convenience: return a renderer function for a panel type.
 * Keeps call sites simple if some code prefers direct lookup.
 */
export function getRenderer(panelType) {
  const type = String(panelType || "").trim();
  const reg = createRendererRegistry();
  return reg.get(type) || ((container, panel, data) => renderPanelError(container, panel, data));
}
