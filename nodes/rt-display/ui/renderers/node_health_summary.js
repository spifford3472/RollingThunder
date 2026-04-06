// node_health_summary.js
//
// v4 (controller-owned browse visual sync):
// - Adds browse cursor + windowed rendering
// - Supports local browse event fallback
// - Visual cursor now follows controller-projected browse state
// - Modal behavior unchanged from prior version

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

const WINDOW = 8;

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

function buildNonControllerModal(nodeId) {
  return {
    kind: "confirm",
    title: "",
    bodyHtml: `
      <div class="rt-modal-warning-title rt-modal-warning-red"><strong>WARNING</strong></div>
      <div class="rt-modal-bodyline">Selecting OK will reboot this node</div>
    `,
    body: `WARNING\nSelecting OK will reboot this node`,
    confirmLabel: "OK",
    cancelLabel: "Exit",
    autoCancelMs: 10000,
    autoCancelLabel: "Exit",
    action: {
      intent: "node.reboot",
      params: { nodeId: String(nodeId), confirm: true },
    },
  };
}

function buildControllerStep2(nodeId) {
  return {
    kind: "confirm",
    title: "",
    bodyHtml: `
      <div class="rt-modal-warning-title rt-modal-warning-red rt-blink-warn"><strong>PRESS OK TO REBOOT</strong></div>
    `,
    body: `PRESS OK TO REBOOT`,
    confirmLabel: "OK",
    cancelLabel: "Cancel",
    autoCancelMs: 5000,
    autoCancelLabel: "Cancel",
    action: {
      intent: "node.reboot",
      params: { nodeId: String(nodeId), confirm: true },
    },
  };
}

function openNodeConfirm(slot, nodeId) {
  if (!slot || !nodeId) return;

  const id = String(nodeId).trim();
  const isController = (id === "rt-controller");

  if (!isController) {
    slot.dispatchEvent(new CustomEvent("rt-open-modal", {
      bubbles: true,
      detail: {
        kind: "confirm",
        title: "Confirm",
        body: `Selected node: ${id}`,
        warningHtml: `
          <div class="rt-modal-warning-title rt-modal-warning-red"><strong>WARNING</strong></div>
          <div style="color:#fff; margin-top:6px;">Selecting OK will reboot this node</div>
        `,
        confirmLabel: "OK",
        cancelLabel: "Exit",
        timeoutMs: 10000,
        danger: true,
        action: {
          intent: "node.reboot",
          params: { nodeId: id, confirm: true }
        }
      }
    }));
    return;
  }

  slot.dispatchEvent(new CustomEvent("rt-open-modal", {
    bubbles: true,
    detail: {
      kind: "confirm",
      title: "Confirm",
      body: `Selected node: ${id}`,
      warningHtml: `
        <div class="rt-warn-blink" style="margin-bottom:6px;">WARNING</div>
        <div class="rt-modal-warning-red" style="margin-bottom:6px;">System will go down during reboot</div>
        <div style="color:#fff;">Selecting OK begins the process</div>
      `,
      confirmLabel: "OK",
      cancelLabel: "Exit",
      twoStep: true,
      armLabel: "PRESS OK TO REBOOT",
      timeoutMs: 5000,
      danger: true,
      action: {
        intent: "node.reboot",
        params: { nodeId: id, confirm: true }
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

  if (slot.__rtNhBrowseV5Attached) return;
  slot.__rtNhBrowseV5Attached = true;

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

  slot.addEventListener("rt-browse-delta", onDelta);
  slot.__rtNhBrowseV5Handlers = { onDelta };
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

function applyProjectedBrowseCursorToNodes(data, list, m) {
  const browse = data?.ui_browse || data?.__ui?.browse || null;
  if (!browse || typeof browse !== "object") return;

  if (String(browse.panel || "") !== "node_health_summary") return;

  const idx = Number(browse.selected_index);
  if (!Number.isFinite(idx)) return;

  if (!Array.isArray(list) || list.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
    m.selectedId = null;
    return;
  }

  m.cursor = clamp(idx, 0, Math.max(0, list.length - 1));
  const cur = list[m.cursor];
  m.selectedId = cur ? String(cur?.id || cur?.node_id || "") : null;
  ensureCursorInWindow(m, list.length);
}

function renderControllerOwnedNodeModal(container, data) {
  const modal = data?.__ui?.modal || null;
  const slot = container.closest(".rt-slot");
  if (!slot) return;

  if (!modal || typeof modal !== "object") {
    container.__rtLastNodeModalId = null;
    return;
  }

  if (String(modal.type || "") !== "node_reboot_confirm") return;

  const modalId = String(modal.id || "");
  if (!modalId) return;

  if (container.__rtLastNodeModalId === modalId) return;

  const hadPriorModal = !!container.__rtLastNodeModalId;
  container.__rtLastNodeModalId = modalId;

  const warning = String(modal.warning || "");
  const message = String(modal.message || "");
  const submessage = String(modal.submessage || "");
  const confirmLabel = String(modal.confirm_label || "OK");
  const cancelLabel = String(modal.cancel_label || "Cancel");

  if (hadPriorModal) {
    slot.dispatchEvent(new CustomEvent("rt-close-modal", {
      bubbles: true,
      detail: { reason: "replace", id: modalId }
    }));
  }

  slot.dispatchEvent(new CustomEvent("rt-open-modal", {
    bubbles: true,
    detail: {
      id: modalId,
      kind: "confirm",
      title: String(modal.title || "Confirm"),
      body: [message, submessage].filter(Boolean).join("\n"),
      warningHtml: `
        ${warning ? `<div class="rt-modal-warning-title rt-modal-warning-red"><strong>${esc(warning)}</strong></div>` : ""}
        ${message ? `<div style="color:#fff; margin-top:6px;">${esc(message)}</div>` : ""}
        ${submessage ? `<div style="color:#fff; margin-top:6px;">${esc(submessage)}</div>` : ""}
      `,
      confirmLabel,
      cancelLabel,
      danger: !!modal.destructive,
    }
  }));
}

export function renderNodeHealthSummary(container, panel, data) {
  attachBrowseHandlersOnce(container);

  const fromRuntime = data?.nodes ?? data?.data?.nodes;
  let nodesList = Array.isArray(fromRuntime) ? fromRuntime : null;

  if (!nodesList) {
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

  const list = nodesList.filter(Boolean).slice().sort((a, b) =>
    String(a.id || a.node_id || "").localeCompare(String(b.id || b.node_id || ""))
  );

  const m = getModel(container);

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

  // Key Phase B fix:
  // force local visual cursor to follow controller-owned browse state
  applyProjectedBrowseCursorToNodes(data, list, m);

  if (list.length <= 0) {
    m.cursor = 0;
    m.offset = 0;
  } else {
    ensureCursorInWindow(m, list.length);
    const cur = list[m.cursor];
    m.selectedId = cur ? String(cur?.id || cur?.node_id || "") : null;
  }

  renderTableWindow(container, list, m);
  renderControllerOwnedNodeModal(container, data);
}