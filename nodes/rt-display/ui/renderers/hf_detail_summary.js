// hf_detail_summary.js
// PURE RENDERER — displays controller/projector-owned selected HF detail only.
// No browser-owned decisions, no Redis writes, no intent execution.

function unwrapObject(value) {
  if (!value) return {};

  if (
    typeof value === "object" &&
    value.value &&
    typeof value.value === "object" &&
    !Array.isArray(value.value)
  ) {
    return value.value;
  }

  if (typeof value === "object" && !Array.isArray(value)) return value;

  return {};
}

export function renderHfDetailSummary(container, panel, data) {
  const qrz = unwrapObject(data?.qrz);
  const spot = unwrapObject(data?.spot);

  const callsign = String(qrz?.callsign || spot?.callsign || spot?.call || "").trim();

  if (!callsign) {
    container.innerHTML = `<div class="rt-muted">No selected callsign</div>`;
    return;
  }

  const name = String(qrz?.name || "").trim();
  const address = String(qrz?.address || "").trim();
  const country = String(qrz?.country || "").trim();
  const grid = String(qrz?.grid || "").trim();

  const freq = String(spot?.freq || "").trim();
  const mode = String(spot?.mode || "").trim();
  const band = String(spot?.band || "").trim();
  const status = String(spot?.status || "").trim();

  if (!container.__rtHfDetailInit) {
    container.innerHTML = `
      <div class="rt-detail">
        <div class="rt-hf-call" style="font-size:1.6em; font-weight:700;"></div>
        <div class="rt-hf-subtitle rt-muted"></div>

        <table style="margin-top:.5rem;">
          <tbody>
            <tr><th>Freq</th><td class="rt-hf-freq"></td></tr>
            <tr><th>Mode</th><td class="rt-hf-mode"></td></tr>
            <tr><th>Band</th><td class="rt-hf-band"></td></tr>
            <tr><th>Status</th><td class="rt-hf-status"></td></tr>
            <tr><th>Grid</th><td class="rt-hf-grid"></td></tr>
            <tr><th>QTH</th><td class="rt-hf-qth"></td></tr>
          </tbody>
        </table>
      </div>
    `;
    container.__rtHfDetailInit = true;
  }

  container.querySelector(".rt-hf-call").textContent = callsign;
  container.querySelector(".rt-hf-subtitle").textContent =
    [name, country].filter(Boolean).join(" • ") || "No QRZ detail";

  container.querySelector(".rt-hf-freq").textContent = freq || "-";
  container.querySelector(".rt-hf-mode").textContent = mode || "-";
  container.querySelector(".rt-hf-band").textContent = band || "-";
  container.querySelector(".rt-hf-status").textContent = status || "-";
  container.querySelector(".rt-hf-grid").textContent = grid || "-";
  container.querySelector(".rt-hf-qth").textContent = address || "-";
}