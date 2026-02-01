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

async function postJson(url, bodyObj, timeoutMs = 2500) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyObj),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

export function createBindingStore(opts = {}) {
  // Default to controller hostname that rt-display can resolve.
  const stateBatchUrl =
    opts.stateBatchUrl ||
    "http://rt-controller:8625/api/v1/ui/state/batch";

  // Tiny cache to keep one render pass from firing N POSTs.
  // Cleared each time resolveAll() is called (if your UI has it),
  // otherwise it still helps across rapid successive resolves.
  let cache = new Map();

  async function stateBatch(keys) {
    const uniq = Array.from(new Set(keys.filter(Boolean).map(String)));
    if (uniq.length === 0) return {};

    const resp = await postJson(stateBatchUrl, { keys: uniq });
    if (!resp || resp.ok !== true || !resp.data || !resp.data.values) {
      return {};
    }
    return resp.data.values; // map: key -> { ok, encoding, value }
  }

  return {
    // Optional hook your UI can call at the start of each refresh cycle
    // to prevent stale cache surprises.
    beginCycle() {
      cache = new Map();
    },

    // The original single-binding resolver.
    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();

      if (src === "api") {
        return await fetchJson(binding.url);
      }

      if (src === "state") {
        const key = String(binding?.key || "");
        if (!key) return null;

        if (cache.has(key)) return cache.get(key);

        // Batch-of-1 fallback (still uses the batch endpoint)
        const values = await stateBatch([key]);
        const entry = values[key];
        const val = entry && entry.ok ? entry.value : null;

        cache.set(key, val);
        return val;
      }

      return null;
    },

    // A convenience method if your UI can resolve many bindings at once.
    // If you don't use it yet, you can ignore it safely.
    async resolveMany(bindings) {
      const stateKeys = [];
      const out = [];

      // First pass: collect keys and mark non-state results
      for (const b of bindings) {
        const src = String(b?.source || "").toLowerCase();
        if (src === "state") {
          const k = String(b?.key || "");
          out.push({ _stateKey: k });
          if (k && !cache.has(k)) stateKeys.push(k);
        } else if (src === "api") {
          out.push(await fetchJson(b.url));
        } else {
          out.push(null);
        }
      }

      // Batch read missing keys
      if (stateKeys.length) {
        const values = await stateBatch(stateKeys);
        for (const k of stateKeys) {
          const entry = values[k];
          cache.set(k, entry && entry.ok ? entry.value : null);
        }
      }

      // Second pass: fill state results
      return out.map((v) => {
        if (v && v._stateKey !== undefined) {
          return cache.get(v._stateKey) ?? null;
        }
        return v;
      });
    },
  };
}
