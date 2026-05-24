// rf_band_recommendations.js
// PURE RENDERER — displays controller/projector-owned RF Intel band model only.
// No browser-owned decisions, no Redis access, no API calls, no polling, no intent execution.
// Presentation-only: formats projected fields; does not rank, infer, score, enrich, or correct data.

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

function firstText(model, keys, fallback = "") {
  if (typeof model === "string") return model.trim();

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

function safeClassToken(value) {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function scoreValue(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return String(Math.max(0, Math.min(100, Math.round(n))));
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

function bandLabel(item, index) {
  if (typeof item === "string") return item.trim();

  return firstText(
    item,
    ["band", "label", "name", "id"],
    `Band ${index + 1}`
  );
}

function bandStatus(item) {
  return firstText(
    item,
    ["condition", "status", "state"],
    ""
  );
}

function bandRecommendation(item) {
  return firstText(
    item,
    ["recommendation", "summary", "message", "detail", "reason"],
    ""
  );
}

function bandConfidence(item) {
  return firstText(
    item,
    ["confidence", "confidence_label"],
    ""
  );
}

function bandMode(item) {
  return firstText(
    item,
    ["mode_suggestion", "mode"],
    ""
  );
}

function bandTrend(item) {
  if (!item || typeof item !== "object") return "";

  if (item.trend && typeof item.trend === "object") {
    return firstText(item.trend, ["direction", "trend", "status"], "");
  }

  return firstText(item, ["trend"], "");
}

function renderBandRow(item, index) {
  const label = bandLabel(item, index);
  const status = typeof item === "object" ? bandStatus(item) : "";
  const recommendation = typeof item === "object" ? bandRecommendation(item) : "";
  const confidence = typeof item === "object" ? bandConfidence(item) : "";
  const mode = typeof item === "object" ? bandMode(item) : "";
  const score = typeof item === "object" ? scoreValue(item.score) : "";
  const trend = typeof item === "object" ? bandTrend(item) : "";

  const rowClasses = [
    "rt-rfintel-band-row",
    index === 0 ? "rt-rfintel-band-row-primary" : "",
    status ? `rt-rfintel-band-status-${safeClassToken(status)}` : "",
    confidence ? `rt-rfintel-band-confidence-${safeClassToken(confidence)}` : "",
    trend ? `rt-rfintel-band-trend-${safeClassToken(trend)}` : "",
  ].filter(Boolean).join(" ");

  const style = score ? ` style="--rt-band-score:${esc(score)};"` : "";

  return `
    <div class="${rowClasses}"${style}>
      <div class="rt-rfintel-band-card-top">
        <div class="rt-rfintel-band-name">${esc(label)}</div>
        ${score ? `<div class="rt-rfintel-band-score">${esc(score)}</div>` : ""}
      </div>

      <div class="rt-rfintel-band-primary">
        ${esc(recommendation || status || "—")}
      </div>

      <div class="rt-rfintel-band-badges">
        ${status ? badge(status, "rt-rfintel-band-status-badge") : ""}
        ${confidence ? badge(confidence) : ""}
        ${mode ? badge(mode) : ""}
        ${trend ? badge(trend, "rt-rfintel-band-trend-badge") : ""}
      </div>
    </div>
  `;
}

export function renderRfBandRecommendations(container, panel, data) {
  const bands = unwrapObject(data?.bands);
  const items = unwrapItems(data?.bands);

  const updated = compactTime(
    bands.updated_utc ||
    bands.updated_at ||
    bands.timestamp_utc ||
    bands.timestamp
  );

  const isMock =
    bands.mock === true ||
    String(bands.source || "").toLowerCase().includes("mock");

  if (items.length === 0 && Object.keys(bands).length === 0) {
    container.innerHTML = `
      <div class="rt-rfintel-panel">
        <div class="rt-rfintel-title">Band Recommendations</div>
        <div class="rt-muted">Waiting for projected band recommendation data</div>
      </div>
    `;
    return;
  }

  if (items.length === 0) {
    container.innerHTML = `
      <div class="rt-rfintel-panel rt-rfintel-bands-panel">
        <div class="rt-rfintel-title-row">
          <div class="rt-rfintel-title">Band Recommendations</div>
          ${isMock ? badge("MOCK", "rt-rfintel-badge-mock") : ""}
        </div>

        <div class="rt-rfintel-bands-empty">
          <div class="rt-rfintel-hero-label">Status</div>
          <div class="rt-rfintel-bands-empty-text">
            ${esc(firstText(bands, ["summary", "message", "status"], "No projected band rows"))}
          </div>
        </div>

        <div class="rt-rfintel-footer rt-rfintel-bands-footer">
          ${updated ? `<span>Updated: ${esc(updated)}</span>` : ""}
        </div>
      </div>
    `;
    return;
  }

  const visible = items.slice(0, 6);
  const visibleRows = visible
    .map((item, index) => renderBandRow(item, index))
    .filter(Boolean)
    .join("");

  const countClass = visible.length <= 4
    ? "rt-rfintel-band-count-4"
    : "rt-rfintel-band-count-6";

  container.innerHTML = `
    <div class="rt-rfintel-panel rt-rfintel-bands-panel">
      <div class="rt-rfintel-title-row">
        <div class="rt-rfintel-title">Band Recommendations</div>
        <div class="rt-rfintel-badge-row">
          ${isMock ? badge("MOCK", "rt-rfintel-badge-mock") : ""}
        </div>
      </div>

      <div class="rt-rfintel-band-list ${countClass}">
        ${visibleRows}
      </div>

      <div class="rt-rfintel-footer rt-rfintel-bands-footer">
        <span>Bands shown: ${esc(String(visible.length))} / ${esc(String(items.length))}</span>
        ${updated ? `<span>Updated: ${esc(updated)}</span>` : ""}
      </div>
    </div>
  `;
}