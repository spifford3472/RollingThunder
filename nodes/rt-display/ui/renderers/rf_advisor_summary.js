// rf_advisor_summary.js
// PURE RENDERER — displays controller/projector-owned RF Intel advisor model only.
// No browser-owned advice, no Redis access, no API calls, no polling, no intent execution.

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

function advisorText(model) {
  if (typeof model === "string") return model;

  return String(
    model?.text ||
    model?.message ||
    model?.summary ||
    model?.status ||
    ""
  ).trim();
}

function renderKeyValueRows(model) {
  return Object.entries(model)
    .filter(([key]) => !String(key).startsWith("_"))
    .filter(([key]) => !["text", "message", "summary"].includes(String(key)))
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

export function renderRfAdvisorSummary(container, panel, data) {
  const advisor = data?.advisor;
  const advisorObj = unwrapObject(advisor);
  const text = advisorText(advisorObj);

  if (!text && Object.keys(advisorObj).length === 0) {
    container.innerHTML = `<div class="rt-muted">Waiting for projected advisor data</div>`;
    return;
  }

  container.innerHTML = `
    <div class="rt-panel-section">
      <div class="rt-panel-title">RF Advisor</div>
      <div class="rt-panel-body">
        ${
          text
            ? `<div class="rt-advisor-text">${esc(text)}</div>`
            : `<div class="rt-muted">No projected advisor text</div>`
        }
        ${renderKeyValueRows(advisorObj)}
      </div>
    </div>
  `;
}