// rf_dx_map_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel map model only.
// No browser geocoding, no map API calls, no Redis access, no polling, no intent execution.
// Presentation-only: formats projected fields; does not derive regions, countries, pins, or advice.

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
  if (Array.isArray(obj.rows)) return obj.rows;
  if (Array.isArray(obj.regions)) return obj.regions;
  if (Array.isArray(obj.paths)) return obj.paths;
  if (Array.isArray(obj.zones)) return obj.zones;

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

function firstText(model, keys, fallback = "") {
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

function badge(label, className = "") {
  const s = String(label ?? "").trim();
  if (!s) return "";

  return `
    <span class="rt-rfintel-badge ${className}">
      ${esc(s)}
    </span>
  `;
}

function itemLabel(item, index) {
  if (!item || typeof item !== "object") return `Region ${index + 1}`;

  return firstText(
    item,
    ["label", "region", "name", "path", "zone", "title"],
    `Region ${index + 1}`
  );
}

function itemText(item) {
  if (typeof item === "string") return item.trim();

  return firstText(
    item,
    ["status", "message", "summary", "detail", "activity", "condition"],
    ""
  );
}

function renderRegionItem(item, index) {
  const label = itemLabel(item, index);
  const text = itemText(item);

  if (!label && !text) return "";

  return `
    <div class="rt-rfintel-map-region">
      <div class="rt-rfintel-map-region-label">${esc(label)}</div>
      <div class="rt-rfintel-map-region-text">${esc(text || "—")}</div>
    </div>
  `;
}

function renderMapVisual(imageUrl, status, isMock) {
  if (imageUrl) {
    return `
      <div class="rt-rfintel-map-visual-card">
        <img class="rt-rfintel-map-image" src="${esc(imageUrl)}" alt="Projected RF Intel map">
      </div>
    `;
  }

  return `
    <div class="rt-rfintel-map-visual-card rt-rfintel-map-placeholder">
      <div class="rt-rfintel-map-grid"></div>
      <div class="rt-rfintel-map-sweep"></div>
      <div class="rt-rfintel-map-crosshair"></div>
      <div class="rt-rfintel-map-center-label">
        ${isMock ? "MOCK MAP DATA" : esc(status || "NO LIVE MAP DATA")}
      </div>
    </div>
  `;
}

export function renderRfDxMapSummary(container, panel, data) {
  const map = unwrapObject(data?.map);
  const items = unwrapItems(data?.map);

  if (Object.keys(map).length === 0 && items.length === 0) {
    container.innerHTML = `
      <div class="rt-rfintel-panel">
        <div class="rt-rfintel-title">DX / Propagation Map</div>
        <div class="rt-muted">Waiting for projected DX map data</div>
      </div>
    `;
    return;
  }

  const status = firstText(
    map,
    ["map_status", "status", "condition", "summary"],
    "Map data waiting"
  );

  const message = firstText(
    map,
    ["message", "detail", "description"],
    ""
  );

  const updated = compactTime(
    map.updated_utc ||
    map.updated_at ||
    map.timestamp_utc ||
    map.timestamp
  );

  const isMock =
    map.mock === true ||
    String(map.source || "").toLowerCase().includes("mock");

  const imageUrl = safeProjectedImageUrl(
    map.image_url ||
    map.map_url ||
    map.url
  );

  const visibleRegions = items
    .map((item, index) => renderRegionItem(item, index))
    .filter(Boolean)
    .slice(0, 4)
    .join("");

  container.innerHTML = `
    <div class="rt-rfintel-panel rt-rfintel-map-panel">
      <div class="rt-rfintel-title-row">
        <div class="rt-rfintel-title">DX / Propagation Map</div>
        <div class="rt-rfintel-badge-row">
          ${status ? badge(status) : ""}
          ${isMock ? badge("MOCK", "rt-rfintel-badge-mock") : ""}
        </div>
      </div>

      <div class="rt-rfintel-map-layout">
        <div class="rt-rfintel-map-left">
          ${renderMapVisual(imageUrl, status, isMock)}
        </div>

        <div class="rt-rfintel-map-right">
          <div class="rt-rfintel-map-status-card">
            <div class="rt-rfintel-hero-label">Status</div>
            <div class="rt-rfintel-map-status">${esc(status)}</div>
            ${message ? `<div class="rt-rfintel-map-message">${esc(message)}</div>` : ""}
          </div>

          <div class="rt-rfintel-map-region-list">
            ${
              visibleRegions ||
              `<div class="rt-muted">No projected regions</div>`
            }
          </div>
        </div>
      </div>

      <div class="rt-rfintel-footer rt-rfintel-map-footer">
        ${updated ? `<span>Updated: ${esc(updated)}</span>` : ""}
      </div>
    </div>
  `;
}