async function fetchJson(url, timeoutMs = 2500) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

export function createBindingStore() {
  return {
    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();

      if (src === "api") {
        return await fetchJson(binding.url);
      }

      // state bindings will be wired later (via controller endpoint).
      if (src === "state") {
        return null;
      }

      return null;
    }
  };
}
