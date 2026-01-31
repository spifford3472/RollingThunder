export function renderNodeHealthSummary(container, panel, bindings) {
  try {
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
    }[c]));

    // bindings.nodes can be:
    //  - API payload object: { data: { nodes: [...] } }
    //  - direct object: { nodes: [...] }
    //  - direct array: [ ... ]
    const raw = bindings?.nodes;

    let list = [];
    if (Array.isArray(raw)) {
      list = raw.filter(Boolean);
    } else if (raw && typeof raw === "object") {
      if (Array.isArray(raw?.data?.nodes)) list = raw.data.nodes.filter(Boolean);
      else if (Array.isArray(raw?.nodes)) list = raw.nodes.filter(Boolean);
    }

    if (!list.length) {
      const keys = raw && typeof raw === "object" ? Object.keys(raw) : [];
      container.innerHTML = `
        <div class="panel">
          <div class="panel-title">Nodes</div>
          <div class="panel-muted">No nodes reported.</div>
          <div class="panel-muted" style="margin-top:6px; font-size:12px;">
            (debug: nodes binding keys: ${esc(keys.join(", ") || "-")})
          </div>
        </div>
      `;
      return;
    }

    // UI_SEMANTICS: stable ordering by id
    list.sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")));

    const isCanonicalStatus = (s) => s === "online" || s === "stale" || s === "offline";

    const classify = (n) => {
      const id = n?.id || n?.node_id || "-";
      const ip = n?.ip || n?.net?.ip || "-";

      const ageNum = Number(n?.age_sec);
      const age = Number.isFinite(ageNum) ? Math.max(0, Math.floor(ageNum)) : "-";

      const statusRaw = String(n?.status || "").toLowerCase().trim();
      const status = isCanonicalStatus(statusRaw) ? statusRaw : "stale";

      let sev = status === "online" ? "ok" : (status === "stale" ? "warn" : "bad");

      // Escalation rules (never downgrade)
      const pubErr = String(n?.publisher_error || "").trim();
      if (pubErr && sev === "ok") sev = "warn";

      const role = String(n?.role || "").toLowerCase();
      const renderOk = (n?.ui_render_ok ?? n?.ui?.render_ok);
      if (role === "display" && renderOk === false && sev === "ok") sev = "warn";

      const statusLabel =
        status === "online" ? "Online" :
        status === "stale" ? `Stale (${age === "-" ? "?" : age}s)` :
        `Offline (${age === "-" ? "?" : age}s)`;

      return { id, ip, age, sev, statusLabel };
    };

    const pill = (sev, label) => {
      const cls = sev === "ok" ? "pill ok" : (sev === "warn" ? "pill warn" : "pill bad");
      return `<span class="${cls}">${esc(label)}</span>`;
    };

    const rows = list.map((n) => {
      const m = classify(n);
      return `
        <tr class="sev-${esc(m.sev)}">
          <td><strong>${esc(m.id)}</strong></td>
          <td>${pill(m.sev, m.statusLabel)}</td>
          <td>${esc(m.ip)}</td>
          <td>${esc(m.age)}</td>
        </tr>
      `;
    }).join("");

    container.innerHTML = `
      <div class="panel">
        <div class="panel-title">Nodes</div>
        <table>
          <thead>
            <tr><th>Node</th><th>Status</th><th>IP</th><th>Age (sec)</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  } catch (e) {
    // Last-resort: never crash runtime
    container.innerHTML = `
      <div class="panel panel-error">
        <div class="panel-title">Nodes</div>
        <div class="panel-muted">Renderer error: ${String(e?.message || e)}</div>
      </div>
    `;
  }
}
