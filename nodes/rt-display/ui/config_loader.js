// config_loader.js
//
// RollingThunder UI config loader (rt-display)
//
// Supports BOTH:
// 1) include-resolved app.json:
//      { pages: [{id,...}, ...], panels: [{id,...}, ...] }
// 2) include-unresolved app.json:
//      { pages: ["pages/home.json", ...], panels: ["panels/x.json", ...] }
//   plus common wrappers like:
//      { pages: { include:[...]} } or { pages: { files:[...]} } or { pages: { list:[...]} }
//
// IMPORTANT:
// Always fetch config from absolute /config/* paths (never relative).

async function fetchTextAbs(url, timeoutMs = 2500) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    return await resp.text();
  } finally {
    clearTimeout(t);
  }
}

async function fetchJsonAbs(url, timeoutMs = 2500) {
  const txt = await fetchTextAbs(url, timeoutMs);

  // If we somehow got HTML, surface it loudly (usually wrong path like /ui/config/*).
  const head = txt.trimStart().slice(0, 32).toLowerCase();
  if (head.startsWith("<!doctype") || head.startsWith("<html") || head.startsWith("<head") || head.startsWith("<body")) {
    throw new Error(`Expected JSON but got HTML for ${url}. First bytes: ${txt.trimStart().slice(0, 80)}`);
  }

  try {
    return JSON.parse(txt);
  } catch (e) {
    throw new Error(`JSON.parse failed for ${url}: ${e?.message || e}`);
  }
}

function isObj(x) {
  return x && typeof x === "object" && !Array.isArray(x);
}

function isNonEmptyStr(x) {
  return typeof x === "string" && x.trim().length > 0;
}

function normalizeConfigPath(p) {
  // Accept:
  //   "pages/home.json" -> "/config/pages/home.json"
  //   "/config/pages/home.json" -> "/config/pages/home.json"
  //   "./pages/home.json" -> "/config/pages/home.json"
  //   "config/pages/home.json" -> "/config/pages/home.json"
  let s = String(p || "").trim();
  if (!s) return null;

  // strip leading "./"
  if (s.startsWith("./")) s = s.slice(2);

  // If already absolute, keep it.
  if (s.startsWith("/")) return s;

  // If it already starts with "config/", strip it (we'll add "/config/").
  if (s.startsWith("config/")) s = s.slice("config/".length);

  return `/config/${s}`;
}

function flattenSpec(spec) {
  // Spec can be:
  // - array of objects (already resolved)
  // - array of strings (paths)
  // - object wrapper { include:[...]} / {files:[...]} / {list:[...]} / {paths:[...]}
  // - null/undefined
  if (Array.isArray(spec)) return spec;
  if (isObj(spec)) {
    for (const k of ["include", "files", "list", "paths"]) {
      if (Array.isArray(spec[k])) return spec[k];
    }
  }
  return null;
}

async function loadDomain(domainName, domainSpec, timeoutMs) {
  // Returns array of objects (domain items).
  const flat = flattenSpec(domainSpec);

  // Missing domain
  if (flat == null) {
    throw new Error(`app.json ${domainName} must be an array (objects or file paths), or an object with include/files/list/paths`);
  }

  // Case A: include-resolved (array of objects)
  if (flat.length === 0) return [];
  if (isObj(flat[0])) {
    // Verify all are objects
    for (let i = 0; i < flat.length; i++) {
      if (!isObj(flat[i])) throw new Error(`${domainName}[${i}] must be an object`);
    }
    return flat;
  }

  // Case B: array of strings -> fetch each file
  const paths = flat
    .map(normalizeConfigPath)
    .filter(Boolean);

  // De-dupe but keep order
  const seen = new Set();
  const ordered = [];
  for (const u of paths) {
    if (!seen.has(u)) { seen.add(u); ordered.push(u); }
  }

  const out = [];
  for (const url of ordered) {
    const obj = await fetchJsonAbs(url, timeoutMs);
    if (!isObj(obj)) throw new Error(`${domainName} file ${url} did not parse to an object`);
    out.push(obj);
  }
  return out;
}

function buildIdMap(list, domainName) {
  if (!Array.isArray(list)) throw new Error(`${domainName} must be an array`);
  const byId = Object.create(null);
  for (let i = 0; i < list.length; i++) {
    const obj = list[i];
    if (!isObj(obj)) throw new Error(`${domainName}[${i}] must be an object`);
    const id = String(obj.id || "").trim();
    if (!id) throw new Error(`${domainName}[${i}].id must be a non-empty string`);
    if (byId[id]) throw new Error(`Duplicate ${domainName} id: ${id}`);
    byId[id] = obj;
  }
  return byId;
}

export async function loadConfigBundle(opts = {}) {
  const timeoutMs = opts.timeoutMs || 2500;

  // Always absolute.
  const appUrl = opts.appUrl || "/config/app.json";
  const cfg = await fetchJsonAbs(appUrl, timeoutMs);

  if (!isObj(cfg)) throw new Error("app.json did not parse to an object");
  if (!isObj(cfg.schema)) throw new Error("app.json missing schema block");
  if (!isNonEmptyStr(cfg.schema.id)) throw new Error("app.json schema.id must be a non-empty string");

  // Load pages/panels (either already resolved or as includes)
  const pages = await loadDomain("pages", cfg.pages, timeoutMs);
  const panels = await loadDomain("panels", cfg.panels, timeoutMs);

  const pagesById = buildIdMap(pages, "pages");
  const panelsById = buildIdMap(panels, "panels");

  return {
    app: cfg,
    schema: cfg.schema,
    globals: cfg.globals || {},
    services: cfg.services || {},

    pages,
    panels,
    pagesById,
    panelsById,
  };
}