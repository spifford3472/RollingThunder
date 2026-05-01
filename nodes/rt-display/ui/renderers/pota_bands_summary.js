// pota_bands_summary.js
// Renderer-only POTA SSB bands panel.
// Authoritative selected band comes from data.context.selected_band.

const WINDOW = 8;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function unwrapBinding(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    if (Array.isArray(value.value)) return value.value;
    if (Array.isArray(value.bands)) return value.bands;
    if (Array.isArray(value.items)) return value.items;
    if (Array.isArray(value.rows)) return value.rows;
  }
  return [];
}

function unwrapObject(value) {
  if (!value) return {};
  if (value && typeof value === "object" && value.value && typeof value.value === "object") {
    return value.value;
  }
  if (typeof value === "object") return value;
  return {};
}

function bandName(item) {
  return String(item?.band || item?.id || item?.name || item || "").trim();
}

function bandSortKey(item) {
  const raw = bandName(item).toLowerCase();
  if (!raw) return [9999, ""];

  if (raw.endsWith("m")) {
    const meters = Number.parseInt(raw.slice(0, -1), 10);
    if (Number.isFinite(meters)) return [meters, raw];
  }

  return [9999, raw];
}

function renderBandsWindow(container, list, selectedBandFromContext, browseSelectedId) {
  if (!Array.isArray(list) || list.length === 0) {
    container.innerHTML = `<div class="muted">No POTA SSB bands available.</div>`;
    return;
  }

  const activeBand = String(selectedBandFromContext || "").trim();
  const cursorBand = String(browseSelectedId || "").trim();

  const rows = list.map((item) => {
    const band = bandName(item);
    const count = Number(item?.count || 0);

    const isCursor = cursorBand && band === cursorBand;
    const isActiveBand = activeBand && band === activeBand;

    const trClass = [
      "sev-ok",
      isCursor ? "rt-selected" : "",
      isActiveBand ? "rt-pota-band-selected" : "",
    ].filter(Boolean).join(" ");

    const icon = isActiveBand ? "▶" : "&nbsp;";

    return `
      <tr class="${trClass}">
        <td>
          <span style="display:flex; align-items:center;">
            <span style="width:1.6em; text-align:center;">${icon}</span>
            <strong>${esc(band)}</strong>
          </span>
        </td>
        <td>${esc(String(count))}</td>
      </tr>
    `;
  }).join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>Band</th><th>Spots</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

export function renderPotaBandsSummary(container, panel, data) {
  const bandsRaw = unwrapBinding(data?.bands);
  const context = unwrapObject(data?.context || {});

  const bands = bandsRaw
    .filter(Boolean)
    .slice()
    .sort((a, b) => {
      const [am, as] = bandSortKey(a);
      const [bm, bs] = bandSortKey(b);
      if (am !== bm) return am - bm;
      return as.localeCompare(bs);
    });

  const selectedBandFromContext = String(context?.selected_band || "").trim();

  const browseRaw = data?.ui_browse || data?.__ui?.browse || {};
  const browse = unwrapObject(browseRaw);

  const browseSelectedId = (() => {
    if (!browse?.active) return "";
    if (String(browse?.panel || "") !== "pota_bands_summary") return "";

    // Prefer selected_id if the controller wrote one
    const byId = String(browse?.selected_id || "").trim();
    if (byId) return byId;

    // Fall back to resolving selected_index into a band name from the sorted list
    const idx = Number(browse?.selected_index);
    if (Number.isFinite(idx) && idx >= 0 && idx < bands.length) {
      return bandName(bands[idx]);
    }

    return "";  
  })();

  renderBandsWindow(
    container,
    bands,
    selectedBandFromContext,
    browseSelectedId
  );
}