const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

export function renderPanelError(container, { title, detail } = {}) {
  container.innerHTML = `
    <div style="border:1px solid rgba(248,81,73,0.55); border-radius:12px; padding:12px;">
      <div style="font-weight:800; margin-bottom:6px;">${esc(title || "Panel Error")}</div>
      <div style="font-family:ui-monospace, Menlo, monospace; white-space:pre-wrap;">${esc(detail || "")}</div>
    </div>
  `;
}
