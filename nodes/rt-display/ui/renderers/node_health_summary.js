// node_health_summary.js
//
// v2 (drop-in replacement):
// - Keeps existing table look/semantics (classifyNode + pills + badges)
// - Adds browse mode:
//     * ArrowUp/ArrowDown via rt-browse-delta moves a cursor through full list
//     * Windowed view (WINDOW rows) with offset auto-adjust
//     * Selected row gets class "rt-selected" (and keeps existing sev-* class)
//     * Footer shows "Selected Node #X of Y" in browse mode, else "Showing X/Y"
// - Enter in browse dispatches rt-browse-ok which opens a modal request event:
//     kind: "node_restart", nodeId, action:{ intent:"node.reboot", params:{nodeId, confirm:true} }
//   (runtime owns the modal/confirm/timeout behavior)
// - Preserves your cache + fallback fetch from /api/v1/ui/nodes

const WINDOW = 10;

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

// ----------------------- browse model -----------------------

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function getModel(container) {
  if (!container.__rtNhModel) {
    container.__rtNhModel = {
      offset: 0,
      cursor: 0,
      selectedId: null,
      lastKey: "",
      lastNodes: [],
    };
  }
  return container.__rtNhModel;
}

function stableKey(nodesSorted) {
  return nodesSorted.map((n) => String(n?.id || n?.node_id || "")).join("|");
}

function ensureCursorInWindow(m, total) {
  m.cursor = clamp(m.cursor || 0, 0, Math.max(0, total - 1));

  const maxOff = Math.max(0, total - WINDOW);
  m.offset = clamp(m.offset || 0, 0, maxOff);

  if (m.cursor < m.offset) m.offset = m.cursor;
  if (m.cursor >= m.offset + WINDOW) m.offset = m.cursor - WINDOW + 1;

  m.offset = clamp(m.offset, 0, maxOff);
}

function openNodeRestartModal(slot, nodeId) {
  if (!slot || !nodeId) return;

  slot.dispatchEvent(new CustomEvent("rt-open-modal", {
    bubbles: true,
    detail: {
      kind: "node_restart",
      nodeId: String(nodeId),
      title: (String(nodeId) === "rt-controller") ? "Restart controller?" : "Restart node?",
      action: {
        intent: "node.reboot",
        params: { nodeId: String(nodeId), confirm: true },
      },
    },
  }));
}

function attachBrowseHandlersOnce(container) {
  const slot = container.closest(".rt-slot");
  if (!slot) return;

  if (slot.__rtNhBrowseAttached) return;
  slot.__rtNhBrowseAttached = true;

  const onDelta = (ev) => {
    const delta = Number(ev?.detail?.delta ?? 0);
    if (!Number.isFinite(delta) || delta === 0) return;

    const m = getModel(container);
    const nodes = Array.isArray(m.lastNodes) ? m.lastNodes : [];
    const total = nodes.length;
    if (total <= 0) return;

    m.cursor = clamp((m.cursor ?? 0) + (delta > 0 ? 1 : -1), 0, total - 1);

    const cur = nodes[m.cursor];
    const id = String(cur?.id || cur?.node_id || "").trim();
    m.selectedId = id || null;

    ensureCursorInWindow(m, total);
    renderWindow(container, nodes, m);
  };

  const onOk = () => {
    const m = getModel(container);
    const nodes = Array.isArray(m.lastNodes) ? m.lastNodes : [];
    const total = nodes.length;
    if (total <= 0) return;

    m.cursor = clamp(m.cursor ?? 0, 0, total - 1);
    const cur = nodes[m.cursor];
    const id = String(cur?.id || cur?.node_id || "").trim();
    if (!id) return;

    openNodeRestartModal(slot, id);
  };

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.addEventListener("rt-browse-ok", onOk);
  slot.__rtNhBrowseHandlers = { onDelta, onOk };
}

// ----------------------- rendering -----------------------

function renderWindow(container, nodesSorted, m) {
  const total = Array.isArray(nodesSorted) ? nodesSorted.length : 0;

  if (total <= 0) {
    container.innerHTML = `<div class="muted">No nodes reported.</div>`;
    return;
  }

  ensureCursorInWindow(m, total);

  const off = m.offset;
  const view = nodesSorted.slice(off, off + WINDOW);

  const rows = view.map((n, i) => {
    const absoluteIndex = off + i;
    const isSelected = (absoluteIndex === m.cursor);

    const nodeId = String(n?.id || n?.node_id || "").trim();
    const mNode = classifyNode(n);

    const badgeHtml = mNode.badges.length
      ? `<div class="small" style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
          ${mNode.badges.map(b => pill(b.sev, b.label)).join("")}
        </div>`
      : "";

    const sevClass = (mNode.sev === "ok" || mNode.sev === "warn" || mNode.sev === "bad") ? mNode.sev : "warn";

    const cls = [
      `sev-${sevClass}`,
      isSelected ? "rt-selected" : "",
      (nodeId === "rt-controller") ? "rt-node-controller" : "",
    ].filter(Boolean).join(" ");

    return `
      <tr class="${cls}" data-rt-node-id="${esc(nodeId)}">
        <td>
          <div><strong>${esc(mNode.id)}</strong></div>
          <div class="small">${esc(mNode.role)}${mNode.host ? " — " + esc(mNode.host) : ""}</div>
        </td>
        <td>
          ${pill(mNode.sev, mNode.statusLabel)}
          ${badgeHtml}
        </td>
        <td>${esc(mNode.ip || "-")}</td>
        <td>${esc(mNode.age === "" ? "-" : mNode.age)}</td>
      </tr>
    `;
  }).join("");

  const selectedNum = total > 0 ? (clamp(m.cursor ?? 0, 0, total - 1) + 1) : 0;

  // Default (non-browse): viewport info
  let footerLeft = `Showing ${Math.min(WINDOW, total)}/${total}`;

  // Browse mode: cursor info
  const slot = container.closest(".rt-slot");
  if (slot && slot.classList.contains("rt-browse-mode")) {
    footerLeft = `Selected Node #${selectedNum} of ${total}`;
  }

  const hint = (total > WINDOW) ? `&nbsp;•&nbsp;<span class="rt-hint">scroll</span>` : "";

  container.innerHTML = `
    <div class="rt-table-wrap">
      <table>
        <thead>
          <tr><th>Node</th><th>Status</th><th>IP</th><th>Age (sec)</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="rt-footer">
        <span class="muted">${footerLeft}</span>${hint}
      </div>
    </div>
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

function toSortedNodeList(nodes) {
  const list = Array.isArray(nodes) ? nodes.filter(Boolean).slice() : [];
  list.sort((a, b) => String(a?.id || a?.node_id || "").localeCompare(String(b?.id || b?.node_id || "")));
  return list;
}

export function renderNodeHealthSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);

  // Prefer runtime-provided data
  const fromRuntime = data?.nodes ?? data?.data?.nodes;
  if (Array.isArray(fromRuntime)) {
    const list = toSortedNodeList(fromRuntime);
    _cache = { ts: Date.now(), nodes: list, err: null };

    const m = getModel(container);
    const key = stableKey(list);

    if (m.lastKey !== key) {
      m.lastKey = key;
      m.offset = 0;
      if (m.selectedId) {
        const idx = list.findIndex((n) => String(n?.id || n?.node_id || "") === String(m.selectedId));
        m.cursor = idx >= 0 ? idx : 0;
      } else {
        m.cursor = 0;
      }
    } else if (m.selectedId) {
      const idx = list.findIndex((n) => String(n?.id || n?.node_id || "") === String(m.selectedId));
      if (idx >= 0) m.cursor = idx;
    }

    m.lastNodes = list;

    if (list.length <= 0) {
      m.cursor = 0;
      m.offset = 0;
    } else {
      ensureCursorInWindow(m, list.length);
      const cur = list[m.cursor];
      m.selectedId = cur ? String(cur?.id || cur?.node_id || "").trim() || null : null;
    }

    return renderWindow(container, list, m);
  }

  // Otherwise render cache or a placeholder, and kick off a fetch.
  if (Array.isArray(_cache.nodes)) {
    const list = toSortedNodeList(_cache.nodes);
    const m = getModel(container);

    // keep browse model coherent with cached list
    const key = stableKey(list);
    if (m.lastKey !== key) {
      m.lastKey = key;
      m.offset = 0;
      m.cursor = 0;
      m.selectedId = list[0] ? String(list[0]?.id || list[0]?.node_id || "") : null;
    }
    m.lastNodes = list;

    renderWindow(container, list, m);
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
      if (Array.isArray(_cache.nodes)) {
        const list = toSortedNodeList(_cache.nodes);
        const m = getModel(container);
        m.lastNodes = list;
        renderWindow(container, list, m);
      }
    });
  }
}