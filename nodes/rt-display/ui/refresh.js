// refresh.js
import { classifyPanelFromResults } from "./contract.js";

function pillHtml(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";
  return `<span class="${cls}">${label}</span>`;
}

function renderHdr(slot, panel, life) {
  const hdr = slot.querySelector(".rt-slot-hdr");
  if (!hdr) return;

  const title = (panel?.meta?.title || panel?.id || "").toString();

  const state = (life?.state || "warn").toLowerCase();
  const pill =
    state === "ok" ? pillHtml("ok", "OK") :
    state === "empty" ? pillHtml("warn", "EMPTY") :
    state === "config" ? pillHtml("bad", "CONFIG") :
    pillHtml("bad", "ERROR");

  // driving-safe: show only one short reason
  const reason = Array.isArray(life?.issues) && life.issues.length ? life.issues[0] : "";

  hdr.innerHTML = `
    <div class="rt-slot-hdr-row">
      <div class="rt-slot-title">${title}</div>
      <div class="rt-slot-right">
        ${pill}
        ${reason ? `<span class="rt-slot-reason">${reason}</span>` : ``}
      </div>
    </div>
  `;
}

export function startPanelRefresh({ slot, panel, bindings, store, render }) {
  const mode = (panel?.refresh?.mode || "poll").toLowerCase();
  const pollIntervalMs = Math.max(250, Number(panel?.refresh?.intervalMs || 1000));
  const list = (Array.isArray(bindings) ? bindings : []).filter(b => b?.id && b?.source);

  // Push config
  const topic = String(panel?.refresh?.topic || "").trim();

  const pushReady =
    mode !== "push" ? true :
    (!!topic && typeof store?.subscribe === "function" && typeof store?.on === "function" && typeof store?.unsubscribe === "function");

  let stopped = false;
  let inflight = false;
  let needsRerun = false;

  // Build the set of state keys this panel depends on (for cheap filtering)
  const panelStateKeys = new Set(
    list
      .filter(b => String(b?.source || "").toLowerCase() === "state")
      .map(b => String(b?.key || "").trim())
      .filter(Boolean)
  );

  async function collectOnce() {
    const data = {};

    const rt = { bindings: {}, ts_ms: Date.now() };
    rt.panel = {
      has_error: false,
      has_missing: false,
      slow_bindings: [],
    };

    for (const b of list) {
      const id = String(b.id);
      const res = await store.resolve(b);
      rt.bindings[id] = res;

      if (res?.ok === false) rt.panel.has_error = true;
      if (res?.ok === true && res.value == null) rt.panel.has_missing = true;

      const ms = Number(res?.meta?.ms ?? NaN);
      if (Number.isFinite(ms) && ms > 1000) rt.panel.slow_bindings.push(id);

      data[id] = res?.ok ? res.value : null;

      if (res?.ok === false) {
        data.__errors = data.__errors || {};
        data.__errors[id] = res.err || "error";
      }
    }

    data.__rt = rt;
    return data;
  }

  async function tick() {

    if (stopped) return;

    // Prevent overlapping runs (push can arrive while poll is running).
    if (inflight) {
      needsRerun = true;
      return;
    }

    inflight = true;
    try {
      const data = await collectOnce();
      slot.__rtData = data;

      const life = classifyPanelFromResults(panel, list, data);
      data.__rt.lifecycle = life;

      renderHdr(slot, panel, life);
      render(data);
    } finally {
      inflight = false;
      if (!stopped && needsRerun) {
        needsRerun = false;
        // Run one more time to catch any missed updates while we were inflight.
        tick();
      }
    }
  }

  // Initial render
  tick();

  let unsub = null;

  if (mode === "push" && pushReady) {
    store.subscribe(topic);

    unsub = store.on(topic, (msg) => {
      try {
        // Expect publish payload shape:
        // { topic:"state.changed", payload:{ keys:["rt:...","rt:..."] }, ts_ms?, source? }
        // But be tolerant:
        const keys = msg?.payload?.keys ?? msg?.data?.payload?.keys;

        // If no keys provided, treat as a general nudge.
        if (!Array.isArray(keys) || keys.length === 0) {
          tick();
          return;
        }

        // Only refresh if this push event touches a key we care about.
        for (const k of keys) {
          const ks = (typeof k === "string") ? k.trim() : String(k || "");
          if (ks && panelStateKeys.has(ks)) {
            tick();
            return;
          }
        }
      } catch (_) {
        // Fail-safe: ignore (poll fallback still runs)
      }
    });
  }

  // Poll fallback:
  // - poll mode: intervalMs
  // - push mode: fallbackPollMs (default 5000ms)
  const fallbackMs =
    mode === "push"
      ? Math.max(1000, Number(panel?.refresh?.fallbackPollMs || 5000))
      : pollIntervalMs;

  const t = setInterval(tick, fallbackMs);

  slot.__rtStop = () => {
    stopped = true;
    clearInterval(t);

    if (typeof unsub === "function") unsub();

    if (mode === "push" && pushReady) {
      try { store.unsubscribe(topic); } catch (_) {}
    }
  };
}
