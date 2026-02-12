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

  // Controller SSE endpoint (shared connection). Controller emits:
  // - event: hello
  // - event: message  (JSON: { channel, ts, server_time_ms, data:<published> })
  // - event: eos      (bounded stream ends; we must reconnect)
  const busSubscribeUrl = opts.busSubscribeUrl || "/api/v1/ui/bus/subscribe";

  // topic -> Set<fn>
  const _handlers = new Map();

  // Track which topics the runtime currently cares about (purely for routing/refs)
  const _topics = new Map(); // topic -> refs

  // Single shared SSE connection state
  let _es = null;
  let _esConnected = false;
  let _reconnectTimer = null;

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

  function _anySubscriptions() {
    for (const [, refs] of _topics) {
      if ((refs || 0) > 0) return true;
    }
    return false;
  }

  function _clearReconnectTimer() {
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = null;
    }
  }

  function _scheduleReconnect(reason) {
    if (!_anySubscriptions()) return;
    if (_reconnectTimer) return;

    // small jitter so multiple kiosks don’t reconnect in lockstep
    const minMs = opts.busReconnectMinMs ?? 250;
    const maxMs = opts.busReconnectMaxMs ?? 750;
    const delay = minMs + Math.floor(Math.random() * (maxMs - minMs + 1));

    _reconnectTimer = setTimeout(() => {
      _reconnectTimer = null;
      // only reconnect if still needed
      if (_anySubscriptions()) _connectSse(`reconnect:${reason || "unknown"}`);
    }, delay);
  }

  function _closeSse() {
    _clearReconnectTimer();
    if (_es) {
      try { _es.close(); } catch (_) {}
      _es = null;
    }
    _esConnected = false;
  }

  function _parseJson(s) {
    if (typeof s !== "string") return null;
    const t = s.trim();
    if (!t) return null;
    try { return JSON.parse(t); } catch (_) { return null; }
  }

  function _extractTopic(obj) {
    // Controller "message" shape:
    // { channel, ts, server_time_ms, data: <published> }
    // Published payload SHOULD carry topic, so look there first.
    if (obj && typeof obj === "object") {
      const inner = (obj.data && typeof obj.data === "object") ? obj.data : null;
      const t1 = inner && typeof inner.topic === "string" ? inner.topic : null;
      const t2 = typeof obj.topic === "string" ? obj.topic : null;
      return (t1 || t2 || "").trim();
    }
    return "";
  }

  function _connectSse(reason) {
    _clearReconnectTimer();

    // If no one is subscribed, do nothing.
    if (!_anySubscriptions()) return;

    // Reset any existing connection
    _closeSse();

    const es = new EventSource(busSubscribeUrl, { withCredentials: false });
    _es = es;

    es.onopen = () => {
      _esConnected = true;
      // console.log("SSE open", reason || "");
    };

    es.addEventListener("hello", (ev) => {
      _esConnected = true;
      // Optional: you could emit a diagnostic topic here if you ever want.
      // const obj = _parseJson(ev.data);
      // console.log("SSE hello", obj);
    });

    // IMPORTANT: controller emits `event: message`, not default "message"
    es.addEventListener("message", (ev) => {

      const obj = _parseJson(ev?.data);
      if (!obj) return;

      const topic = _extractTopic(obj);
      if (!topic) return;

      // Only dispatch if we’re currently subscribed (cheap guard)
      const refs = _topics.get(topic) || 0;
      if (refs <= 0) return;

      // Deliver the *published payload* if present, else the wrapper
      const inner = (obj.data && typeof obj.data === "object") ? obj.data : obj;
      _emit(topic, inner);
    });

    es.addEventListener("eos", () => {
      // Stream ended intentionally (bounded). Force-close, then reconnect if still needed.
      _esConnected = false;
      _closeSse();               // <-- KEY: kill the dead EventSource
      _scheduleReconnect("eos");
    });

    es.onerror = () => {
      // Keep quiet; we can add rate-limited diagnostics later.
      _esConnected = false;
      _closeSse();               // <-- KEY: kill the dead EventSource
      _scheduleReconnect("error");
    };

  }

  function subscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _topics.get(topic) || 0;
    _topics.set(topic, cur + 1);

    // Ensure the shared SSE connection exists
    if (!_es) _connectSse("subscribe");
  }

  function unsubscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _topics.get(topic) || 0;
    const next = Math.max(0, cur - 1);
    if (next === 0) _topics.delete(topic);
    else _topics.set(topic, next);

    // If nothing left subscribed, close the shared SSE
    if (!_anySubscriptions()) _closeSse();
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
      // Note: we do NOT auto-unsubscribe the bus topic here because
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
        // Bus is consumed via subscribe()/on(), not resolve().
        const t = String(binding?.topic || "").trim();
        if (!t) return mkResult({ ok: false, err: "missing_topic", meta: { source: "bus", locator: "", ms: 0 } });
        return mkResult({ ok: false, err: "bus_binding_not_resolvable", meta: { source: "bus", locator: t, ms: 0 } });
      }

      return mkResult({ ok: false, err: `unknown_source:${src || "?"}`, meta: { source: src || "?", locator: "", ms: 0 } });
    },
  };
}

