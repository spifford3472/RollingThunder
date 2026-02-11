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
  const stateBatchUrl = opts.stateBatchUrl || "/api/v1/ui/state/batch";

  // SSE endpoint on the controller (same-origin strongly preferred)
  // Example: /api/v1/ui/bus/subscribe?topic=rt/alerts/active
  const busSubscribeBaseUrl = opts.busSubscribeBaseUrl || "/api/v1/ui/bus/subscribe";

  // topic -> { es: EventSource, refs: number }
  const _streams = new Map();

  // topic -> Set<fn>
  const _handlers = new Map();

  function _emit(topic, msg) {
    const set = _handlers.get(topic);
    if (!set || set.size === 0) return;
    for (const fn of set) {
      try { fn(msg); } catch (e) { console.error("handler error", e); }
    }
  }

  function _ensureHandlerSet(topic) {
    let set = _handlers.get(topic);
    if (!set) { set = new Set(); _handlers.set(topic, set); }
    return set;
  }

  function subscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _streams.get(topic);
    if (cur) {
      cur.refs++;
      return;
    }

    const url = `${busSubscribeBaseUrl}?topic=${encodeURIComponent(topic)}`;
    const es = new EventSource(url, { withCredentials: false });

    const stream = { es, refs: 1 };
    _streams.set(topic, stream);

    es.onmessage = (ev) => {
      // Expect JSON: { topic, payload, ts_ms? }
      try {
        const obj = JSON.parse(ev.data);
        const t = String(obj?.topic || topic);
        // Only dispatch to matching topic (be strict)
        if (t !== topic) return;
        _emit(topic, obj);
      } catch (e) {
        console.error("SSE parse error", e, ev?.data);
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; keep it calm.
      // We do NOT spam UI. We just log once-ish.
      // (If you want rate-limited diagnostics later, we can add them.)
      // console.warn("SSE error for topic", topic);
    };
  }

  function unsubscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _streams.get(topic);
    if (!cur) return;

    cur.refs = Math.max(0, (cur.refs || 0) - 1);
    if (cur.refs > 0) return;

    try { cur.es.close(); } catch (_) {}
    _streams.delete(topic);
  }

  function on(topic, fn) {
    topic = String(topic || "").trim();
    if (!topic || typeof fn !== "function") return () => {};

    const set = _ensureHandlerSet(topic);
    set.add(fn);

    // Return unsubscribe callback
    return () => {
      const s = _handlers.get(topic);
      if (s) s.delete(fn);
      // Note: we do NOT auto-unsubscribe the SSE stream here because
      // refresh.js manages unsub() already. Keep behavior deterministic.
    };
  }

  return {
    subscribe,
    on,
    unsubscribe,

    async resolve(binding) {
      const src = String(binding?.source || "").toLowerCase();
      const started = Date.now();

      if (src === "api") {
        const url = String(binding?.url || "");
        if (!url) {
          return mkResult({ ok: false, err: "missing_url", meta: { source: "api", locator: "", ms: 0 } });
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

      if (src === "bus") {
        // For now: runtime doesn't resolve bus bindings via resolve().
        // Bus is consumed via subscribe()/on().
        const t = String(binding?.topic || "").trim();
        if (!t) return mkResult({ ok: false, err: "missing_topic", meta: { source: "bus", locator: "", ms: 0 } });
        return mkResult({ ok: false, err: "bus_binding_not_resolvable", meta: { source: "bus", locator: t, ms: 0 } });
      }

      return mkResult({ ok: false, err: `unknown_source:${src || "?"}`, meta: { source: src || "?", locator: "", ms: 0 } });
    },
  };
}
