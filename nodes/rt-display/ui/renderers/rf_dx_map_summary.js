// rf_dx_map_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel map model only.
// No browser geocoding, no map API calls, no Redis access, no polling, no intent execution.

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

function safeProjectedImageUrl(value) {
  const s = String(value ?? "").trim();

  // Renderer-only safety: display only URLs already projected by the controller/service.
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

function renderKeyValueRows(model) {
  return Object.entries(model)
    .filter(([key]) => !String(key).startsWith("_"))
    .filter(([key]) => key !== "image_url" && key !== "map_url" && key !== "url")
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

export function renderRfDxMapSummary(container, panel, data) {
  const map = unwrapObject(data?.map);

  if (Object.keys(map).length === 0) {
    container.innerHTML = `<div class="rt-muted">Waiting for projected DX map data</div>`;
    return;
  }

  const imageUrl = safeProjectedImageUrl(map.image_url || map.map_url || map.url);

  container.innerHTML = `
    <div class="rt-panel-section">
      <div class="rt-panel-title">DX / Propagation Map</div>
      <div class="rt-panel-body">
        ${
          imageUrl
            ? `<img class="rt-hf-map-image" src="${esc(imageUrl)}" alt="Projected RF Intel map">`
            : `<div class="rt-muted">No projected map image</div>`
        }
        ${renderKeyValueRows(map)}
      </div>
    </div>
  `;
}