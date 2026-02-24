// controller_services_summary.js
//
// Drop-in replacement (v2):
// - Keeps prior behavior: renders ONLY what scan finds
// - Keeps prior behavior: hides services whose state is "unknown" or blank
// - Stable sort by id/key
// - Age ticker updates in-place
// - NEW: windowed list (max 11 visible rows)
// - NEW: if > 11 services, show scroll hint and support browse scrolling via:
//        slot.dispatchEvent(new CustomEvent("rt-browse-delta", { detail: { delta: +/-1 } }))
//   (runtime.js dispatches this when in PANEL_BROWSE for the focused panel)
//
// No controller changes required. Pure UI behavior.

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

function safeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function clamp(n, lo, hi) {
  const x = Number(n ?? 0);
  const v = Number.isFinite(x) ? x : 0;
  return Math.max(lo, Math.min(hi, v));
}

function stableId(svc) {
  return String(svc?.id || svc?.key || "").trim();
}

function sortServices(all) {
  return all
    .slice()
    .sort((a, b) => {
      const as = stableId(a);
      const bs = stableId(b);
      return as.localeCompare(bs);
    });
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

function renderWindowedTable(container, servicesSorted, offset, windowSize) {
  const total = servicesSorted.length;
  const maxOffset = Math.max(0, total - windowSize);
  const off = clamp(offset, 0, maxOffset);

  const visible = servicesSorted.slice(off, off + windowSize);
  const hasMore = total > windowSize;

  const rows = visible
    .map((svc) => {
      const id = stableId(svc) || "unknown";
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

  // Scroll affordance: shape-first and calm.
  // Uses text-only so it works even if CSS is minimal.
  const hint = hasMore
    ? `<div class="rt-small rt-muted rt-scroll-hint">
         ↕ ${off + 1}-${Math.min(off + windowSize, total)} of ${total} (focus + scroll)
       </div>`
    : `<div class="rt-small rt-muted rt-scroll-hint">
         ${total} service${total === 1 ? "" : "s"}
       </div>`;

  container.innerHTML = `
    <div class="rt-table-wrap">
      ${hint}
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

  return off;
}

// Ensure we keep per-panel UI-only state without leaking globals.
// Stored on the container element (safe in this kiosk runtime).
function ensureLocalState(container) {
  if (!container.__rtSvcState) {
    container.__rtSvcState = {
      offset: 0,
      windowSize: 11,
      lastSorted: [],
      wired: false,
    };
  }
  return container.__rtSvcState;
}

export function renderControllerServicesSummary(container, panel, data) {
  const st = ensureLocalState(container);

  const all = Array.isArray(data?.controller_services) ? data.controller_services : [];

  // Keep prior behavior: hide unknown/unset states entirely
  const services = all.filter(shouldShowService);

  const sorted = sortServices(services);
  st.lastSorted = sorted;

  // Clamp offset if list shrank
  const maxOffset = Math.max(0, sorted.length - st.windowSize);
  st.offset = clamp(st.offset, 0, maxOffset);

  // Wire browse-delta listener once.
  // We listen on the slot (preferred) so events dispatched to slot are captured
  // even if the renderer replaces container.innerHTML.
  if (!st.wired) {
    const slot = container.closest(".rt-slot");
    if (slot) {
      st.wired = true;
      slot.addEventListener("rt-browse-delta", (ev) => {
        const delta = Number(ev?.detail?.delta || 0);
        if (!delta) return;

        const total = st.lastSorted.length;
        const maxOff = Math.max(0, total - st.windowSize);

        // Interpret delta as "one row per tick"
        st.offset = clamp(st.offset + (delta > 0 ? 1 : -1), 0, maxOff);

        // Re-render immediately using the cached list
        st.offset = renderWindowedTable(container, panel, st.lastSorted, st.offset, st.windowSize);
        // Keep age ticker alive (it is cheap; ensures new rows get age updates)
        startAgeTicker(container);
      });
    }
  }

  // Initial / refresh render
  st.offset = renderWindowedTable(container, panel, sorted, st.offset, st.windowSize);

  startAgeTicker(container);
}