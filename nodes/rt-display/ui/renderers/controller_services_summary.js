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
  if (!s) return pillHtml("warn", "N/A");
  // activating / deactivating / etc
  return pillHtml("warn", s.slice(0, 5).toUpperCase());
}

function ageSecFrom(serviceObj) {
  const ms = Number(serviceObj?.last_update_ms ?? NaN);
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const age = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  return age;
}

function fmtAge(ageSec) {
  if (ageSec == null) return "";
  if (ageSec < 60) return String(ageSec);
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}`;
}

export function renderControllerServicesSummary(slot, data) {
  // data.<bindingId> is the resolved value (hash object) or null
  const entries = Object.entries(data || {})
    .filter(([k]) => !k.startsWith("__"))
    .map(([id, obj]) => ({ id, obj }));

  // Stable order: same as JSON binding order (Object keeps insertion order)
  // If you want custom labels, do it here:
  const labelMap = {
    mqtt_bus: "MQTT",
    redis_state: "Redis",
    gps_ingest: "GPS/Env",
    node_health: "Node Health",
    logging: "Logging",
    meshtastic_c2: "Meshtastic",
    noaa_same: "NOAA SAME"
  };

  // Derive an overall “worst” state for quick scan (optional: header already shows OK/ERROR)
  let worst = "ok";
  for (const { obj } of entries) {
    const s = String(obj?.state || "").toLowerCase();
    if (s === "failed" || s === "missing") { worst = "bad"; break; }
    if (s === "stopped" || s === "inactive" || !s) worst = (worst === "bad" ? "bad" : "warn");
  }

  const rows = entries.map(({ id, obj }) => {
    const name = labelMap[id] || id;
    const pill = stateToPill(obj?.state);
    const age = ageSecFrom(obj);
    const ageTxt = fmtAge(age);
    const err = (obj?.publisher_error || "").toString().trim();

    // If you want “stale” service updates, you can dim based on age here.
    // Simple: consider stale after 2x poll interval (~10s)
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
