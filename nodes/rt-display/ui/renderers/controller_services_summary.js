const WINDOW = 18;

function safeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function pillHtml(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";

  return `<span class="${cls}">${safeText(label)}</span>`;
}

function stateToPill(state) {
  const s = String(state || "").trim().toLowerCase();

  if (s === "running" || s === "active") return pillHtml("ok", "RUN");
  if (s === "stopped" || s === "inactive") return pillHtml("warn", "STOP");
  if (s === "failed" || s === "error") return pillHtml("bad", "FAIL");
  if (!s) return "";

  return pillHtml("warn", s.slice(0, 5).toUpperCase());
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function extractRows(data) {
  const model = data?.controller_services;

  if (Array.isArray(model?.items)) return model.items;
  if (Array.isArray(model)) return model;

  return [];
}

export function renderControllerServicesSummary(container, panel, data) {
  const rows = extractRows(data);
  const browse = data?.ui_browse || null;
  const total = rows.length;

  let selectedIndex = 0;
  let windowStart = 0;
  let windowSize = WINDOW;

  if (
    browse &&
    typeof browse === "object" &&
    String(browse.panel || "") === "controller_services_summary"
  ) {
    const maybeSelected = Number(browse.selected_index);
    const maybeWindowStart = Number(browse.window_start);
    const maybeWindowSize = Number(browse.window_size);

    selectedIndex = Number.isFinite(maybeSelected) ? maybeSelected : 0;
    windowStart = Number.isFinite(maybeWindowStart) ? maybeWindowStart : 0;
    windowSize = Number.isFinite(maybeWindowSize) ? maybeWindowSize : WINDOW;
  }

  windowSize = clamp(windowSize, 1, WINDOW);
  selectedIndex = total > 0 ? clamp(selectedIndex, 0, total - 1) : 0;
  windowStart = clamp(windowStart, 0, Math.max(0, total - windowSize));

  const view = rows.slice(windowStart, windowStart + windowSize);

  const body = view.map((row, i) => {
    const absoluteIndex = windowStart + i;
    const selected = absoluteIndex === selectedIndex;
    const type = String(row?.type || "service");

    if (type === "node_header") {
      return `
        <tr class="rt-row rt-node-header ${selected ? "rt-selected" : ""}">
          <td class="rt-cell-node" colspan="3">${safeText(row?.node || "")}</td>
        </tr>
      `;
    }

    return `
      <tr class="rt-row ${selected ? "rt-selected" : ""}">
        <td class="rt-cell-node">${safeText(row?.node || "")}</td>
        <td class="rt-cell-name">${safeText(row?.service || row?.id || row?.name || "")}</td>
        <td class="rt-cell-status">${stateToPill(row?.state)}</td>
      </tr>
    `;
  }).join("");

  const selectedText =
    total > 0 ? `Selected ${selectedIndex + 1} of ${total}` : "No services";

  container.innerHTML = `
    <div class="rt-table-wrap">
      <table class="rt-table">
        <thead>
          <tr>
            <th>Node</th>
            <th>Service</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody>
          ${body || `<tr><td colspan="3">No services</td></tr>`}
        </tbody>
      </table>
      <div class="rt-footer">
        <span class="rt-muted">${safeText(selectedText)}</span>
      </div>
    </div>
  `;
}