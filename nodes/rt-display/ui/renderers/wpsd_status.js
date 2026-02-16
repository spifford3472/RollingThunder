// wpsd_status.js
//
// Renders WPSD config snapshot (rt:wpsd:snapshot) in the same “boring truth” style.

function safeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function hzToMhz(hz) {
  const n = Number(hz ?? NaN);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return (n / 1e6).toFixed(6);
}

function pillHtml(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";
  return `<span class="${cls}">${label}</span>`;
}

function statusToPill(status) {
  const s = String(status || "").toLowerCase();
  if (s === "online") return pillHtml("ok", "ONLINE");
  if (s === "stale") return pillHtml("warn", "STALE");
  if (s === "offline") return pillHtml("bad", "OFFLINE");
  if (!s) return pillHtml("warn", "—");
  return pillHtml("warn", s.slice(0, 7).toUpperCase());
}

function groupTgsBySlot(list) {
  const slots = new Map(); // slot -> [tg...]
  for (const x of list || []) {
    const tg = Number(x?.tg ?? NaN);
    const slot = Number(x?.slot ?? NaN);
    if (!Number.isFinite(tg) || !Number.isFinite(slot)) continue;
    if (!slots.has(slot)) slots.set(slot, []);
    slots.get(slot).push(tg);
  }
  for (const [k, arr] of slots) arr.sort((a,b)=>a-b);
  return Array.from(slots.entries()).sort((a,b)=>a[0]-b[0]);
}

export function renderWpsdStatus(container, panel, data) {
  const snap = data?.snapshot || null;

  const status = statusToPill(snap?.status);
  const ver = snap?.wpsd_version ? safeText(snap.wpsd_version) : "—";

  const rx = hzToMhz(snap?.rx_freq_hz);
  const tx = hzToMhz(snap?.tx_freq_hz);

  const nets = Array.isArray(snap?.dmr_networks) ? snap.dmr_networks : [];
  const netRows = nets
    .slice()
    .sort((a,b)=>Number(a?.id??0)-Number(b?.id??0))
    .map((n) => {
      const id = safeText(n?.id ?? "—");
      const name = safeText(n?.name ?? "");
      const addr = safeText(n?.address ?? "");
      const port = (n?.port != null) ? safeText(n.port) : "";
      const rhs = [addr, port ? `:${port}` : ""].join("");
      return `<tr>
        <td class="rt-cell-name">Net ${id}</td>
        <td class="rt-cell-status">${name || "—"}</td>
        <td class="rt-cell-age">${rhs || "—"}</td>
      </tr>`;
    })
    .join("");

  const tgs = Array.isArray(snap?.bm_mapped_talkgroups) ? snap.bm_mapped_talkgroups : [];
  const grouped = groupTgsBySlot(tgs);
  const tgHtml = grouped.length
    ? grouped.map(([slot, arr]) => {
        const line = arr.map((tg)=>`TG ${tg}`).join(", ");
        return `<div class="rt-kv"><span class="k">Slot ${slot}:</span> <span class="v">${safeText(line)}</span></div>`;
      }).join("")
    : `<div class="rt-muted">No BM TG map</div>`;

  container.innerHTML = `
    <div class="rt-card">
      <div class="rt-card-header">
        <div class="rt-card-title">WPSD</div>
        <div class="rt-card-right">${status}</div>
      </div>

      <div class="rt-kv-grid">
        <div class="rt-kv"><span class="k">Version</span><span class="v">${ver}</span></div>
        <div class="rt-kv"><span class="k">RX</span><span class="v">${safeText(rx)} MHz</span></div>
        <div class="rt-kv"><span class="k">TX</span><span class="v">${safeText(tx)} MHz</span></div>
      </div>

      <div class="rt-section">
        <div class="rt-section-title">DMR Networks (enabled)</div>
        <div class="rt-table-wrap">
          <table class="rt-table">
            <thead>
              <tr><th>ID</th><th>Name</th><th>Address</th></tr>
            </thead>
            <tbody>
              ${netRows || `<tr><td colspan="3">No enabled networks</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>

      <div class="rt-section">
        <div class="rt-section-title">BrandMeister Mapped TGs</div>
        ${tgHtml}
      </div>
    </div>
  `;
}
