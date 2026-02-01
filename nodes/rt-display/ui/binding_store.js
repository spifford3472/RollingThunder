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

async function postJson(url, body, timeoutMs = 2500) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

export function createBindingStore(opts = {}) {
  const stateBatchUrl =
    opts.stateBatchUrl || "http://rt-controller:8625/api/v1/ui/state/batch";

  return {
    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();

      if (src === "api") {
        return await fetchJson(binding.url);
      }

      if (src === "state") {
        const key = String(binding?.key || "");
        if (!key) return null;

        const resp = await postJson(stateBatchUrl, { keys: [key] });
        const entry = resp?.data?.values?.[key];
        return entry?.ok ? entry.value : null;
      }

      return null;
    },
  };
}
