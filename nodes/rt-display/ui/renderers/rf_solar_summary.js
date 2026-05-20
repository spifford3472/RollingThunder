// rf_solar_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel solar model only.
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

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isEmptyObject(obj) {
  return !obj || Object.keys(obj).length === 0;
}

function renderKeyValueRows(model) {
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

export function renderRfSolarSummary(container, panel, data) {
  const solar = unwrapObject(data?.solar);

  if (isEmptyObject(solar)) {
    container.innerHTML = `<div class="rt-muted">Waiting for projected solar data</div>`;
    return;
  }

  container.innerHTML = `
    <div class="rt-panel-section">
      <div class="rt-panel-title">Solar</div>
      <div class="rt-panel-body">
        ${renderKeyValueRows(solar)}
      </div>
    </div>
  `;
}