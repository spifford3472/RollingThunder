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

function unwrapMarkers(value) {
  const obj = unwrapObject(value);

  if (Array.isArray(obj.markers)) return obj.markers;
  if (Array.isArray(obj.points)) return obj.points;
  if (Array.isArray(obj.pins)) return obj.pins;

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

function pct(value, fallback = 50) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(100, n));
}

function intensityScale(value) {
  const n = pct(value, 0);
  return 0.72 + (n / 100) * 0.72;
}

function markerClass(item) {
  const status = String(item?.status || "").trim().toLowerCase();
  if (status === "active") return "rt-rfintel-map-marker-active";
  if (status === "moderate") return "rt-rfintel-map-marker-moderate";
  if (status === "light") return "rt-rfintel-map-marker-light";
  if (status === "quiet") return "rt-rfintel-map-marker-quiet";
  return "";
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
    ["summary", "status", "message", "detail", "activity", "condition"],
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

function renderMarker(item, index) {
  if (!item || typeof item !== "object") return "";

  const x = pct(item.x_pct, 50);
  const y = pct(item.y_pct, 50);
  const intensity = pct(item.intensity, 0);
  const scale = intensityScale(intensity);
  const label = firstText(item, ["label", "band", "region", "name"], `M${index + 1}`);
  const band = Array.isArray(item.bands) && item.bands.length
    ? String(item.bands[0] || "").trim()
    : String(item.band || "").trim();

  const title = [
    label,
    band,
    item.status,
    item.summary,
  ].filter(Boolean).join(" • ");

  return `
    <div
      class="rt-rfintel-map-marker ${markerClass(item)}"
      style="left:${x}%; top:${y}%; --rt-marker-scale:${scale}; --rt-marker-intensity:${intensity};"
      title="${esc(title)}"
    >
      <div class="rt-rfintel-map-marker-ring"></div>
      <div class="rt-rfintel-map-marker-dot"></div>
      <div class="rt-rfintel-map-marker-label">
        <span>${esc(label)}</span>
        ${band ? `<b>${esc(band)}</b>` : ""}
      </div>
    </div>
  `;
}

function renderMarkers(markers) {
  const html = markers
    .filter((item) => item && typeof item === "object")
    .slice(0, 8)
    .map((item, index) => renderMarker(item, index))
    .filter(Boolean)
    .join("");

  return html || "";
}

function renderMapVisual(imageUrl, status, isMock, markers) {
  const markerHtml = renderMarkers(markers);

  if (imageUrl) {
    return `
      <div class="rt-rfintel-map-visual-card">
        <img class="rt-rfintel-map-image" src="${esc(imageUrl)}" alt="Projected RF Intel map">
        <div class="rt-rfintel-map-marker-layer">
          ${markerHtml}
        </div>
      </div>
    `;
  }

  return `
    <div class="rt-rfintel-map-visual-card rt-rfintel-map-placeholder">
      <div class="rt-rfintel-map-grid"></div>
      <div class="rt-rfintel-map-sweep"></div>
      <div class="rt-rfintel-map-crosshair"></div>
      <div class="rt-rfintel-map-marker-layer">
        ${markerHtml}
      </div>
      <div class="rt-rfintel-map-center-label">
        ${markerHtml ? "TACTICAL RF ACTIVITY" : isMock ? "MOCK MAP DATA" : esc(status || "NO LIVE MAP DATA")}
      </div>
    </div>
  `;
}

export function renderRfDxMapSummary(container, panel, data) {
  const map = unwrapObject(data?.map);
  const items = unwrapItems(data?.map);
  const markers = unwrapMarkers(data?.map);

  if (Object.keys(map).length === 0 && items.length === 0 && markers.length === 0) {
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

  const mode = firstText(map, ["mode"], "");
  const basis = firstText(map, ["basis"], "");
  const background = unwrapObject(map.background);
  const backgroundLabel = firstText(background, ["label", "type"], "");

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
    background.asset_url ||
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
          ${mode ? badge(mode) : ""}
          ${isMock ? badge("MOCK", "rt-rfintel-badge-mock") : ""}
        </div>
      </div>

      <div class="rt-rfintel-map-layout">
        <div class="rt-rfintel-map-left">
          ${renderMapVisual(imageUrl, status, isMock, markers)}
        </div>

        <div class="rt-rfintel-map-right">
          <div class="rt-rfintel-map-status-card">
            <div class="rt-rfintel-hero-label">${esc(backgroundLabel || "Status")}</div>
            <div class="rt-rfintel-map-status">${esc(status)}</div>
            ${message ? `<div class="rt-rfintel-map-message">${esc(message)}</div>` : ""}
            ${basis ? `<div class="rt-rfintel-map-basis">${esc(basis)}</div>` : ""}
          </div>

          <div class="rt-rfintel-map-region-list">
            ${
              visibleRegions ||
              `<div class="rt-muted">No projected markers</div>`
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