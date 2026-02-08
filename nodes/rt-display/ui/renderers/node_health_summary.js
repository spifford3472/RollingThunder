const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

let _cache = { ts: 0, nodes: null, err: null };
let _inflight = false;

function isCanonicalStatus(s) {
  return s === "online" || s === "stale" || s === "offline";
}

function pill(sev, label) {
  const cls =
    sev === "ok" ? "pill ok" :
    sev === "warn" ? "pill warn" :
    "pill bad";
  return `<span class="${cls}">${esc(label)}</span>`;
}

function classifyNode(n) {
  const id = n.id || n.node_id || "";
  const roleRaw = (n.role || "").toString();
  const role = roleRaw.toLowerCase();
  const host = n.hostname || "";
  const ip = n.ip || (n.net && n.net.ip) || "";

  const ageRaw = n.age_sec ?? n.last_seen_age_sec ?? n.age;
  const ageNum = Number(ageRaw);
  const age = Number.isFinite(ageNum) ? Math.max(0, Math.floor(ageNum)) : "";

  const statusRaw = String(n.status || "").toLowerCase().trim();
  let status = statusRaw;
  if (!isCanonicalStatus(status)) status = "stale";

  let sev =
    status === "online" ? "ok" :
    status === "stale"  ? "warn" :
    "bad";

  const statusLabel =
    status === "online" ? "Online" :
    status === "stale"  ? `Stale (${age === "" ? "?" : age}s)` :
    `Offline (${age === "" ? "?" : age}s)`;

  const badges = [];

  if (statusRaw && statusRaw !== status) {
    badges.push({ sev: "warn", label: `unknown_status:${statusRaw}` });
    if (sev === "ok") sev = "warn";
  }

  const renderOk = (n.ui_render_ok ?? n.ui?.render_ok);
  if (role === "display" && (status === "online" || status === "stale" || !statusRaw)) {
    if (renderOk === true) badges.push({ sev: "ok", label: "UI OK" });
    else if (renderOk === false) {
      badges.push({ sev: "warn", label: "UI degraded" });
      if (sev === "ok") sev = "warn";
    } else {
      badges.push({ sev: "warn", label: "UI unknown" });
    }
  }

  const pubErr = (n.publisher_error ?? "").toString().trim();
  if (pubErr) {
    badges.push({ sev: "warn", label: "publisher_error" });
    if (sev === "ok") sev = "warn";
  }

  return { id, role: roleRaw, host, ip, age, sev, statusLabel, badges };
}

function renderTable(container, nodes) {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    container.innerHTML = `<div class="muted">No nodes reported.</div>`;
    return;
  }

  const list = nodes.filter(Boolean).slice().sort((a,b) =>
    String(a.id || "").localeCompare(String(b.id || ""))
  );

  const rows = list.map((n) => {
    const m = classifyNode(n);
    const badgeHtml = m.badges.length
      ? `<div class="small" style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
          ${m.badges.map(b => pill(b.sev, b.label)).join("")}
        </div>`
      : "";

    const sevClass = (m.sev === "ok" || m.sev === "warn" || m.sev === "bad") ? m.sev : "warn";

    return `
      <tr class="sev-${sevClass}">
        <td>
          <div><strong>${esc(m.id)}</strong></div>
          <div class="small">${esc(m.role)}${m.host ? " — " + esc(m.host) : ""}</div>
        </td>
        <td>
          ${pill(m.sev, m.statusLabel)}
          ${badgeHtml}
        </td>
        <td>${esc(m.ip || "-")}</td>
        <td>${esc(m.age === "" ? "-" : m.age)}</td>
      </tr>
    `;
  }).join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Node</th><th>Status</th><th>IP</th><th>Age (sec)</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function fetchNodesOnce(url) {
  if (_inflight) return;
  _inflight = true;
  try {
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const payload = await resp.json();
    const nodes = payload?.data?.nodes;
    _cache = { ts: Date.now(), nodes: Array.isArray(nodes) ? nodes : [], err: null };
  } catch (e) {
    _cache = { ts: Date.now(), nodes: null, err: String(e?.message || e) };
  } finally {
    _inflight = false;
  }
}

export function renderNodeHealthSummary(container, panel, data) {
  // Prefer runtime-provided data
  const fromRuntime = data?.nodes ?? data?.data?.nodes;
  if (Array.isArray(fromRuntime)) {
    _cache = { ts: Date.now(), nodes: fromRuntime, err: null };
    return renderTable(container, fromRuntime);
  }

  // Otherwise render cache or a placeholder, and kick off a fetch.
  if (Array.isArray(_cache.nodes)) {
    renderTable(container, _cache.nodes);
  } else if (_cache.err) {
    container.innerHTML = `<div class="muted">Nodes unavailable: ${esc(_cache.err)}</div>`;
  } else {
    container.innerHTML = `<div class="muted">Loading nodes…</div>`;
  }

  // Default live endpoint (same-origin)
  const url = (panel?.meta?.nodesUrl) || "/api/v1/ui/nodes";

  // Refresh cache at most once every 2s (renderer can be called often)
  if (Date.now() - (_cache.ts || 0) > 2000) {
    fetchNodesOnce(url).then(() => {
      // Best-effort rerender
      if (Array.isArray(_cache.nodes)) renderTable(container, _cache.nodes);
    });
  }
}
