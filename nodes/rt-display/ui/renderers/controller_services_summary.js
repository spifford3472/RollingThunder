// renderers/controller_services_summary.js

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
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}`;
}

export function renderControllerServicesSummary(slot, data) {
  // Preferred: scan binding provides an array at data.controller_services
  let services = [];
  if (Array.isArray(data?.controller_services)) {
    services = data.controller_services;
  } else {
    // Fallback: old static bindings (mqtt_bus, redis_state, etc)
    services = Object.entries(data || {})
      .filter(([k]) => !k.startsWith("__"))
      .map(([id, obj]) => ({ id, ...obj }));
  }

  // Friendly names
  const labelMap = {
    mqtt_bus: "MQTT",
    redis_state: "Redis",
    gps_ingest: "GPS/Env",
    node_health: "Node Health",
    logging: "Logging",
    meshtastic_c2: "Meshtastic",
    noaa_same: "NOAA SAME",
    wpsd_integration: "WPSD"
  };

  // Normalize shape: scan rows may include { id, state, last_update_ms, ownerNode, key, ... }
  const rowsNorm = services
    .map(s => {
      const id = String(s?.id || s?.key || "").replace(/^rt:services:/, "");
      return {
        id,
        state: s?.state,
        last_update_ms: s?.last_update_ms,
        ownerNode: s?.ownerNode,
        publisher_error: s?.publisher_error,
      };
    })
    .filter(s => s.id);

  // Stable ordering
  rowsNorm.sort((a, b) => a.id.localeCompare(b.id));

  const rows = rowsNorm.map(s => {
    const name = labelMap[s.id] || s.id;
    const pill = stateToPill(s.state);
    const age = ageSecFrom(s);
    const ageTxt = fmtAge(age);
    const err = (s?.publisher_error || "").toString().trim();

    const stale = (age != null && age > 12);
    const rowCls = stale ? "rt-row stale" : "rt-row";

    return `
      <tr class="${rowCls}">
        <td class="rt-cell-name">${name}</td>
        <td class="rt-cell-status">${pill}</td>
        <td class="rt-cell-age">${ageTxt}</td>
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
