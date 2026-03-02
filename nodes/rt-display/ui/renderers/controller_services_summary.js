// controller_services_summary.js
//
// v4 (view-only):
// - Windowed list: show at most 11 rows at a time
// - Browse mode:
//     ArrowUp/ArrowDown moves a *cursor highlight* through the full list
//     (offset auto-adjusts to keep cursor inside the window)
// - NO restart modal, NO intents (view-only)
// - Adaptive unknown filtering
// - Stable sort by id/key
// - Age ticker updates in-place
// - Footer:
//     non-browse: "Showing X/Y" (+ scroll hint if needed)
//     browse:     "Selected Service #i of N"

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
    container.__rtModel = {
      offset: 0,
      cursor: 0,
      selectedId: null,
      lastKey: "",
      lastServices: [],
    };
  }
  return container.__rtModel;
}

function computeStableKey(list) {
  return list.map(x => String(x?.id || x?.key || "")).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function renderWindow(container, services, m) {
  const total = services.length;

  ensureCursorInWindow(m, total);

  const off = m.offset;
  const view = services.slice(off, off + WINDOW);

  const rows = view.map((svc, i) => {
    const id = String(svc?.id || svc?.key || "unknown");
    const pill = stateToPill(svc?.state);

    const ms = svc?.last_update_ms ?? null;
    const age = ageSecFromMs(ms);
    const ageTxt = fmtAge(age);

    const stale = (age != null && age > 12);

    const absoluteIndex = off + i;
    const isSelected = (absoluteIndex === m.cursor);

    const cls = [
      "rt-row",
      stale ? "stale" : "",
      isSelected ? "rt-selected" : "",
    ].filter(Boolean).join(" ");

    return `
      <tr class="${cls}" data-rt-service-id="${safeText(id)}">
        <td class="rt-cell-name">${safeText(id)}</td>
        <td class="rt-cell-status">${pill}</td>
        <td class="rt-cell-age" data-rt-age-ms="${ms ?? ""}">${ageTxt}</td>
      </tr>
    `;
  }).join("");

  const selected = (total > 0) ? (clamp(m.cursor ?? 0, 0, total - 1) + 1) : 0;

  let footerLeft = total === 0 ? "Showing 0/0" : `Showing ${Math.min(WINDOW, total)}/${total}`;

  const slot = container.closest(".rt-slot");
  if (slot && slot.classList.contains("rt-browse-mode")) {
    footerLeft = total === 0 ? "Selected Service —" : `Selected Service #${selected} of ${total}`;
  }

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
        <span class="rt-muted">${footerLeft}</span>${hint}
      </div>
    </div>
  `;

  startAgeTicker(container);
}

function attachBrowseHandlersOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;

  // bump attachment version so old v3 handlers won't be re-added by this file
  if (slot.__rtCssBrowseV4Attached) return;
  slot.__rtCssBrowseV4Attached = true;

  // If v3 already attached an OK handler, we can't remove it without a reference.
  // But we ALSO won't attach any rt-browse-ok handler here, so new loads are clean.
  // (If you want to actively remove old handlers, we can do that with a small runtime-side cleanup.)

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const services = Array.isArray(m.lastServices) ? m.lastServices : [];
    const total = services.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);

    const cur = services[m.cursor];
    m.selectedId = cur ? String(cur?.id || cur?.key || "") : null;

    ensureCursorInWindow(m, total);
    renderWindow(container, services, m);
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.__rtCssBrowseV4Handlers = { onDelta };
}

export function renderControllerServicesSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);

  const all = Array.isArray(data?.controller_services) ? data.controller_services : [];

  const sorted = all.slice().sort((a, b) => {
    const as = String(a?.id || a?.key || "");
    const bs = String(b?.id || b?.key || "");
    return as.localeCompare(bs);
  });

  const anyReal = sorted.some(svc => isRealState(svc?.state));
  const services = anyReal
    ? sorted.filter(svc => isRealState(svc?.state))
    : sorted;

  const m = getModel(container);

  const key = computeStableKey(services);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;

    if (m.selectedId) {
      const idx = services.findIndex(s => String(s?.id || s?.key || "") === String(m.selectedId));
      m.cursor = idx >= 0 ? idx : 0;
    } else {
      m.cursor = 0;
    }
  } else {
    if (m.selectedId) {
      const idx = services.findIndex(s => String(s?.id || s?.key || "") === String(m.selectedId));
      if (idx >= 0) m.cursor = idx;
    }
  }

  m.lastServices = services;

  if (services.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
  } else {
    ensureCursorInWindow(m, services.length);
    const cur = services[m.cursor];
    m.selectedId = cur ? String(cur?.id || cur?.key || "") : null;
  }

  renderWindow(container, services, m);
}