const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
}[c]));

export function renderTopbarCore(container, panel, data) {
  // Read-only: no business logic, no control, just display.
  const params = new URLSearchParams(location.search);
  const page = params.get("page") || "home";

  const now = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dateStr = now.toLocaleDateString([], { weekday: "short", month: "short", day: "2-digit" });

  container.innerHTML = `
    <div class="rt-topbar">
      <div class="rt-topbar-left">
        <div class="rt-topbar-brand">RollingThunder</div>
        <div class="rt-topbar-page">${esc(page)}</div>
      </div>

      <div class="rt-topbar-right">
        <div class="rt-topbar-dt">
          <span class="rt-topbar-date">${esc(dateStr)}</span>
          <span class="rt-topbar-time">${esc(timeStr)}</span>
        </div>
      </div>
    </div>
  `;
}
