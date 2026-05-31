// vhf_side_b_summary.js
// PURE RENDERER — displays controller/projector-owned VHF page status/options model.
// START/STOP emits only the safe vhf.scan.set_enabled UI intent through runtime.
// No Redis access, no SQLite reads, no distance calculation, no radio control,
// no adapter calls, no rigctl, no PTT/transmit controls, no memory operations.

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
  let v = value;

  for (let i = 0; i < 4; i++) {
    if (!v) return {};

    if (typeof v === "string") {
      const s = v.trim();
      if (!s) return {};
      try {
        v = JSON.parse(s);
        continue;
      } catch (_) {
        return {};
      }
    }

    if (
      typeof v === "object" &&
      !Array.isArray(v) &&
      v.value !== undefined
    ) {
      v = v.value;
      continue;
    }

    if (typeof v === "object" && !Array.isArray(v)) return v;

    return {};
  }

  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

function text(value, fallback = "-") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

function firstText(...values) {
  for (const value of values) {
    const s = String(value ?? "").trim();
    if (s) return s;
  }
  return "";
}

function boolish(value) {
  if (value === true || value === 1) return true;
  const s = String(value ?? "").trim().toLowerCase();
  return ["1", "true", "yes", "y", "on", "enabled", "active", "scanning"].includes(s);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function statusPanel(page) {
  const sp = page?.status_panel;
  return sp && typeof sp === "object" && !Array.isArray(sp) ? sp : {};
}

function selectedRepeater(page, status) {
  for (const candidate of [
    status?.current_repeater,
    status?.selected_repeater,
    status?.repeater,
    page?.current_repeater,
    page?.selected_repeater,
  ]) {
    if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) return candidate;
  }
  return {};
}

function optionLabel(option) {
  if (typeof option === "string") return option;
  if (!option || typeof option !== "object") return "";
  return firstText(option.label, option.name, option.id, option.key);
}

function optionDisabled(option) {
  if (!option || typeof option !== "object") return false;
  return boolish(option.disabled) || boolish(option.noop) || String(option.status || "").toLowerCase() === "disabled";
}

function badgeLabel(badge) {
  if (typeof badge === "string") return badge;
  if (!badge || typeof badge !== "object") return "";
  return firstText(badge.label, badge.name, badge.text, badge.kind, badge.type);
}

function repeaterBadges(repeater, status) {
  const out = [];

  for (const source of [status, repeater]) {
    for (const badge of asArray(source?.badges)) {
      const label = badgeLabel(badge);
      if (label) out.push(label);
    }

    if (boolish(source?.ares) || boolish(source?.ares_flag) || boolish(source?.is_ares)) out.push("ARES");
    if (boolish(source?.skywarn) || boolish(source?.skywarn_flag) || boolish(source?.is_skywarn)) out.push("SkyWarn");
  }

  return Array.from(new Set(out.map((x) => String(x).trim()).filter(Boolean)));
}

function renderBadges(repeater, status) {
  const badges = repeaterBadges(repeater, status);
  if (!badges.length) return "";

  return `
    <div style="display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.65rem;">
      ${badges.map((label) => `
        <span style="
          display:inline-block;
          border:1px solid rgba(255,255,255,.24);
          border-radius:999px;
          padding:.18rem .55rem;
          font-size:.92rem;
          font-weight:900;
          letter-spacing:.025em;
          background:rgba(255,255,255,.07);
        ">${esc(label)}</span>
      `).join("")}
    </div>
  `;
}

function renderOptions(options, opts = {}) {
  if (!options.length) {
    return `<div class="rt-muted" style="font-size:1.05rem;">No VHF options supplied.</div>`;
  }

  const selectedIndexRaw = Number.parseInt(opts.selectedIndex ?? "0", 10);
  const selectedIndex = Math.max(
    0,
    Math.min(options.length - 1, Number.isFinite(selectedIndexRaw) ? selectedIndexRaw : 0)
  );

  return `
    <div style="display:flex; gap:.45rem; flex-wrap:wrap;">
      ${options.map((option, idx) => {
        const label = optionLabel(option) || "OPTION";
        const disabled = optionDisabled(option);
        const selected = idx === selectedIndex;

        return `
          <span style="
            display:inline-block;
            border:1px solid ${selected ? "rgba(255,255,255,.68)" : "rgba(255,255,255,.18)"};
            border-radius:999px;
            padding:.25rem .62rem;
            font-size:.95rem;
            font-weight:900;
            opacity:${disabled ? ".42" : "1"};
            background:${selected ? "rgba(255,255,255,.16)" : "rgba(255,255,255,.035)"};
            box-shadow:${selected ? "inset 0 0 0 2px rgba(255,255,255,.18)" : "none"};
          ">${esc(label)}${disabled ? " · OFF" : ""}</span>
        `;
      }).join("")}
    </div>
  `;
}

function inferScanEnabled(page, scan, status) {
  if (typeof scan?.requested === "boolean") return scan.requested;
  if (typeof scan?.enabled === "boolean") return scan.enabled;
  if (typeof scan?.scanning === "boolean") return scan.scanning;

  if (typeof page?.scan_enabled === "boolean") return page.scan_enabled;
  if (typeof status?.scan_enabled === "boolean") return status.scan_enabled;

  const state = String(
    status?.scan_state ||
    status?.actual_scan_state ||
    status?.state ||
    page?.scan_state ||
    scan?.actual_scan_state ||
    scan?.status ||
    ""
  ).trim().toLowerCase();

  return [
    "enabled",
    "scanning",
    "active",
    "software_scanning",
    "priming_radio",
    "confirming_activity",
    "stopped_on_activity"
  ].includes(state);
}

function rightPanelOptions(scanEnabled) {
  if (scanEnabled) {
    return [
      { key: "stop_scan", label: "Stop Scan" },
    ];
  }

  return [
    { key: "start_scan", label: "Start Scan" },
    { key: "repeaters", label: "Repeaters" },
    { key: "air", label: "Air" },
    { key: "news", label: "News" },
  ];
}

function selectedRightOptionIndex(options, browse) {
  if (!options.length) return 0;

  let selectedIndex = 0;

  if (browse && browse.active === true && Number.isFinite(Number(browse.selected_index))) {
    selectedIndex = Number(browse.selected_index);
  }

  return Math.max(0, Math.min(options.length - 1, selectedIndex));
}

export function renderVhfSideBSummary(container, panel, data) {
  const page = unwrapObject(data?.page);
  const scan = unwrapObject(data?.scan);
  const status = statusPanel(page);
  const repeater = selectedRepeater(page, status);
  const scanEnabled = inferScanEnabled(page, scan, status);
  const actionLabel = scanEnabled ? "STOP SCAN" : "START SCAN";

  const browse = data?.ui_browse && typeof data.ui_browse === "object"
    ? data.ui_browse
    : null;

  const rightOptions = rightPanelOptions(scanEnabled);
  const selectedRightIndex = selectedRightOptionIndex(rightOptions, browse);
  const selectedRightKey = String(rightOptions[selectedRightIndex]?.key || "").trim();
  const actionSelected = selectedRightKey === "start_scan" || selectedRightKey === "stop_scan";

  const headline = firstText(
    status.headline,
    page.headline,
    scan.headline,
    scan.status,
    "VHF Scan"
  );

  const reason = firstText(
    status.reason,
    status.message,
    page.reason,
    scan.reason,
    scan.message,
    "Controller-owned VHF status model"
  );

  const scanState = firstText(
    status.scan_state,
    status.actual_scan_state,
    page.scan_state,
    scan.actual_scan_state,
    scan.status,
    scan.state,
    scanEnabled ? "enabled" : "disabled"
  );

  const callsign = firstText(
    repeater.callsign,
    repeater.call,
    status.callsign,
    status.call,
    "—"
  );

  const name = firstText(
    repeater.name,
    repeater.label,
    status.name,
    status.label,
    status.repeater_name,
    "No repeater selected"
  );

  const frequency = firstText(
    repeater.frequency,
    repeater.frequency_label,
    repeater.freq,
    repeater.freq_label,
    repeater.frequency_mhz,
    repeater.freq_mhz,
    status.frequency,
    status.frequency_label,
    status.frequency_mhz,
    status.freq_mhz,
    "—"
  );

  const subline = firstText(
    repeater.subline,
    repeater.description,
    status.subline,
    status.detail,
    status.selected_detail,
    ""
  );

  container.innerHTML = `
    <div style="
      height:100%;
      box-sizing:border-box;
      display:grid;
      grid-template-rows:auto auto 1fr auto;
      gap:.85rem;
      overflow:hidden;
    ">
      <div style="
        display:flex;
        align-items:flex-start;
        justify-content:space-between;
        gap:.85rem;
        overflow:hidden;
      ">
        <div style="min-width:0;">
          <div style="
            font-size:2.15rem;
            line-height:1.0;
            font-weight:950;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(headline)}</div>
          <div class="rt-muted" style="
            margin-top:.25rem;
            font-size:1.08rem;
            line-height:1.12;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(reason)}</div>
        </div>

        <div
          data-rt-vhf-scan-indicator="1"
          style="
            flex:0 0 auto;
            border:1px solid ${actionSelected ? "rgba(255,255,255,.68)" : "rgba(255,255,255,.28)"};
            border-radius:16px;
            padding:.75rem .95rem;
            font-size:1.05rem;
            line-height:1.0;
            font-weight:950;
            color:#fff;
            background:${scanEnabled ? "rgba(255,80,80,.18)" : "rgba(90,210,130,.18)"};
            box-shadow:${actionSelected ? "inset 0 0 0 2px rgba(255,255,255,.18)" : "none"};
          "
          aria-label="${esc(actionLabel)}"
        >${esc(actionLabel)}</div>
      </div>

      <div style="
        border:1px solid rgba(255,255,255,.10);
        border-radius:16px;
        padding:.75rem .85rem;
        background:rgba(255,255,255,.035);
        overflow:hidden;
      ">
        <div class="rt-muted" style="font-size:1.0rem;">Scan State</div>
        <div style="
          margin-top:.2rem;
          font-size:1.45rem;
          line-height:1.08;
          font-weight:950;
          white-space:nowrap;
          overflow:hidden;
          text-overflow:ellipsis;
        ">${esc(scanState)}</div>
      </div>

      <div style="
        min-height:0;
        border:1px solid rgba(255,255,255,.10);
        border-radius:18px;
        padding:1rem;
        background:rgba(255,255,255,.03);
        overflow:hidden;
      ">
        <div class="rt-muted" style="font-size:1.02rem;">Current / Selected Repeater</div>

        <div style="
          margin-top:.45rem;
          display:grid;
          grid-template-columns:minmax(0,1fr) auto;
          gap:.8rem;
          align-items:start;
        ">
          <div style="min-width:0;">
            <div style="
              font-size:2.0rem;
              line-height:1.0;
              font-weight:950;
              white-space:nowrap;
              overflow:hidden;
              text-overflow:ellipsis;
            ">${esc(callsign)}</div>
            <div style="
              margin-top:.32rem;
              font-size:1.25rem;
              line-height:1.12;
              font-weight:850;
              white-space:nowrap;
              overflow:hidden;
              text-overflow:ellipsis;
            ">${esc(name)}</div>
            ${subline ? `
              <div class="rt-muted" style="
                margin-top:.28rem;
                font-size:1.0rem;
                line-height:1.14;
                white-space:nowrap;
                overflow:hidden;
                text-overflow:ellipsis;
              ">${esc(subline)}</div>
            ` : ""}
            ${renderBadges(repeater, status)}
          </div>

          <div style="
            text-align:right;
            flex:0 0 auto;
            font-size:1.75rem;
            line-height:1.0;
            font-weight:950;
            letter-spacing:.02em;
            white-space:nowrap;
          ">${esc(frequency)}</div>
        </div>
      </div>

      <div>
        <div class="rt-muted" style="font-size:.95rem; margin-bottom:.35rem;">Options</div>
        ${renderOptions(rightOptions, { selectedIndex: selectedRightIndex })}
      </div>
    </div>
  `;

}