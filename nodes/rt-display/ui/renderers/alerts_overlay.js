const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

const WINDOW = 5;

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

export function renderAlertsOverlay(container, panel, data) {
  const payload = data?.alerts ?? {};
  const list =
    Array.isArray(payload) ? payload :
    Array.isArray(payload.items) ? payload.items :
    Array.isArray(payload.alerts) ? payload.alerts :
    Array.isArray(payload.data) ? payload.data :
    [];

  const browse = data?.ui_browse || null;
  const total = list.length;

  let selectedIndex = 0;
  let windowStart = 0;
  let windowSize = WINDOW;

  if (browse && typeof browse === "object" && String(browse.panel || "") === "alerts_overlay") {
    selectedIndex = Number.isFinite(Number(browse.selected_index)) ? Number(browse.selected_index) : 0;
    windowStart = Number.isFinite(Number(browse.window_start)) ? Number(browse.window_start) : 0;
    windowSize = Number.isFinite(Number(browse.window_size)) ? Number(browse.window_size) : WINDOW;
  }

  windowSize = clamp(windowSize, 1, WINDOW);
  selectedIndex = total > 0 ? clamp(selectedIndex, 0, total - 1) : 0;
  windowStart = clamp(windowStart, 0, Math.max(0, total - windowSize));

  const view = list.slice(windowStart, windowStart + windowSize);

  const rows = view.map((a, i) => {
    const absoluteIndex = windowStart + i;
    const selected = absoluteIndex === selectedIndex;

    const kind = a.kind ?? a.type ?? a.category ?? "alert";
    const sev = (a.severity ?? a.level ?? "").toString().toLowerCase();
    const title = a.title ?? a.event ?? a.message ?? a.name ?? "(unnamed)";
    const when = a.time ?? a.ts ?? a.timestamp ?? a.start ?? "";

    const sevClass =
      sev === "bad" || sev === "critical" || sev === "error" ? "rt-alert-bad" :
      sev === "warn" || sev === "warning" ? "rt-alert-warn" :
      sev === "ok" || sev === "info" ? "rt-alert-ok" :
      "rt-alert-warn";

    return `
      <div class="rt-alert ${sevClass} ${selected ? "rt-selected" : ""}">
        <div class="rt-alert-title">${esc(title)}</div>
        <div class="rt-alert-meta">${esc(kind)}${when ? " • " + esc(when) : ""}</div>
      </div>
    `;
  }).join("");

  container.innerHTML = `
    <div class="panel">
      <div class="panel-title">Alerts</div>
      ${total === 0
        ? `<div class="muted">No active alerts.</div>`
        : `<div class="rt-alerts">
             ${rows}
             <div class="rt-footer">
               <span class="rt-muted">Selected ${selectedIndex + 1} of ${total}</span>
             </div>
           </div>`
      }
    </div>
  `;
}