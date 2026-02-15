// controller_services_summary.js

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

function ageSecFromMs(ms) {
  const n = Number(ms ?? NaN);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.max(0, Math.floor((Date.now() - n) / 1000));
}

function fmtAge(ageSec) {
  if (ageSec == null) return "";
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

export function renderControllerServicesSummary(container, panel, data) {
  // Expect scan binding shape: data.controller_services = [{id,state,last_update_ms,...}, ...]
  const services = Array.isArray(data?.controller_services) ? data.controller_services : [];

  // Optional: stable display order
  const labelMap = {
    mqtt_bus: "MQTT",
    redis_state: "Redis",
    gps_ingest: "GPS/Env",
    node_health: "Node Health",
    logging: "Logging",
    meshtastic_c2: "Meshtastic",
    noaa_same: "NOAA SAME",
  };

  // Sort for readability (keep known ones first, then alpha)
  const knownOrder = Object.keys(labelMap);
  const orderIndex = new Map(knownOrder.map((k, i) => [k, i]));

  const rows = services
    .slice()
    .sort((a, b) => {
      const ai = orderIndex.has(a?.id) ? orderIndex.get(a.id) : 999;
      const bi = orderIndex.has(b?.id) ? orderIndex.get(b.id) : 999;
      if (ai !== bi) return ai - bi;
      return String(a?.id || "").localeCompare(String(b?.id || ""));
    })
    .map((svc) => {
      const id = String(svc?.id || svc?.key || "unknown");
      const name = labelMap[id] || id;
      const pill = stateToPill(svc?.state);
      const age = ageSecFromMs(svc?.last_update_ms);
      const ageTxt = fmtAge(age);

      // Consider stale if > ~2 poll intervals of service_state_publisher (10–12s)
      const stale = (age != null && age > 12);
      const rowCls = stale ? "rt-row stale" : "rt-row";

      return `
        <tr class="${rowCls}">
          <td class="rt-cell-name">${name}</td>
          <td class="rt-cell-status">${pill}</td>
          <td class="rt-cell-age">${ageTxt}</td>
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
}
