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
// NEW (manifest support):
// - If an included JSON file parses to an object with include/files/list/paths,
//   it is treated as a manifest and expanded (recursively, bounded).
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
  if (
    head.startsWith("<!doctype") ||
    head.startsWith("<html") ||
    head.startsWith("<head") ||
    head.startsWith("<body")
  ) {
    throw new Error(
      `Expected JSON but got HTML for ${url}. First bytes: ${txt.trimStart().slice(0, 80)}`
    );
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

  if (s.startsWith("./")) s = s.slice(2);
  if (s.startsWith("/")) return s;
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

function looksLikeManifestObject(obj) {
  if (!isObj(obj)) return false;
  // If it has any of these arrays, treat it as a manifest wrapper.
  for (const k of ["include", "files", "list", "paths"]) {
    if (Array.isArray(obj[k])) return true;
  }
  return false;
}

function coerceManifestList(obj) {
  // Returns an array of strings/objects if manifest-ish, else null.
  const flat = flattenSpec(obj);
  return Array.isArray(flat) ? flat : null;
}

function dedupeKeepOrder(arr) {
  const out = [];
  const seen = new Set();
  for (const x of arr) {
    const k = String(x);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(x);
  }
  return out;
}

async function resolveDomainItems(domainName, items, timeoutMs, bounds, depth) {
  // items is a flat list (strings or objects).
  // Returns fully expanded array of objects (pages/panels), manifest-expanded.
  const out = [];

  if (!Array.isArray(items)) return out;

  // Case A: already resolved objects
  if (items.length > 0 && isObj(items[0])) {
    for (let i = 0; i < items.length; i++) {
      if (!isObj(items[i])) throw new Error(`${domainName}[${i}] must be an object`);
      out.push(items[i]);
    }
    return out;
  }

  // Case B: strings -> fetch, and expand if the fetched JSON is a manifest
  const urls = dedupeKeepOrder(
    items
      .filter(isNonEmptyStr)
      .map(normalizeConfigPath)
      .filter(Boolean)
  );

  bounds.filesFetched += urls.length;
  if (bounds.filesFetched > bounds.maxFiles) {
    throw new Error(
      `${domainName} include expansion exceeded maxFiles=${bounds.maxFiles} (possible recursion or bad include set)`
    );
  }

  for (const url of urls) {
    const obj = await fetchJsonAbs(url, timeoutMs);

    if (looksLikeManifestObject(obj)) {
      if (depth >= bounds.maxDepth) {
        throw new Error(
          `${domainName} manifest expansion exceeded maxDepth=${bounds.maxDepth} at ${url}`
        );
      }

      const more = coerceManifestList(obj) || [];
      // Support common manifest style where entries are bare filenames, relative to a folder.
      // Your manifest files are at:
      //   /config/pages.manifest.json  -> files: ["home.json", ...]
      //   /config/panels.manifest.json -> files: ["topbar_core.json", ...]
      //
      // Those filenames need to be resolved under /config/pages/ and /config/panels/.
      const basePrefix =
        url.endsWith("/pages.manifest.json") || url.endsWith("/pages.manifest")
          ? "pages/"
          : url.endsWith("/panels.manifest.json") || url.endsWith("/panels.manifest")
          ? "panels/"
          : null;

      const normalizedMore = more.map((entry) => {
        if (!isNonEmptyStr(entry)) return entry;

        const s = String(entry).trim();

        // If it's already a path containing '/', respect it.
        if (s.includes("/")) return s;

        // If it’s a bare filename and we can infer basePrefix, apply it.
        if (basePrefix) return `${basePrefix}${s}`;

        // Otherwise leave as-is (normalizeConfigPath will anchor it under /config/)
        return s;
      });

      const expanded = await resolveDomainItems(
        domainName,
        normalizedMore,
        timeoutMs,
        bounds,
        depth + 1
      );
      out.push(...expanded);
      continue;
    }

    if (!isObj(obj)) {
      throw new Error(`${domainName} file ${url} did not parse to an object`);
    }

    out.push(obj);
  }

  return out;
}

async function loadDomain(domainName, domainSpec, timeoutMs, opts = {}) {
  const flat = flattenSpec(domainSpec);

  if (flat == null) {
    throw new Error(
      `app.json ${domainName} must be an array (objects or file paths), or an object with include/files/list/paths`
    );
  }

  const bounds = {
    maxFiles: Number.isFinite(opts.maxFiles) ? opts.maxFiles : 128,
    maxDepth: Number.isFinite(opts.maxDepth) ? opts.maxDepth : 3,
    filesFetched: 0,
  };

  // If include-resolved objects, return directly
  if (flat.length === 0) return [];
  if (isObj(flat[0])) {
    for (let i = 0; i < flat.length; i++) {
      if (!isObj(flat[i])) throw new Error(`${domainName}[${i}] must be an object`);
    }
    return flat;
  }

  // Otherwise, expand includes/manifests into concrete objects
  return await resolveDomainItems(domainName, flat, timeoutMs, bounds, 0);
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

  const pages = await loadDomain("pages", cfg.pages, timeoutMs, {
    maxFiles: 128,
    maxDepth: 3,
  });

  const panels = await loadDomain("panels", cfg.panels, timeoutMs, {
    maxFiles: 256,
    maxDepth: 3,
  });

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