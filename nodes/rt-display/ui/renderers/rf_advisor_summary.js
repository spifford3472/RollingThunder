// rf_advisor_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel advisor model only.
// No browser-owned advice, no Redis access, no API calls, no polling, no intent execution.
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

function unwrapItems(value) {
  const obj = unwrapObject(value);

  if (Array.isArray(value)) return value;
  if (Array.isArray(obj.items)) return obj.items;
  if (Array.isArray(obj.rows)) return obj.rows;
  if (Array.isArray(obj.messages)) return obj.messages;
  if (Array.isArray(obj.advice)) return obj.advice;

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

function badge(label, className = "") {
  const s = String(label ?? "").trim();
  if (!s) return "";

  return `
    <span class="rt-rfintel-badge ${className}">
      ${esc(s)}
    </span>
  `;
}

function itemText(item) {
  if (typeof item === "string") return item.trim();

  return firstText(
    item,
    ["text", "message", "summary", "recommendation", "reason", "status"],
    ""
  );
}

function itemLabel(item, index) {
  if (!item || typeof item !== "object") return `#${index + 1}`;

  return firstText(
    item,
    ["label", "title", "band", "name", "priority", "level", "severity"],
    `#${index + 1}`
  );
}

function renderAdvisorItem(item, index) {
  const label = itemLabel(item, index);
  const text = itemText(item);

  if (!text) return "";

  const severity =
    item && typeof item === "object"
      ? firstText(item, ["severity", "level", "status"], "")
      : "";

  const category =
    item && typeof item === "object"
      ? firstText(item, ["category"], "")
      : "";

  const priority =
    item && typeof item === "object"
      ? firstText(item, ["priority"], "")
      : "";

  const id =
    item && typeof item === "object"
      ? firstText(item, ["id"], "")
      : "";

  const rowClasses = [
    "rt-rfintel-advisor-row",
    index === 0 ? "rt-rfintel-advisor-row-primary" : "",
    severity ? `rt-rfintel-advisor-severity-${safeClassToken(severity)}` : "",
    category ? `rt-rfintel-advisor-category-${safeClassToken(category)}` : "",
    id ? `rt-rfintel-advisor-id-${safeClassToken(id)}` : "",
  ].filter(Boolean).join(" ");

  return `
    <div class="${rowClasses}">
      <div class="rt-rfintel-advisor-row-top">
        <div class="rt-rfintel-advisor-row-label">${esc(label)}</div>
        <div class="rt-rfintel-badge-row">
          ${category ? badge(category, "rt-rfintel-advisor-category-badge") : ""}
          ${priority ? badge(`P${priority}`, "rt-rfintel-advisor-priority-badge") : ""}
          ${severity ? badge(severity, "rt-rfintel-advisor-row-badge") : ""}
        </div>
      </div>
      <div class="rt-rfintel-advisor-row-text">${esc(text)}</div>
    </div>
  `;
}

export function renderRfAdvisorSummary(container, panel, data) {
  const advisorRaw = data?.advisor;
  const advisor = unwrapObject(advisorRaw);
  const items = unwrapItems(advisorRaw);

  const primaryText = firstText(
    typeof advisorRaw === "string" ? advisorRaw : advisor,
    ["advisor_text", "message", "text", "summary"],
    ""
  );

  if (!primaryText && Object.keys(advisor).length === 0 && items.length === 0) {
    container.innerHTML = `
      <div class="rt-rfintel-panel">
        <div class="rt-rfintel-title">RF Advisor</div>
        <div class="rt-muted">Waiting for projected advisor data</div>
      </div>
    `;
    return;
  }

  const level = firstText(advisor, ["level", "severity", "status"], "");
  const priority = firstText(advisor, ["priority"], "");
  const mobileMode = firstText(advisor, ["mobile_mode", "mode"], "");
  const status = firstText(advisor, ["status"], "");

  const updated = compactTime(
    advisor.updated_utc ||
    advisor.updated_at ||
    advisor.timestamp_utc ||
    advisor.timestamp
  );

  const isMock =
    advisor.mock === true ||
    String(advisor.source || "").toLowerCase().includes("mock");

  const visibleItems = items
    .map((item, index) => renderAdvisorItem(item, index))
    .filter(Boolean)
    .slice(0, 4)
    .join("");

  const panelClasses = [
    "rt-rfintel-panel",
    "rt-rfintel-advisor-panel",
    level ? `rt-rfintel-advisor-level-${safeClassToken(level)}` : "",
    status ? `rt-rfintel-advisor-status-${safeClassToken(status)}` : "",
    mobileMode ? `rt-rfintel-mobile-mode-${safeClassToken(mobileMode)}` : "",
  ].filter(Boolean).join(" ");

  container.innerHTML = `
    <div class="${panelClasses}">
      <div class="rt-rfintel-title-row">
        <div class="rt-rfintel-title">RF Advisor</div>
        <div class="rt-rfintel-badge-row">
          ${level ? badge(level) : ""}
          ${priority ? badge(`P${priority}`, "rt-rfintel-advisor-priority-badge") : ""}
          ${mobileMode ? badge(mobileMode, "rt-rfintel-mobile-badge") : ""}
          ${isMock ? badge("MOCK", "rt-rfintel-badge-mock") : ""}
        </div>
      </div>

      ${
        mobileMode
          ? `<div class="rt-rfintel-mobile-active-strip">Mobile Advisor Active</div>`
          : ""
      }

      <div class="rt-rfintel-advisor-hero">
        <div class="rt-rfintel-hero-label">Primary Guidance</div>
        <div class="rt-rfintel-advisor-main">
          ${esc(primaryText || "No projected advisor text")}
        </div>
      </div>

      <div class="rt-rfintel-advisor-list">
        ${
          visibleItems ||
          `<div class="rt-muted">No additional advisor items</div>`
        }
      </div>

      <div class="rt-rfintel-footer rt-rfintel-advisor-footer">
        ${updated ? `<span>Updated: ${esc(updated)}</span>` : ""}
      </div>
    </div>
  `;
}