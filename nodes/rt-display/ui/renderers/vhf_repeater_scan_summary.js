// vhf_repeater_scan_summary.js
// PURE RENDERER — displays controller/projector-owned VHF page/map models only.
// No Redis access, no SQLite reads, no repeater filtering/sorting, no distance calculation,
// no radio control, no intent execution.

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

function boolish(value) {
  if (value === true || value === 1) return true;
  const s = String(value ?? "").trim().toLowerCase();
  return ["1", "true", "yes", "y", "on", "active", "selected", "highlighted"].includes(s);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function firstText(...values) {
  for (const value of values) {
    const s = String(value ?? "").trim();
    if (s) return s;
  }
  return "";
}

function leftPanel(page) {
  const lp = page?.left_panel;
  return lp && typeof lp === "object" && !Array.isArray(lp) ? lp : {};
}

function pageItems(page) {
  const lp = leftPanel(page);
  if (Array.isArray(lp.items)) return lp.items;
  if (Array.isArray(page?.items)) return page.items;
  return [];
}

function badgeLabel(badge) {
  if (typeof badge === "string") return badge;
  if (!badge || typeof badge !== "object") return "";
  return firstText(badge.label, badge.name, badge.text, badge.kind, badge.type);
}

function itemBadges(item) {
  const out = [];

  for (const badge of asArray(item?.badges)) {
    const label = badgeLabel(badge);
    if (label) out.push(label);
  }

  if (boolish(item?.ares) || boolish(item?.ares_flag) || boolish(item?.is_ares)) out.push("ARES");
  if (boolish(item?.skywarn) || boolish(item?.skywarn_flag) || boolish(item?.is_skywarn)) out.push("SkyWarn");

  return Array.from(new Set(out.map((x) => String(x).trim()).filter(Boolean)));
}

function itemLabel(item) {
  return firstText(
    item?.label,
    item?.title,
    item?.callsign,
    item?.call,
    item?.name,
    item?.repeater,
    item?.id
  ) || "Repeater";
}

function itemFrequency(item) {
  return firstText(
    item?.frequency,
    item?.frequency_label,
    item?.freq,
    item?.freq_label,
    item?.rx_frequency,
    item?.rx_frequency_label
  ) || "-";
}

function itemSubline(item) {
  return firstText(
    item?.subline,
    item?.subtitle,
    item?.description,
    item?.name,
    item?.location,
    item?.city_state
  );
}

function itemAccessLine(item) {
  return firstText(
    item?.access,
    item?.access_line,
    item?.meta,
    item?.detail,
    item?.tone_line,
    item?.tone_label,
    item?.distance_bearing_tone,
    item?.range_line
  );
}

function itemRangeLine(item) {
  return firstText(
    item?.distance_bearing,
    item?.distance_bearing_label,
    item?.range,
    item?.range_label,
    item?.distance_label,
    item?.bearing_label
  );
}

function rowClass(item) {
  const selected = boolish(item?.selected) || boolish(item?.is_selected);
  const active = boolish(item?.active) || boolish(item?.is_active);
  const highlighted = boolish(item?.highlighted) || boolish(item?.is_highlighted);

  // Model-selected means the controller/current scan row.
  // Browse-selected is handled separately in renderRows().
  return [
    active || highlighted || selected ? "rt-vhf-active" : "",
  ].filter(Boolean).join(" ");
}

function renderBadges(item) {
  const badges = itemBadges(item);
  if (!badges.length) return "";

  return `
    <div style="display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.35rem;">
      ${badges.map((label) => `
        <span style="
          display:inline-block;
          border:1px solid rgba(255,255,255,.22);
          border-radius:999px;
          padding:.12rem .45rem;
          font-size:.82rem;
          font-weight:850;
          letter-spacing:.02em;
          background:rgba(255,255,255,.07);
        ">${esc(label)}</span>
      `).join("")}
    </div>
  `;
}

function renderRows(items, opts = {}) {
  const browse = opts.browse || null;
  const startIndex = Number.isFinite(Number(opts.startIndex)) ? Number(opts.startIndex) : 0;
  const selectedIndex = Number.isFinite(Number(opts.selectedIndex)) ? Number(opts.selectedIndex) : 0;
  const windowSize = Number.isFinite(Number(opts.windowSize)) ? Number(opts.windowSize) : 7;
  const rowGapRem = 0.45;
  if (!items.length) {
    return `
      <div class="rt-muted" style="font-size:1.45rem;">
        No controller-provided repeater rows available.
      </div>
    `;
  }

  return `
    <div style="
      height:100%;
      min-height:0;
      overflow:auto;
      display:flex;
      flex-direction:column;
      gap:.45rem;
      padding-right:.15rem;
    ">
      ${items.map((item, visibleIdx) => {
        const absoluteIdx = startIndex + visibleIdx;
        const browseSelected = browse && browse.active === true && absoluteIdx === selectedIndex;
        const cls = [rowClass(item), browseSelected ? "rt-selected" : ""].filter(Boolean).join(" ");        
        const label = itemLabel(item);
        const freq = itemFrequency(item);
        const subline = itemSubline(item);
        const access = itemAccessLine(item);
        const range = itemRangeLine(item);

        return `
          <div class="${esc(cls)}" style="
            position:relative;
            box-sizing:border-box;
            flex:0 0 calc((100% - ${(windowSize - 1) * rowGapRem}rem) / ${windowSize});
            min-height:0;
            box-sizing:border-box;
            display:flex;
            flex-direction:column;
            justify-content:center;
            border:1px solid rgba(255,255,255,.11);
            border-radius:16px;
            padding:.55rem .75rem .55rem .95rem;
            ${cls.includes("rt-vhf-active") ? `
              <div aria-hidden="true" style="
                position:absolute;
                right:0;
                top:0;
                bottom:0;
                width:7px;
                background:#ff1744;
                box-shadow:0 0 10px rgba(255,23,68,.95);
              "></div>
            ` : ""}
            box-shadow:${cls.includes("rt-selected") ? "inset 0 0 0 2px rgba(255,255,255,.65)" : "none"};
            overflow:hidden;
            display:flex;
            flex-direction:column;
            justify-content:center;
          ">
            <div style="
              display:grid;
              grid-template-columns:minmax(0,1fr) auto;
              gap:${rowGapRem}rem;
              align-items:start;
            ">
              <div style="min-width:0;">
                <div style="
                  font-size:1.35rem;
                  line-height:1.08;
                  font-weight:950;
                  white-space:nowrap;
                  overflow:hidden;
                  text-overflow:ellipsis;
                ">${esc(label)}</div>
                ${subline ? `
                  <div class="rt-muted" style="
                    margin-top:.18rem;
                    font-size:1.02rem;
                    line-height:1.12;
                    white-space:nowrap;
                    overflow:hidden;
                    text-overflow:ellipsis;
                  ">${esc(subline)}</div>
                ` : ""}
                ${renderBadges(item)}
              </div>

              <div style="text-align:right; flex:0 0 auto;">
                <div style="
                  font-size:1.42rem;
                  line-height:1.0;
                  font-weight:950;
                  letter-spacing:.02em;
                  white-space:nowrap;
                ">${esc(freq)}</div>
                ${range ? `
                  <div class="rt-muted" style="margin-top:.28rem; font-size:.98rem; font-weight:750;">
                    ${esc(range)}
                  </div>
                ` : ""}
              </div>
            </div>

            ${access ? `
              <div style="
                margin-top:.42rem;
                font-size:1.0rem;
                line-height:1.14;
                font-weight:750;
                white-space:nowrap;
                overflow:hidden;
                text-overflow:ellipsis;
              ">${esc(access)}</div>
            ` : ""}
          </div>
        `;
      }).join("")}
    </div>
  `;
}

export function renderVhfRepeaterScanSummary(container, panel, data) {
  const page = unwrapObject(data?.page);
  const map = unwrapObject(data?.map);
  console.log("[vhf_repeater_scan_summary]", {
    dataKeys: data ? Object.keys(data) : [],
    rawPageType: typeof data?.page,
    rawPage: data?.page,
    page,
    leftPanel: page?.left_panel,
    itemCount: Array.isArray(page?.left_panel?.items) ? page.left_panel.items.length : -1,
  });
  const lp = leftPanel(page);


  const allItems = pageItems(page);
  const browse = data?.ui_browse && typeof data.ui_browse === "object" ? data.ui_browse : null;

  const WINDOW_SIZE = 7;
  let selectedIndex = 0;

  if (browse && Number.isFinite(Number(browse.selected_index))) {
    selectedIndex = Number(browse.selected_index);
  }

  selectedIndex = Math.max(0, Math.min(allItems.length - 1, selectedIndex));

  let startIndex = 0;

  if (browse && browse.active === true) {
    // Manual encoder browse window.
    startIndex = selectedIndex - Math.floor(WINDOW_SIZE / 2);
    startIndex = Math.max(0, Math.min(Math.max(0, allItems.length - WINDOW_SIZE), startIndex));
  } else {
    // No auto-scroll during scan. Stay at top unless user browses.
    startIndex = 0;
  }

  const items = allItems.slice(startIndex, startIndex + WINDOW_SIZE);

  const title = firstText(lp.title, lp.headline, page.headline, "Nearby Repeaters");
  const subtitle = firstText(lp.subtitle, lp.reason, page.reason, page.status);
  const count = Number.isFinite(Number(lp.count)) ? Number(lp.count) : allItems.length;
  const mapStatus = firstText(map.status, map.reason, "");
  const highlight = firstText(map.highlight, map.highlight_id, "");

  container.innerHTML = `
    <div style="
      height:100%;
      box-sizing:border-box;
      display:grid;
      grid-template-rows:auto 1fr auto;
      gap:.65rem;
      overflow:hidden;
    ">
      <div style="
        display:flex;
        align-items:flex-start;
        justify-content:space-between;
        gap:.8rem;
        min-height:0;
      ">
        <div style="min-width:0;">
          <div style="
            font-size:2.05rem;
            line-height:1.0;
            font-weight:950;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
          ">${esc(title)}</div>
          ${subtitle ? `
            <div class="rt-muted" style="
              margin-top:.22rem;
              font-size:1.05rem;
              line-height:1.1;
              white-space:nowrap;
              overflow:hidden;
              text-overflow:ellipsis;
            ">${esc(subtitle)}</div>
          ` : ""}
        </div>

        <div style="
          flex:0 0 auto;
          text-align:right;
          border:1px solid rgba(255,255,255,.10);
          border-radius:14px;
          padding:.45rem .65rem;
          background:rgba(255,255,255,.035);
        ">
          <div class="rt-muted" style="font-size:.88rem;">Rows</div>
          <div style="font-size:1.45rem; line-height:1.0; font-weight:950;">${esc(count)}</div>
        </div>
      </div>

      <div style="min-height:0; overflow:hidden;">
        ${renderRows(items, { browse, startIndex, selectedIndex, windowSize: WINDOW_SIZE })}
      </div>

      <div class="rt-footer">
        <span class="rt-muted" style="font-size:1.02rem;">
          ${esc([
            `Controller model: ${page?.status || "unknown"}`,
            mapStatus ? `Map: ${mapStatus}` : "",
            highlight ? `Highlight: ${highlight}` : ""
          ].filter(Boolean).join(" • "))}
        </span>
      </div>
    </div>
  `;
}