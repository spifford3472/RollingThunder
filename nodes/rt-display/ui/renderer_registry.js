import { renderPanelError } from "./renderers/panel_error.js";
import { renderDeployDriftSummary } from "./renderers/deploy_drift_summary.js";
import { renderTopbarCore } from "./renderers/topbar_core.js";
import { renderAlertsOverlay } from "./renderers/alerts_overlay.js";
import { renderNodeHealthSummary } from "./renderers/node_health_summary.js";
import { renderRadioStatus } from "./renderers/radio_status.js";
import { renderControllerServicesSummary } from  "./renderers/controller_services_summary.js";
import { renderPotaBandsSummary } from "./renderers/pota_bands_summary.js";
import { renderPotaSpotsSummary } from "./renderers/pota_spots_summary.js";
import { renderPotaParksSummary } from "./renderers/pota_parks_summary.js";
import { renderHfBandsSummary } from "./renderers/hf_bands_summary.js";
import { renderHfSpotsSummary } from "./renderers/hf_spots_summary.js";
import { renderHfMapSummary } from "./renderers/hf_map_summary.js";
import { renderHfDetailSummary } from "./renderers/hf_detail_summary.js";
import { renderRfSolarSummary } from "./renderers/rf_solar_summary.js";
import { renderRfBandRecommendations } from "./renderers/rf_band_recommendations.js";
import { renderRfDxMapSummary } from "./renderers/rf_dx_map_summary.js";
import { renderRfAdvisorSummary } from "./renderers/rf_advisor_summary.js";

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

  // POTA Panels
  map.set("pota_bands_summary", (container, panel, data) =>
    renderPotaBandsSummary(container, panel, data)
  );

  map.set("pota_spots_summary", (container, panel, data) =>
    renderPotaSpotsSummary(container, panel, data)
  );

  map.set("pota_parks_summary", (container, panel, data) =>
    renderPotaParksSummary(container, panel, data)
  );

  // HF Panels
  map.set("hf_bands_summary", (container, panel, data) =>
    renderHfBandsSummary(container, panel, data)
  );

  map.set("hf_spots_summary", (container, panel, data) =>
    renderHfSpotsSummary(container, panel, data)
  );

  map.set("hf_map_summary", (container, panel, data) =>
    renderHfMapSummary(container, panel, data)
  );

  map.set("hf_detail_summary", (container, panel, data) =>
    renderHfDetailSummary(container, panel, data)
  );

  // RF Intel Panels
  map.set("rf_solar_summary", (container, panel, data) =>
    renderRfSolarSummary(container, panel, data)
  );

  map.set("rf_band_recommendations", (container, panel, data) =>
    renderRfBandRecommendations(container, panel, data)
  );

  map.set("rf_dx_map_summary", (container, panel, data) =>
    renderRfDxMapSummary(container, panel, data)
  );

  map.set("rf_advisor_summary", (container, panel, data) =>
    renderRfAdvisorSummary(container, panel, data)
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
