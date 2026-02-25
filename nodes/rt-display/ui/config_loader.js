// config_loader.js
//
// RollingThunder UI config loader (rt-display)
//
// IMPORTANT:
// Always fetch config from absolute /config/* paths.
// Do NOT use relative paths like "./config/app.json" because when the UI is served
// at /ui/index.html, that resolves to /ui/config/app.json (404 HTML), causing
// JSON.parse errors.

async function fetchJsonAbs(url, timeoutMs = 2500) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    const ctype = String(resp.headers.get("content-type") || "").toLowerCase();

    // If we got HTML here, someone served the wrong thing (usually a relative-path bug).
    if (ctype.includes("text/html")) {
      const txt = await resp.text();
      throw new Error(`Expected JSON but got HTML for ${url}. First bytes: ${txt.slice(0, 80)}`);
    }

    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

function isNonEmptyStr(x) {
  return typeof x === "string" && x.trim().length > 0;
}

function buildIdMap(list, domainName) {
  if (!Array.isArray(list)) {
    throw new Error(`${domainName} must be an array`);
  }
  const byId = Object.create(null);
  for (let i = 0; i < list.length; i++) {
    const obj = list[i];
    if (!obj || typeof obj !== "object") {
      throw new Error(`${domainName}[${i}] must be an object`);
    }
    const id = String(obj.id || "").trim();
    if (!id) {
      throw new Error(`${domainName}[${i}].id must be a non-empty string`);
    }
    if (byId[id]) {
      throw new Error(`Duplicate ${domainName} id: ${id}`);
    }
    byId[id] = obj;
  }
  return byId;
}

export async function loadConfigBundle(opts = {}) {
  // Absolute paths only.
  const appUrl = opts.appUrl || "/config/app.json";
  const cfg = await fetchJsonAbs(appUrl, opts.timeoutMs || 2500);

  // Minimal shape checks (keep loader strict; validator already exists elsewhere).
  if (!cfg || typeof cfg !== "object") {
    throw new Error("app.json did not parse to an object");
  }
  if (!cfg.schema || typeof cfg.schema !== "object") {
    throw new Error("app.json missing schema block");
  }
  if (!isNonEmptyStr(cfg.schema.id)) {
    throw new Error("app.json schema.id must be a non-empty string");
  }
  if (!Array.isArray(cfg.pages)) {
    throw new Error("app.json pages must be an array (include-resolved)");
  }
  if (!Array.isArray(cfg.panels)) {
    throw new Error("app.json panels must be an array (include-resolved)");
  }

  const pagesById = buildIdMap(cfg.pages, "pages");
  const panelsById = buildIdMap(cfg.panels, "panels");

  return {
    app: cfg,
    pages: cfg.pages,
    panels: cfg.panels,
    pagesById,
    panelsById,
    // Optional convenience for callers:
    schema: cfg.schema,
    globals: cfg.globals || {},
    services: cfg.services || {},
  };
}