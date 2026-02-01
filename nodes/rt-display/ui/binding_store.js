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

export function createBindingStore() {
  const STATE_ENDPOINT = "/api/v1/ui/state/batch";

  // Tiny TTL cache prevents request storms during frequent re-render/refresh.
  const CACHE_TTL_MS = 250;

  // key -> { ts:number, value:any|null }
  const cache = new Map();

  // Micro-batch state keys requested in the same tick.
  let pendingKeys = new Set();
  let flushTimer = null;

  // Promise that resolves when the current scheduled flush completes.
  let flushPromise = null;
  let flushResolve = null;

  function getCached(key) {
    const hit = cache.get(key);
    if (!hit) return undefined;
    if ((Date.now() - hit.ts) > CACHE_TTL_MS) {
      cache.delete(key);
      return undefined;
    }
    return hit.value;
  }

  function putCached(key, valueOrNull) {
    cache.set(key, { ts: Date.now(), value: valueOrNull });
  }

  function ensureFlushPromise() {
    if (flushPromise) return flushPromise;
    flushPromise = new Promise((resolve) => {
      flushResolve = resolve;
    });
    return flushPromise;
  }

  async function flushPending() {
    const keys = Array.from(pendingKeys);
    pendingKeys = new Set();

    // Clear the promise early so new requests can schedule the next batch
    const done = flushResolve;
    flushPromise = null;
    flushResolve = null;

    if (keys.length === 0) {
      done && done();
      return;
    }

    try {
      const resp = await postJson(
        STATE_ENDPOINT,
        { schema_version: "ui.state.batch.v1", keys: Array.from(new Set(keys)) },
        2500
      );

      // Controller contract: resp.data.values[key] = { ok, encoding, value }
      const values = resp?.data?.values || {};

      for (const k of keys) {
        const entry = values[k];
        const val = (entry && entry.ok) ? entry.value : null;
        putCached(k, val);
      }
    } catch (e) {
      // Preserve legacy UI behavior: failures yield null, never throw.
      for (const k of keys) {
        putCached(k, null);
      }
    } finally {
      done && done();
    }
  }

  function scheduleFlush() {
    if (flushTimer !== null) return;
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flushPending();
    }, 0);
  }

  return {
    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();

      if (src === "api") {
        return await fetchJson(binding.url);
      }

      if (src === "state") {
        const key = binding?.key;
        if (!key) return null;

        const cached = getCached(key);
        if (cached !== undefined) return cached;

        pendingKeys.add(key);
        const p = ensureFlushPromise();
        scheduleFlush();
        await p;

        const after = getCached(key);
        return after === undefined ? null : after;
      }

      return null;
    }
  };
}
