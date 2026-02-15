// controller_services_summary.js
//
// Drop-in replacement:
// - Renders ONLY what scan finds
// - Hides services whose state is "unknown" (and also blank/null), so you don’t see
//   logging / meshtastic_c2 / noaa_same / node_health until they’re real.
// - Stable sort by id/key
// - Age ticker updates in-place

function pillHtml(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";
  return `<span class="${cls}">${label}</span>`;
}

function stateToPill(state) {
  const s = String(state || "").toLowerCase();
  if (s === "running" || s === "active") return pillHtml("ok", "RUN");
  if (s === "stopped" || s === "inactive") return pillHtml("warn", "STOP");
  if (s === "failed") return pillHtml("bad", "FAIL");
  if (s === "missing") return pillHtml("bad", "MISS");
  if (s === "unknown") return pillHtml("warn", "UNKN");
  if (!s) return pillHtml("warn", "N/A");
  return pillHtml("warn", s.slice(0, 5).toUpperCase());
}

function shouldShowService(svc) {
  const s = String(svc?.state || "").toLowerCase().trim();

  // Hide anything that’s not actually giving us a meaningful state yet.
  if (!s) return false;
  if (s === "unknown") return false;

  return true;
}

function ageSecFromMs(ms) {
  const n = Number(ms ?? NaN);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.max(0, Math.floor((Date.now() - n) / 1000));
}

function fmtAge(ageSec) {
  if (ageSec == null) return "—";
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

function startAgeTicker(container) {
  if (container.__rtAgeTimer) {
    try { clearInterval(container.__rtAgeTimer); } catch (_) {}
    container.__rtAgeTimer = null;
  }

  container.__rtAgeTimer = setInterval(() => {
    const cells = container.querySelectorAll("[data-rt-age-ms]");
    for (const el of cells) {
      const ms = el.getAttribute("data-rt-age-ms");
      const age = ageSecFromMs(ms);
      el.textContent = fmtAge(age);

      const tr = el.closest("tr");
      if (tr) {
        const stale = (age != null && age > 12);
        tr.classList.toggle("stale", stale);
      }
    }
  }, 1000);
}

function safeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderControllerServicesSummary(container, panel, data) {
  const all = Array.isArray(data?.controller_services) ? data.controller_services : [];

  // Key change: hide unknown/unset states entirely
  const services = all.filter(shouldShowService);

  const rows = services
    .slice()
    .sort((a, b) => {
      const as = String(a?.id || a?.key || "");
      const bs = String(b?.id || b?.key || "");
      return as.localeCompare(bs);
    })
    .map((svc) => {
      const id = String(svc?.id || svc?.key || "unknown");
      const pill = stateToPill(svc?.state);

      const ms = svc?.last_update_ms ?? null;
      const age = ageSecFromMs(ms);
      const ageTxt = fmtAge(age);

      const stale = (age != null && age > 12);
      const rowCls = stale ? "rt-row stale" : "rt-row";

      return `
        <tr class="${rowCls}">
          <td class="rt-cell-name">${safeText(id)}</td>
          <td class="rt-cell-status">${pill}</td>
          <td class="rt-cell-age" data-rt-age-ms="${ms ?? ""}">${ageTxt}</td>
        </tr>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="rt-table-wrap">
      <table class="rt-table">
        <thead>
          <tr>
            <th>Service</th>
            <th>Status</th>
            <th>Age</th>
          </tr>
        </thead>
        <tbody>
          ${rows || `<tr><td colspan="3">No services</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  startAgeTicker(container);
}
