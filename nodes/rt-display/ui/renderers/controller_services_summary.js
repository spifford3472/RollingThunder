// controller_services_summary.js
// Renderer signature MUST be (container, panel, data) to match renderer_registry.js

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
  return Math.max(0, Math.floor((Date.now() - ms) / 1000));
}

function fmtAge(ageSec) {
  if (ageSec == null) return "";
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  const s = ageSec % 60;
  return `${m}m${String(s).padStart(2, "0")}`;
}

function safeText(s, max = 80) {
  const t = String(s || "").trim();
  if (!t) return "";
  return t.length <= max ? t : (t.slice(0, max) + "…");
}

export function renderControllerServicesSummary(container, panel, data) {
  // In your runtime, `data.<bindingId>` is the resolved VALUE (hash object) or null.
  // Also data includes __rt / __errors; ignore those.
  const entries = Object.entries(data || {})
    .filter(([k]) => !String(k).startsWith("__"))
    .map(([id, obj]) => ({ id, obj }));

  // Prefer binding order (Object preserves insertion order for normal keys)
  const labelMap = {
    mqtt_bus: "MQTT",
    redis_state: "Redis",
    gps_ingest: "GPS/Env",
    node_health: "Node Health",
    logging: "Logging",
    meshtastic_c2: "Meshtastic",
    noaa_same: "NOAA SAME",
  };

  // Stale threshold: if publisher polls at 5s, >12s is "stale-ish"
  // (Purely visual; your lifecycle header already covers hard errors.)
  const STALE_SEC = 12;

  const rowsHtml = entries.map(({ id, obj }) => {
    const name = labelMap[id] || id;

    // If binding is null, show N/A
    const state = obj?.state;
    const pill = stateToPill(state);

    const age = ageSecFrom(obj);
    const ageTxt = fmtAge(age);

    const err = safeText(obj?.publisher_error || "", 120);

    const stale = (age != null && age > STALE_SEC);
    const dimStyle = stale ? ` style="opacity:0.65"` : "";

    return `
      <tr${dimStyle}>
        <td>${name}</td>
        <td>${pill}</td>
        <td class="small">${ageTxt}</td>
        <td class="small">${err}</td>
      </tr>
    `;
  }).join("");

  const title = safeText(panel?.meta?.title || panel?.id || "Controller Services", 40);

  container.innerHTML = `
    <div class="title">${title}</div>
    <table class="drift-table">
      <thead>
        <tr>
          <th>Service</th>
          <th>Status</th>
          <th>Age</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rowsHtml || `<tr><td colspan="4" class="small">No services</td></tr>`}
      </tbody>
    </table>
  `;
}
