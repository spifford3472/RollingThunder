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

function qs(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj || {})) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

function safeObj(x) {
  return (x && typeof x === "object") ? x : null;
}

function coerceRow(entry) {
  // entry shape from /api/v1/ui/state/scan:
  // { key, type, preview: {...} }
  const e = safeObj(entry) || {};
  const pv = safeObj(e.preview) || {};
  return {
    key: e.key ?? null,
    type: e.type ?? null,
    ...pv,
  };
}

function matchFilter(row, filterObj) {
  const f = safeObj(filterObj);
  if (!f) return true;

  for (const [k, want] of Object.entries(f)) {
    const got = row?.[k];
    if (want == null) continue;
    if (String(got) !== String(want)) return false;
  }
  return true;
}

export function createBindingStore(opts = {}) {
  const stateBatchUrl = opts.stateBatchUrl || "/api/v1/ui/state/batch";
  const stateScanUrl  = opts.stateScanUrl  || "/api/v1/ui/state/scan";

  // Controller SSE endpoint (shared connection)
  const busSubscribeUrl = opts.busSubscribeUrl || "/api/v1/ui/bus/subscribe";

  // NEW: UI -> controller intent endpoint (HTTP POST)
  const uiIntentUrl = opts.uiIntentUrl || "/api/v1/ui/intent";

  // topic -> Set<fn>
  const _handlers = new Map();
  const _topics = new Map(); // topic -> refs

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

    const minMs = opts.busReconnectMinMs ?? 250;
    const maxMs = opts.busReconnectMaxMs ?? 750;
    const delay = minMs + Math.floor(Math.random() * (maxMs - minMs + 1));

    _reconnectTimer = setTimeout(() => {
      _reconnectTimer = null;
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
    if (!_anySubscriptions()) return;

    _closeSse();

    const es = new EventSource(busSubscribeUrl, { withCredentials: false });
    _es = es;

    es.onopen = () => { _esConnected = true; };

    es.addEventListener("hello", () => { _esConnected = true; });

    es.addEventListener("message", (ev) => {
      const obj = _parseJson(ev?.data);
      if (!obj) return;

      const topic = _extractTopic(obj);
      if (!topic) return;

      const refs = _topics.get(topic) || 0;
      if (refs <= 0) return;

      const inner = (obj.data && typeof obj.data === "object") ? obj.data : obj;
      _emit(topic, inner);
    });

    es.addEventListener("eos", () => {
      _esConnected = false;
      _closeSse();
      _scheduleReconnect("eos");
    });

    es.onerror = () => {
      _esConnected = false;
      _closeSse();
      _scheduleReconnect("error");
    };
  }

  function subscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _topics.get(topic) || 0;
    _topics.set(topic, cur + 1);

    if (!_es) _connectSse("subscribe");
  }

  function unsubscribe(topic) {
    topic = String(topic || "").trim();
    if (!topic) return;

    const cur = _topics.get(topic) || 0;
    const next = Math.max(0, cur - 1);
    if (next === 0) _topics.delete(topic);
    else _topics.set(topic, next);

    if (!_anySubscriptions()) _closeSse();
  }

  function on(topic, fn) {
    topic = String(topic || "").trim();
    if (!topic || typeof fn !== "function") return () => {};

    const set = _ensureHandlerSet(topic);
    set.add(fn);

    return () => {
      const s = _handlers.get(topic);
      if (s) s.delete(fn);
    };
  }

  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
      try { _closeSse(); } catch (_) {}
    });
  }

  // NEW: publish an intent to the controller (bounded, auditable)
  async function publishIntent({ intent, params = null, pageId = null, panelId = null, source = "rt-display" }, timeoutMs = 1500) {
    const started = Date.now();
    const it = String(intent || "").trim();
    if (!it) return mkResult({ ok: false, err: "missing_intent", meta: { source: "intent", locator: uiIntentUrl, ms: 0 } });

    const body = {
      intent: it,
      params: safeObj(params) || null,
      page_id: pageId ? String(pageId) : null,
      panel_id: panelId ? String(panelId) : null,
      source: String(source || "rt-display"),
      ts_ms: Date.now(),
    };

    try {
      const resp = await postJson(uiIntentUrl, body, timeoutMs);
      // Expect { ok:true } or { ok:false, err:"..." }. If controller returns something else, still treat HTTP 200 as ok.
      const ok = resp?.ok !== false;
      return mkResult({ ok, value: resp, meta: { source: "intent", locator: uiIntentUrl, ms: Date.now() - started } });
    } catch (e) {
      return mkResult({ ok: false, err: e?.message || e, meta: { source: "intent", locator: uiIntentUrl, ms: Date.now() - started } });
    }
  }

  return {
    subscribe,
    on,
    unsubscribe,
    publishIntent,

    async resolve(binding) {
      const src = String(binding?.source || "").trim().toLowerCase();
      const started = Date.now();

      if (src === "api") {
        const url = String(binding?.url || "");
        if (!url) return mkResult({ ok: false, err: "missing_url", meta: { source: "api", locator: "", ms: 0 } });

        try {
          const val = await fetchJson(url);
          return mkResult({ ok: true, value: val, meta: { source: "api", locator: url, ms: Date.now() - started } });
        } catch (e) {
          return mkResult({ ok: false, err: e?.message || e, meta: { source: "api", locator: url, ms: Date.now() - started } });
        }
      }

      if (src === "state") {
        const key = String(binding?.key || "");
        if (!key) return mkResult({ ok: false, err: "missing_key", meta: { source: "state", locator: "", ms: 0 } });

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

      if (src === "scan") {
        // binding: { match: "rt:services:*", limit, cursor?, filter? }
        const match = String(binding?.match || "").trim();
        const limit = Math.max(1, Math.min(500, Number(binding?.limit || 50)));
        const cursor = binding?.cursor != null ? Number(binding.cursor) : undefined;

        if (!match) {
          return mkResult({ ok: false, err: "missing_match", meta: { source: "scan", locator: "", ms: 0 } });
        }

        try {
          const url = `${stateScanUrl}${qs({ match, limit, cursor })}`;
          const resp = await fetchJson(url);
          const keys = resp?.data?.keys;
          const list = Array.isArray(keys) ? keys.map(coerceRow) : [];

          const filtered = list.filter(row => matchFilter(row, binding?.filter));
          return mkResult({
            ok: true,
            value: filtered,
            meta: { source: "scan", locator: match, ms: Date.now() - started, next_cursor: resp?.data?.next_cursor ?? null }
          });
        } catch (e) {
          return mkResult({ ok: false, err: e?.message || e, meta: { source: "scan", locator: match, ms: Date.now() - started } });
        }
      }

      if (src === "bus") {
        const t = String(binding?.topic || "").trim();
        if (!t) return mkResult({ ok: false, err: "missing_topic", meta: { source: "bus", locator: "", ms: 0 } });
        return mkResult({ ok: false, err: "bus_binding_not_resolvable", meta: { source: "bus", locator: t, ms: 0 } });
      }

      return mkResult({ ok: false, err: `unknown_source:${src || "?"}`, meta: { source: src || "?", locator: "", ms: 0 } });
    },
  };
}