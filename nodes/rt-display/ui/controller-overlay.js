(function () {
  const OVERLAY_ID = "rt-controller-overlay";

  function ensureOverlay() {
    let overlay = document.getElementById(OVERLAY_ID);
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.innerHTML = `
      <div class="rt-controller-overlay-card">
        <div class="rt-controller-overlay-spinner"></div>
        <div class="rt-controller-overlay-title">Controller Reconnecting</div>
        <div class="rt-controller-overlay-message">
          RollingThunder is waiting for rt-controller to come back.
        </div>
        <div class="rt-controller-overlay-detail" id="rt-controller-overlay-detail">
          Last good state is still being shown.
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
  }

  function setVisible(visible, detailText) {
    const overlay = ensureOverlay();
    const detail = document.getElementById("rt-controller-overlay-detail");
    if (detail && typeof detailText === "string" && detailText.length > 0) {
      detail.textContent = detailText;
    }
    overlay.classList.toggle("rt-visible", !!visible);
    document.body.classList.toggle("rt-controller-offline", !!visible);
  }

  window.RTControllerOverlay = {
    show(detailText) {
      setVisible(true, detailText || "Last good state is still being shown.");
    },
    hide() {
      setVisible(false);
    },
    setDetail(detailText) {
      const detail = document.getElementById("rt-controller-overlay-detail");
      if (detail && typeof detailText === "string") {
        detail.textContent = detailText;
      }
    },
  };
})();