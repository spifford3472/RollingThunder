const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

// Read-only alerts overlay.
// It only renders what it is given. No inference, no state transitions.
export function renderAlertsOverlay(container, panel, data) {
  // Try a few common binding IDs / shapes.
  // The runtime binding store typically keys by binding.id, e.g. data.alerts or data.noaa, etc.
  const payload =
    data?.alerts ??
    data?.alerts_overlay ??
    data?.alert ??
    data?.noaa ??
    data?.data ??
    {};

  // Normalize to a list without inventing meaning.
  // Accept: {items:[...]}, {alerts:[...]}, or [...] directly.
  const list =
    Array.isArray(payload) ? payload :
    Array.isArray(payload.items) ? payload.items :
    Array.isArray(payload.alerts) ? payload.alerts :
    Array.isArray(payload.data) ? payload.data :
    [];

  const count = list.length;

  // Small helper: pull some display-ish fields if present (but don't require any).
  const rows = list.slice(0, 6).map((a) => {
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
      <div class="rt-alert ${sevClass}">
        <div class="rt-alert-title">${esc(title)}</div>
        <div class="rt-alert-meta">${esc(kind)}${when ? " • " + esc(when) : ""}</div>
      </div>
    `;
  }).join("");

  container.innerHTML = `
    <div class="panel">
      <div class="panel-title">Alerts</div>
      ${count === 0
        ? `<div class="muted">No active alerts.</div>`
        : `<div class="rt-alerts">
             <div class="small muted" style="margin-bottom:8px;">Showing ${count} alert${count === 1 ? "" : "s"}.</div>
             ${rows}
           </div>`
      }
    </div>
  `;
}
