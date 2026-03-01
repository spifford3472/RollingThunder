// node_health_summary.js
//
// v2 (browse-capable):
// - Adds browse cursor + windowed rendering (like controller_services_summary)
// - Listens for slot events:
//     * rt-browse-delta  {delta:+1/-1}
//     * rt-browse-ok     (Enter) -> opens confirm modal for selected node
// - Keeps existing node classification + badges
// - Still supports runtime-provided data OR fallback fetch/cache

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

const WINDOW = 8; // tune for your panel height

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

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

function getModel(container) {
  if (!container.__rtNhModel) {
    container.__rtNhModel = {
      cursor: 0,
      offset: 0,
      selectedId: null,
      lastKey: "",
      lastList: [],
    };
  }
  return container.__rtNhModel;
}

function computeStableKey(list) {
  return list.map(x => String(x?.id || x?.node_id || "")).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function openNodeConfirm(slot, nodeId) {
  if (!slot || !nodeId) return;

  slot.dispatchEvent(new CustomEvent("rt-open-modal", {
    bubbles: true,
    detail: {
      kind: "node_restart",
      nodeId: String(nodeId),
      action: {
        intent: "node.reboot",
        params: { nodeId: String(nodeId), confirm: true }
      }
    }
  }));
}

function renderTableWindow(container, list, m) {
  const total = list.length;
  if (total === 0) {
    container.innerHTML = `<div class="muted">No nodes reported.</div>`;
    return;
  }

  ensureCursorInWindow(m, total);
  const off = m.offset;
  const view = list.slice(off, off + WINDOW);

  const rows = view.map((n, i) => {
    const meta = classifyNode(n);
    const badgeHtml = meta.badges.length
      ? `<div class="small" style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
          ${meta.badges.map(b => pill(b.sev, b.label)).join("")}
        </div>`
      : "";

    const sevClass = (meta.sev === "ok" || meta.sev === "warn" || meta.sev === "bad") ? meta.sev : "warn";
    const absoluteIndex = off + i;
    const isSelected = (absoluteIndex === m.cursor);

    const trClass = [
      `sev-${sevClass}`,
      isSelected ? "rt-selected" : "",
    ].filter(Boolean).join(" ");

    return `
      <tr class="${trClass}" data-rt-node-id="${esc(meta.id)}">
        <td>
          <div><strong>${esc(meta.id)}</strong></div>
          <div class="small">${esc(meta.role)}${meta.host ? " — " + esc(meta.host) : ""}</div>
        </td>
        <td>
          ${pill(meta.sev, meta.statusLabel)}
          ${badgeHtml}
        </td>
        <td>${esc(meta.ip || "-")}</td>
        <td>${esc(meta.age === "" ? "-" : meta.age)}</td>
      </tr>
    `;
  }).join("");

  const selectedN = total > 0 ? (clamp(m.cursor, 0, total - 1) + 1) : 0;

  let footerLeft = `Showing ${Math.min(WINDOW, total)}/${total}`;
  const slot = container.closest(".rt-slot");
  if (slot && slot.classList.contains("rt-browse-mode")) {
    footerLeft = `Selected Node #${selectedN} of ${total}`;
  }

  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Node</th><th>Status</th><th>IP</th><th>Age (sec)</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="rt-footer">
      <span class="rt-muted">${footerLeft}</span>${hint}
    </div>
  `;
}

function attachBrowseHandlersOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;

  if (slot.__rtNhBrowseV2Attached) return;
  slot.__rtNhBrowseV2Attached = true;

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);
    const cur = list[m.cursor];
    m.selectedId = cur ? String(cur?.id || cur?.node_id || "") : null;

    ensureCursorInWindow(m, total);
    renderTableWindow(container, list, m);
  };

  const onOk = () => {
    const m = getModel(container);
    const list = Array.isArray(m.lastList) ? m.lastList : [];
    const total = list.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = list[m.cursor];
    const nodeId = String(cur?.id || cur?.node_id || "").trim();
    if (!nodeId) return;

    openNodeConfirm(slot, nodeId);
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);

  slot.__rtNhBrowseV2Handlers = { onDelta, onOk };
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
  attachBrowseHandlersOnce(container);

  // Prefer runtime-provided data
  const fromRuntime = data?.nodes ?? data?.data?.nodes;
  let nodesList = Array.isArray(fromRuntime) ? fromRuntime : null;

  if (!nodesList) {
    // Otherwise render cache or placeholder, and kick off a fetch.
    if (Array.isArray(_cache.nodes)) nodesList = _cache.nodes;
  }

  if (!nodesList) {
    if (_cache.err) container.innerHTML = `<div class="muted">Nodes unavailable: ${esc(_cache.err)}</div>`;
    else container.innerHTML = `<div class="muted">Loading nodes…</div>`;

    const url = (panel?.meta?.nodesUrl) || "/api/v1/ui/nodes";
    if (Date.now() - (_cache.ts || 0) > 2000) {
      fetchNodesOnce(url).then(() => {
        if (Array.isArray(_cache.nodes)) renderNodeHealthSummary(container, panel, { nodes: _cache.nodes });
      });
    }
    return;
  }

  // Deterministic sort
  const list = nodesList.filter(Boolean).slice().sort((a, b) =>
    String(a.id || a.node_id || "").localeCompare(String(b.id || b.node_id || ""))
  );

  const m = getModel(container);

  // Reset/retain selection based on stable key
  const key = computeStableKey(list);
  if (m.lastKey !== key) {
    m.lastKey = key;
    m.offset = 0;

    if (m.selectedId) {
      const idx = list.findIndex(n => String(n?.id || n?.node_id || "") === String(m.selectedId));
      m.cursor = idx >= 0 ? idx : 0;
    } else {
      m.cursor = 0;
    }
  } else {
    if (m.selectedId) {
      const idx = list.findIndex(n => String(n?.id || n?.node_id || "") === String(m.selectedId));
      if (idx >= 0) m.cursor = idx;
    }
  }

  m.lastList = list;

  if (list.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
  } else {
    ensureCursorInWindow(m, list.length);
    const cur = list[m.cursor];
    m.selectedId = cur ? String(cur?.id || cur?.node_id || "") : null;
  }

  renderTableWindow(container, list, m);
}