// rf_solar_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel solar model only.
// No browser-owned RF decisions, no Redis access, no API calls, no polling, no intent execution.
// Presentation-only: formats and hides fields; does not score, infer, enrich, or correct data.

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

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function firstText(model, keys, fallback = "—") {
  for (const key of keys) {
    const value = model?.[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      return String(value).trim();
    }
  }
  return fallback;
}

function compactTime(value) {
  const s = String(value ?? "").trim();
  if (!s) return "";

  return s
    .replace("T", " ")
    .replace(/\.000Z$/, "Z")
    .replace(/\+00:00$/, "Z");
}

function safeProjectedImageUrl(value) {
  const s = String(value ?? "").trim();

  if (
    s.startsWith("/") ||
    s.startsWith("http://") ||
    s.startsWith("https://") ||
    s.startsWith("data:image/")
  ) {
    return s;
  }

  return "";
}

function renderMetric(label, value) {
  return `
    <div class="rt-rfintel-metric">
      <div class="rt-rfintel-metric-label">${esc(label)}</div>
      <div class="rt-rfintel-metric-value">${esc(value)}</div>
    </div>
  `;
}

function renderSolarVisual(imageUrl, condition, isMock) {
  if (imageUrl) {
    return `
      <div class="rt-rfintel-solar-visual-card">
        <img class="rt-rfintel-solar-image" src="${esc(imageUrl)}" alt="${esc(condition)}">
      </div>
    `;
  }

  return `
    <div class="rt-rfintel-solar-visual-card rt-rfintel-solar-visual-placeholder">
      <div class="rt-rfintel-solar-disc"></div>
      <div class="rt-rfintel-solar-grid"></div>
      <div class="rt-rfintel-solar-visual-label">
        ${isMock ? "MOCK SOLAR VISUAL" : "SOLAR VISUAL"}
      </div>
    </div>
  `;
}

export function renderRfSolarSummary(container, panel, data) {
  const solar = unwrapObject(data?.solar);

  if (Object.keys(solar).length === 0) {
    container.innerHTML = `
      <div class="rt-rfintel-panel">
        <div class="rt-rfintel-title">Solar</div>
        <div class="rt-muted">Waiting for projected solar data</div>
      </div>
    `;
    return;
  }

  const condition = firstText(
    solar,
    ["condition", "solar_condition", "status", "summary", "message"],
    "Solar data received"
  );

  const kIndex = firstText(solar, ["k_index", "k", "kp", "kp_index"], "—");
  const sfi = firstText(solar, ["sfi", "solar_flux", "flux", "solar_flux_index"], "—");
  const aIndex = firstText(solar, ["a_index", "a"], "—");
  const sunspots = firstText(solar, ["sunspot_number", "sunspots", "ssn"], "—");

  const xray = firstText(
    solar,
    ["xray", "x_ray", "xray_status", "x_ray_status"],
    "—"
  );

  const updated = compactTime(
    solar.updated_utc ||
    solar.updated_at ||
    solar.timestamp_utc ||
    solar.timestamp
  );

  const isMock = solar.mock === true || String(solar.source || "").toLowerCase().includes("mock");

  const imageUrl = safeProjectedImageUrl(
    solar.image_url ||
    solar.sun_image_url ||
    solar.solar_image_url ||
    solar.aurora_image_url ||
    solar.aurora_map_url ||
    solar.url
  );

  container.innerHTML = `
    <div class="rt-rfintel-panel rt-rfintel-solar-panel">
      <div class="rt-rfintel-title-row">
        <div class="rt-rfintel-title">Solar</div>
        ${isMock ? `<div class="rt-rfintel-badge rt-rfintel-badge-mock">MOCK</div>` : ""}
      </div>

      <div class="rt-rfintel-hero rt-rfintel-solar-condition">
        <div class="rt-rfintel-hero-label">Current Condition</div>
        <div class="rt-rfintel-hero-value">${esc(condition)}</div>
      </div>

      <div class="rt-rfintel-solar-main">
        <div class="rt-rfintel-solar-metrics">
          <div class="rt-rfintel-metric-grid rt-rfintel-solar-metric-grid">
            ${renderMetric("K Index", kIndex)}
            ${renderMetric("SFI", sfi)}
            ${renderMetric("A Index", aIndex)}
            ${renderMetric("Sunspots", sunspots)}
          </div>
        </div>
        
        <div class="rt-rfintel-solar-right">
          ${renderSolarVisual(imageUrl, condition, isMock)}
          <div class="rt-rfintel-solar-xray-card">
            <div class="rt-rfintel-solar-xray-label">X-Ray</div>
            <div class="rt-rfintel-solar-xray-value">${esc(xray)}</div>
          </div>
        </div>
      </div>

      <div class="rt-rfintel-footer rt-rfintel-solar-footer">
        ${updated ? `<span>Updated: ${esc(updated)}</span>` : ""}
      </div>
    </div>
  `;
}