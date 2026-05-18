// hf_map_summary.js
// PURE RENDERER — displays controller/projector-owned HF map/location data.
// No browser geocoding.
// No QRZ calls.
// No map API calls.
// No flag derivation.
// No Redis access.
// No intent execution.

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

function text(value, fallback = "") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

function safeImageUrl(value) {
  const s = String(value ?? "").trim();

  // Renderer-only safety: render only URLs already projected by controller/service.
  // Allow local UI paths, absolute http(s), and data images if a future service uses them.
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

function renderFallback(symbol, label, extraClass = "") {
  return `
    <div class="rt-hf-map-fallback ${extraClass}">
      <div class="rt-hf-map-fallback-symbol">${symbol}</div>
      <div class="rt-hf-map-fallback-label">${label}</div>
    </div>
  `;
}

function renderFlag(mapModel, country) {
  const flag = unwrapObject(mapModel?.flag);
  const status = text(flag.status).toLowerCase();
  const url = safeImageUrl(flag.url);
  const label = text(flag.label, country || "FLAG UNAVAILABLE");

  if (status === "ok" && url) {
    return `
      <div class="rt-hf-map-flag-wrap">
        <img class="rt-hf-map-flag" src="${url}" alt="${label}">
      </div>
    `;
  }

  return renderFallback("⚑", "FLAG UNAVAILABLE", "rt-hf-map-flag-missing");
}

function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, n));
}

function renderMap(mapModel) {
  const map = unwrapObject(mapModel?.map);
  const status = text(map.status).toLowerCase();
  const url = safeImageUrl(map.url);
  const label = text(map.label, "MAP UNAVAILABLE");

  if ((status === "ok" || status === "local_world") && url) {
    const altParts = [
      text(mapModel?.callsign),
      text(mapModel?.country),
      text(map.provider)
    ].filter(Boolean);

    const alt = altParts.length ? altParts.join(" • ") : "HF station map";

    const x = pct(map.pin_x_pct);
    const y = pct(map.pin_y_pct);

    const pinHtml = x !== null && y !== null
      ? `
        <div class="rt-hf-map-pin" style="left:${x}%; top:${y}%;">
          <div class="rt-hf-map-pin-dot"></div>
          <div class="rt-hf-map-pin-ring"></div>
        </div>
      `
      : "";

    return `
      <div class="rt-hf-map-image-wrap rt-hf-map-world-wrap">
        <img class="rt-hf-map-image rt-hf-map-world-image" src="${url}" alt="${alt}">
        ${pinHtml}
      </div>
    `;
  }

  return renderFallback("⌕", label || "MAP UNAVAILABLE", "rt-hf-map-map-missing");
}

export function renderHfMapSummary(container, panel, data) {
  const spot = unwrapObject(data?.spot);
  const mapModel = unwrapObject(data?.map);

  const spotCallsign = text(spot?.callsign || spot?.call);
  const mapCallsign = text(mapModel?.callsign);
  const callsign = mapCallsign || spotCallsign;

  if (!callsign) {
    container.innerHTML = `<div class="rt-muted">No selected station</div>`;
    container.__rtHfMapLastKey = "";
    return;
  }

  const country = text(
    mapModel?.country ||
      mapModel?.country_name ||
      unwrapObject(mapModel?.flag)?.label,
    "Unknown country"
  );

  const status = text(mapModel?.status, "pending");
  const message = text(mapModel?.message);

  const renderKey = JSON.stringify({
    callsign,
    country,
    status,
    message,
    flag: mapModel?.flag || {},
    map: mapModel?.map || {},
    updated_at_ms: mapModel?.updated_at_ms || ""
  });

  if (container.__rtHfMapLastKey === renderKey) return;
  container.__rtHfMapLastKey = renderKey;

  const flagHtml = renderFlag(mapModel, country);
  const mapHtml = renderMap(mapModel);

  container.innerHTML = `
    <div class="rt-hf-map-panel">
      <div class="rt-hf-map-left">
        <div class="rt-hf-map-call">${callsign}</div>
        ${flagHtml}
        <div class="rt-hf-map-country">${country}</div>
      </div>

      <div class="rt-hf-map-right">
        ${mapHtml}
      </div>
    </div>
  `;
}