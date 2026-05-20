// rf_band_recommendations.js
// PURE RENDERER — displays controller/projector-owned RF Intel band model only.
// No browser-owned decisions, no Redis access, no API calls, no polling, no intent execution.

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

function unwrapItems(value) {
  const obj = unwrapObject(value);

  if (Array.isArray(value)) return value;
  if (Array.isArray(obj.items)) return obj.items;
  if (Array.isArray(obj.bands)) return obj.bands;
  if (Array.isArray(obj.rows)) return obj.rows;

  return [];
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderObject(model) {
  return Object.entries(model)
    .filter(([key]) => !String(key).startsWith("_"))
    .map(([key, value]) => {
      const displayValue =
        value && typeof value === "object"
          ? JSON.stringify(value)
          : value;

      return `
        <div class="rt-kv-row">
          <div class="rt-kv-key">${esc(key)}</div>
          <div class="rt-kv-value">${esc(displayValue)}</div>
        </div>
      `;
    })
    .join("");
}

function renderItem(item) {
  if (item && typeof item === "object") {
    const label = item.label || item.band || item.name || item.id || "Band";
    const detail = item.detail || item.reason || item.status || item.message || "";

    return `
      <div class="rt-list-row">
        <div class="rt-strong">${esc(label)}</div>
        ${detail ? `<div class="rt-muted">${esc(detail)}</div>` : ""}
      </div>
    `;
  }

  return `<div class="rt-list-row">${esc(item)}</div>`;
}

export function renderRfBandRecommendations(container, panel, data) {
  const bands = unwrapObject(data?.bands);
  const items = unwrapItems(data?.bands);

  if (items.length > 0) {
    container.innerHTML = `
      <div class="rt-panel-section">
        <div class="rt-panel-title">Band Recommendations</div>
        <div class="rt-panel-body">
          ${items.map(renderItem).join("")}
        </div>
      </div>
    `;
    return;
  }

  if (Object.keys(bands).length > 0) {
    container.innerHTML = `
      <div class="rt-panel-section">
        <div class="rt-panel-title">Band Recommendations</div>
        <div class="rt-panel-body">
          ${renderObject(bands)}
        </div>
      </div>
    `;
    return;
  }

  container.innerHTML = `<div class="rt-muted">Waiting for projected band recommendation data</div>`;
}