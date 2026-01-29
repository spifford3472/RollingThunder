import { renderDeployDriftSummary } from "./renderers/deploy_drift_summary.js";
import { renderTopbarCore } from "./renderers/topbar_core.js";
import { renderPanelError } from "./renderers/panel_error.js";

export function createRendererRegistry() {
  const map = new Map();

  map.set("topbar_core", (container, panel, data) => {
    renderTopbarCore(container, panel, data);
  });

  map.set("deploy_drift_summary", (container, panel, data) => {
    renderDeployDriftSummary(container, panel, data);
  });

  // Temporary stub until you wire node_health_summary renderer
  map.set("node_health_summary", (container) => {
    renderPanelError(container, {
      title: "node_health_summary",
      detail: "Renderer not wired yet (stub).",
    });
  });

  return { get: (t) => map.get(t) };
}
