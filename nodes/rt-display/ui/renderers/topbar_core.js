const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

export function renderTopbarCore(container, panel, data) {
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  container.innerHTML = `
    <div class="rt-topbar">
      <div class="rt-topbar-left">
        <div class="rt-topbar-brand">RollingThunder</div>
        <div class="rt-topbar-page">${esc(page)}</div>
      </div>
      <div class="rt-topbar-right" style="font-size:12px; opacity:0.9;">
        data=${esc(JSON.stringify(data))}
      </div>
    </div>
  `;
}
