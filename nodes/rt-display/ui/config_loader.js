async function fetchJson(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
  return await resp.json();
}

function indexById(list) {
  const out = {};
  for (const o of list) if (o && o.id) out[o.id] = o;
  return out;
}

export async function loadConfigBundle() {
  // ui/ is at nodes/rt-display/ui/
  // repo-root config/ is at ../../config/ from here
const appUrl = "/config/app.json";
const pagesManifestUrl = "/config/pages.manifest.json";
const panelsManifestUrl = "/config/panels.manifest.json";
const pagesDir = "/config/pages";
const panelsDir = "/config/panels";



  const app = await fetchJson(appUrl);

  const pagesManifest = await fetchJson(pagesManifestUrl);
  const panelsManifest = await fetchJson(panelsManifestUrl);

  const pages = [];
  for (const f of (pagesManifest.files || [])) {
    pages.push(await fetchJson(`${pagesDir}/${f}`));
  }

  const panels = [];
  for (const f of (panelsManifest.files || [])) {
    panels.push(await fetchJson(`${panelsDir}/${f}`));
  }

  return {
    app,
    pages,
    panels,
    pagesById: indexById(pages),
    panelsById: indexById(panels),
  };
}
