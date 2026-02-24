// controller_services_summary.js
//
// v2:
// - Windowed list: show at most 11 rows at a time
// - Scrollable when in browse mode: runtime dispatches "rt-browse-delta" to the slot
// - Adaptive unknown filtering:
//     - If ANY real service states exist, hide unknown/blank (keeps the panel clean)
//     - If NONE exist, show unknown so the panel isn't empty and you can debug upstream
// - Stable sort by id/key
// - Age ticker updates in-place
// - Footer: "Showing X/Y" and "scroll" hint if Y > WINDOW

const WINDOW = 11;

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

function normState(x) {
  return String(x ?? "").toLowerCase().trim();
}

function isRealState(s) {
  const v = normState(s);
  return !!v && v !== "unknown";
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

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function getModel(container) {
  if (!container.__rtModel) {
    container.__rtModel = { offset: 0, lastKey: "" };
  }
  return container.__rtModel;
}

function computeStableKey(list) {
  // cheap stability marker so we can reset offset when the list shape changes
  // (ids only, in sorted order)
  const ids = list.map(x => String(x?.id || x?.key || "")).join("|");
  return ids;
}

function renderWindow(container, services, offset) {
  const total = services.length;
  const off = clamp(offset, 0, Math.max(0, total - WINDOW));
  const view = services.slice(off, off + WINDOW);

  const rows = view.map((svc) => {
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
  }).join("");

  const showing = total === 0 ? "0/0" : `${Math.min(WINDOW, total)}/${total}`;
  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

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
      <div class="rt-footer">
        <span class="rt-muted">Showing ${showing}</span>${hint}
      </div>
    </div>
  `;

  startAgeTicker(container);
  return off;
}

function attachBrowseHandlerOnce(container) {
  if (container.__rtBrowseAttached) return;
  container.__rtBrowseAttached = true;

  const slot = container.closest(".rt-slot");
  if (!slot) return;

  slot.addEventListener("rt-browse-delta", (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    m.offset = (m.offset || 0) + (delta > 0 ? 1 : -1);

    // Re-render with the last computed list snapshot if present
    const services = Array.isArray(m.lastServices) ? m.lastServices : [];
    m.offset = renderWindow(container, services, m.offset);
  });
}

export function renderControllerServicesSummary(container, panel, data) {
  attachBrowseHandlerOnce(container);

  const all = Array.isArray(data?.controller_services) ? data.controller_services : [];

  // Sort deterministically
  const sorted = all.slice().sort((a, b) => {
    const as = String(a?.id || a?.key || "");
    const bs = String(b?.id || b?.key || "");
    return as.localeCompare(bs);
  });

  // Adaptive unknown filtering:
  // If anything has a real state, hide unknown/blank.
  const anyReal = sorted.some(svc => isRealState(svc?.state));
  const services = anyReal
    ? sorted.filter(svc => isRealState(svc?.state))
    : sorted; // show unknown so panel is not empty

  const m = getModel(container);

  // Reset offset if the list identity changed
  const key = computeStableKey(services);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;
  }

  // Save snapshot for browse re-render
  m.lastServices = services;

  m.offset = renderWindow(container, services, m.offset);
}