// hf_map_summary.js
// PURE RENDERER — placeholder for controller/projector-owned HF map/location data.
// No browser geocoding, no location calculation, no Redis writes, no intent execution.

function unwrapObject(value) {
  if (!value) return {};

  if (
    typeof value === "object" &&
    value.value &&
    typeof value.value === "object" &&
    !Array.isArray(value.value)
  ) {
    return value.value;
  }

  if (typeof value === "object" && !Array.isArray(value)) return value;

  return {};
}

export function renderHfMapSummary(container, panel, data) {
  const spot = unwrapObject(data?.spot);

  const callsign = String(spot?.callsign || spot?.call || "").trim();

  if (!callsign) {
    container.innerHTML = `<div class="rt-muted">No selected station</div>`;
    return;
  }

  const band = String(spot?.band || "").trim();
  const freq = String(spot?.freq || "").trim();
  const mode = String(spot?.mode || "").trim();

  if (!container.__rtHfMapInit) {
    container.innerHTML = `
      <div class="rt-detail">
        <div class="rt-hf-map-call" style="font-size:1.3em; font-weight:700;"></div>
        <div class="rt-muted">Location panel pending controller-provided map data.</div>

        <table style="margin-top:.5rem;">
          <tbody>
            <tr><th>Band</th><td class="rt-hf-map-band"></td></tr>
            <tr><th>Freq</th><td class="rt-hf-map-freq"></td></tr>
            <tr><th>Mode</th><td class="rt-hf-map-mode"></td></tr>
          </tbody>
        </table>
      </div>
    `;
    container.__rtHfMapInit = true;
  }

  container.querySelector(".rt-hf-map-call").textContent = callsign;
  container.querySelector(".rt-hf-map-band").textContent = band || "-";
  container.querySelector(".rt-hf-map-freq").textContent = freq || "-";
  container.querySelector(".rt-hf-map-mode").textContent = mode || "-";
}