// binding_store.js

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

function mkResult({ ok, value = null, err = null, meta = {} }) {
  return { ok: !!ok, value: ok ? value : null, err: err ? String(err) : null, meta };
}

export function createBindingStore(opts = {}) {
  const stateBatchUrl =
    opts.stateBatchUrl || "http://rt-controller:8625/api/v1/ui/state/batch";

  return {
    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();
      const started = Date.now();

      if (src === "api") {
        const url = String(binding?.url || "");
        if (!url) {
          return mkResult({ ok: false, err: "missing_url", meta: { source: "api", locator: "" , ms: 0 } });
        }
        try {
          const val = await fetchJson(url);
          return mkResult({ ok: true, value: val, meta: { source: "api", locator: url, ms: Date.now() - started } });
        } catch (e) {
          return mkResult({ ok: false, err: e?.message || e, meta: { source: "api", locator: url, ms: Date.now() - started } });
        }
      }

      if (src === "state") {
        const key = String(binding?.key || "");
        if (!key) {
          return mkResult({ ok: false, err: "missing_key", meta: { source: "state", locator: "", ms: 0 } });
        }
        try {
          const resp = await postJson(stateBatchUrl, { keys: [key] });
          const entry = resp?.data?.values?.[key];
          if (entry?.ok) {
            return mkResult({ ok: true, value: entry.value, meta: { source: "state", locator: key, ms: Date.now() - started } });
          }
          return mkResult({ ok: true, value: null, meta: { source: "state", locator: key, ms: Date.now() - started } });
        } catch (e) {
          return mkResult({ ok: false, err: e?.message || e, meta: { source: "state", locator: key, ms: Date.now() - started } });
        }
      }

      // Unknown source
      return mkResult({ ok: false, err: `unknown_source:${src || "?"}`, meta: { source: src || "?", locator: "", ms: 0 } });
    },
  };
}
