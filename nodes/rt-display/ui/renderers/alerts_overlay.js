const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

const SEVERITY_RANK = {
  critical: 60,
  error: 55,
  bad: 50,
  warn: 40,
  warning: 40,
  watch: 30,
  info: 20,
  ok: 10,
};

function severityRank(a) {
  const sev = String(a?.severity ?? a?.level ?? "").toLowerCase().trim();
  return SEVERITY_RANK[sev] ?? 25;
}

function alertTimeMs(a) {
  const candidates = [
    a?.created_ms,
    a?.updated_ms,
    a?.last_update_ms,
    a?.timestamp_ms,
    a?.ts_ms,
  ];

  for (const v of candidates) {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) return n;
  }

  const textCandidates = [
    a?.created_utc,
    a?.timestamp_utc,
    a?.time,
    a?.ts,
    a?.timestamp,
    a?.start,
    a?.when,
  ];

  for (const v of textCandidates) {
    const t = Date.parse(String(v || ""));
    if (Number.isFinite(t) && t > 0) return t;
  }

  return 0;
}

function pickTopAlert(list) {
  if (!Array.isArray(list) || list.length <= 0) return null;

  return [...list].sort((a, b) => {
    const sevDiff = severityRank(b) - severityRank(a);
    if (sevDiff !== 0) return sevDiff;

    return alertTimeMs(b) - alertTimeMs(a);
  })[0] || null;
}

function alertTitle(a) {
  return String(
    a?.title ??
    a?.event ??
    a?.message ??
    a?.name ??
    "Active alert"
  ).trim();
}

function countText(total) {
  if (total === 1) return "1 active alert";
  return `${total} active alerts`;
}

export function renderAlertsOverlay(container, panel, data) {
  const payload = data?.alerts ?? {};
  const list =
    Array.isArray(payload) ? payload :
    Array.isArray(payload.items) ? payload.items :
    Array.isArray(payload.alerts) ? payload.alerts :
    Array.isArray(payload.data) ? payload.data :
    [];

  const total = list.length;
  const top = pickTopAlert(list);

  let body = `<div class="muted">No active alerts.</div>`;

  if (top) {
    const sev = String(top.severity ?? top.level ?? "alert").toLowerCase().trim();
    const kind = String(top.kind ?? top.type ?? top.category ?? "alert").trim();

    const sevClass =
      sev === "bad" || sev === "critical" || sev === "error" ? "rt-alert-bad" :
      sev === "warn" || sev === "warning" || sev === "watch" ? "rt-alert-warn" :
      sev === "ok" || sev === "info" ? "rt-alert-ok" :
      "rt-alert-warn";

    body = `
      <div class="rt-alert ${sevClass}">
        <div class="rt-alert-title">${esc(alertTitle(top))}</div>
        <div class="rt-alert-meta">${esc(kind)}${sev ? " • " + esc(sev) : ""}</div>
      </div>
      <div class="rt-footer">
        <span class="rt-muted" style="font-size:20px;">${esc(countText(total))}</span>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="panel">
      <div class="panel-title">Alerts</div>
      <div class="rt-alerts">
        ${body}
      </div>
    </div>
  `;
}