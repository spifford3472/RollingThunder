export function renderDeployDriftSummary(container, panel, data) {
  // data.deploy is expected to be the parsed JSON from /api/v1/ui/deploy
  const payload = data?.deploy || {};
  const nodes = payload?.data?.nodes || [];

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[c]));

  const rows = nodes.map(n => {
    const drift = n.drift || {};
    const state = drift.state || "stale";
    const reasons = (drift.reasons || []).join(", ");
    const commit = n.deploy?.deployed_commit ?? "-";
    const age = n.deploy?.report_age_sec ?? "-";

    return `
      <tr class="drift-${esc(state)}">
        <td class="mono">${esc(n.id)}</td>
        <td>${esc(n.role)}</td>
        <td class="mono">${esc(commit)}</td>
        <td>${esc(age)}s</td>
        <td class="state">${esc(state)}</td>
        <td class="reasons">${esc(reasons)}</td>
      </tr>
    `;
  }).join("");

  container.innerHTML = `
    <div class="panel">
      <div class="panel-title">Deploy / Drift</div>
      <table class="drift-table">
        <thead>
          <tr><th>Node</th><th>Role</th><th>Commit</th><th>Age</th><th>Drift</th><th>Reasons</th></tr>
        </thead>
        <tbody>
          ${rows || `<tr><td colspan="6">No deploy reports yet</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}
