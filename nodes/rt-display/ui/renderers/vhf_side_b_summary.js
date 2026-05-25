// vhf_side_b_summary.js
// PURE RENDERER — displays controller/projector-owned Side B model only.
// No Side B tuning, no Side B programming, no radio API calls,
// no Redis access, no intent execution.

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

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

function text(value, fallback = "-") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

function hasModel(obj) {
  return !!obj && typeof obj === "object" && Object.keys(obj).length > 0;
}

function radioLine(radio) {
  if (!hasModel(radio)) return "VHF radio status unknown";

  const status = text(radio?.status || radio?.state || radio?.availability, "");
  const reason = text(radio?.reason || radio?.message, "");

  if (status && reason) return `${status} — ${reason}`;
  return status || reason || "VHF radio model present";
}

function freqLine(sideB) {
  const direct = text(sideB?.frequency || sideB?.freq || sideB?.frequency_mhz, "");
  if (direct) return direct;

  const hz = Number(sideB?.freq_hz || sideB?.frequency_hz || 0);
  if (Number.isFinite(hz) && hz > 0) return (hz / 1000000).toFixed(5);

  const mhz = Number(sideB?.freq_mhz || 0);
  if (Number.isFinite(mhz) && mhz > 0) return mhz.toFixed(5);

  return "—";
}

function activityLine(sideB) {
  const parts = [];

  const squelch = text(sideB?.squelch || sideB?.squelch_status || sideB?.open, "");
  const last = text(sideB?.last_activity_utc || sideB?.updated_utc || sideB?.timestamp_utc, "");
  const message = text(sideB?.reason || sideB?.message, "");

  if (squelch) parts.push(`Squelch ${squelch}`);
  if (last) parts.push(last);
  if (message) parts.push(message);

  return parts.join(" • ") || "No activity details supplied";
}

export function renderVhfSideBSummary(container, panel, data) {
  const radio = unwrapObject(data?.radio);
  const sideB = unwrapObject(data?.side_b);

  const sideBPresent = hasModel(sideB);

  const status = sideBPresent
    ? text(sideB?.status || sideB?.state, "model present")
    : "Side B monitor model not active yet";

  const label = sideBPresent
    ? text(sideB?.label || sideB?.name || sideB?.channel, "Side B")
    : "Side B / 146.520 Monitor";

  const mode = sideBPresent
    ? text(sideB?.mode || sideB?.modulation, "—")
    : "—";

  const freq = sideBPresent ? freqLine(sideB) : "—";
  const activity = sideBPresent ? activityLine(sideB) : "Waiting for controller-provided Side B model.";

  container.innerHTML = `
    <div style="
      height:100%;
      box-sizing:border-box;
      display:grid;
      grid-template-rows:auto 1fr auto;
      gap:.9rem;
      overflow:hidden;
    ">
      <div style="
        display:flex;
        align-items:baseline;
        justify-content:space-between;
        gap:.9rem;
        overflow:hidden;
      ">
        <div style="min-width:0;">
          <div style="
            font-size:2.35rem;
            line-height:1.03;
            font-weight:950;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(label)}</div>
          <div class="rt-muted" style="
            margin-top:.2rem;
            font-size:1.35rem;
            line-height:1.12;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(status)}</div>
        </div>

        <div style="
          text-align:right;
          flex:0 0 auto;
          border:1px solid rgba(255,255,255,.10);
          border-radius:14px;
          padding:.6rem .85rem;
          background:rgba(255,255,255,.035);
        ">
          <div class="rt-muted" style="font-size:1.05rem;">Mode</div>
          <div style="font-size:1.65rem; line-height:1.05; font-weight:900;">${esc(mode)}</div>
        </div>
      </div>

      <div style="
        min-height:0;
        display:grid;
        grid-template-columns:1fr 1fr;
        gap:.85rem;
      ">
        <div style="
          border:1px solid rgba(255,255,255,.10);
          border-radius:18px;
          padding:1rem;
          background:rgba(255,255,255,.03);
          overflow:hidden;
        ">
          <div class="rt-muted" style="font-size:1.25rem;">Frequency</div>
          <div style="
            margin-top:.35rem;
            font-size:3.1rem;
            line-height:1.0;
            font-weight:950;
            letter-spacing:.025em;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(freq)}</div>
        </div>

        <div style="
          border:1px solid rgba(255,255,255,.10);
          border-radius:18px;
          padding:1rem;
          background:rgba(255,255,255,.03);
          overflow:hidden;
        ">
          <div class="rt-muted" style="font-size:1.25rem;">Control Path</div>
          <div style="
            margin-top:.45rem;
            font-size:1.45rem;
            line-height:1.18;
            font-weight:850;
            max-height:4.8em;
            overflow:hidden;
          ">${esc(radioLine(radio))}</div>
        </div>
      </div>

      <div class="rt-footer">
        <span class="rt-muted" style="font-size:1.25rem;">${esc(activity)}</span>
      </div>
    </div>
  `;
}
