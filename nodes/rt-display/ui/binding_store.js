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
  // ---- State batching internals (single endpoint, read-only) ----
  const STATE_ENDPOINT = "/api/v1/ui/state/batch";

  // Small TTL cache: keeps UI behavior stable under frequent refresh
  const CACHE_TTL_MS = 250;

  // key -> { ts:number, value:any } where value is already "final" (either actual value or null)
  const cache = new Map();

  // Micro-batching: collect keys requested within the same tick
  let pendingKeys = new Set();
  let flushTimer = null;

  // All resolves in-flight for current batch wait on this
  let inFlightFlushPromise = null;
  let inFlightFlushResolve = null;

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

  async function flushPending() {
    const keys = Array.from(pendingKeys);
    pendingKeys = new Set();

    // Reset flush controls early to allow next batch while this one runs
    const resolveFlush = inFlightFlushResolve;
    inFlightFlushPromise = null;
    inFlightFlushResolve = null;

    if (keys.length === 0) {
      resolveFlush && resolveFlush();
      return;
    }

    try {
      const resp = await postJson(
        STATE_ENDPOINT,
        {
          schema_version: "ui.state.batch.v1",
          keys,
        },
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
      // Preserve existing behavior: on any failure, treat all as null
      for (const k of keys) {
        putCached(k, null);
      }
    } finally {
      resolveFlush && resolveFlush();
    }
  }

  function scheduleFlush() {
    if (flushTimer !== null) return;

    // One flush at end of current event loop tick
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flushPending();
    }, 0);
  }

  function ensureFlushPromise() {
    if (inFlightFlushPromise) return inFlightFlushPromise;
    inFlightFlushPromise = new Promise((resolve) => {
      inFlightFlushResolve = resolve;
    });
    return inFlightFlushPromise;
  }

  // ---- Public API ----
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

        // Queue for batch and await the flush that includes it
        pendingKeys.add(key);
        const p = ensureFlushPromise();
        scheduleFlush();
        await p;

        const after = getCached(key);
        return after === undefined ? null : after;
      }

      return null;
    },
  };
}
