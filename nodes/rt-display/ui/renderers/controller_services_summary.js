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
  if (!s) return pillHtml("warn", "N/A");
  return pillHtml("warn", s.slice(0, 5).toUpperCase());
}

function ageSecFrom(obj) {
  const ms = Number(obj?.last_update_ms ?? NaN);
  if (!Number.isFinite(ms) || ms <= 0) return null;
  return Math.max(0, Math.floor((Date.now() - ms) / 1000));
}

function fmtAge(ageSec) {
  if (ageSec == null) return "";
  if (ageSec < 60) return String(ageSec);
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}`;
}

export function renderControllerServicesSummary(slot, data) {
  // Preferred: scan binding
  let services = Array.isArray(data?.controller_services) ? data.controller_services : null;

  // Fallback: older static bindings (mqtt_bus, redis_state, etc.)
  if (!services) {
    services = Object.entries(data || {})
      .filter(([k, v]) => !k.startsWith("__") && v && typeof v === "object" && "state" in v)
      .map(([id, obj]) => ({ id, ...obj }));
  }

  services = services || [];

  const rows = services.map((svc) => {
    const id = String(svc?.id || svc?.key || "service");
    const state = svc?.state;
    const pill = stateToPill(state);
    const age = fmtAge(ageSecFrom(svc));
    const err = String(svc?.publisher_error || "").trim();

    const stale = (ageSecFrom(svc) != null && ageSecFrom(svc) > 12);
    const rowCls = stale ? "rt-row stale" : "rt-row";

    return `
      <tr class="${rowCls}">
        <td class="rt-cell-name">${id}</td>
        <td class="rt-cell-status">${pill}</td>
        <td class="rt-cell-age">${age}</td>
        <td class="rt-cell-err">${err ? err : ""}</td>
      </tr>
    `;
  }).join("");

  slot.innerHTML = `
    <div class="rt-table-wrap">
      <table class="rt-table">
        <thead>
          <tr>
            <th>Service</th>
            <th>Status</th>
            <th>Age</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows || `<tr><td colspan="4">No services</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}
