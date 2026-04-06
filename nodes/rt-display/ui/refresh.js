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

  const topic = String(panel?.refresh?.topic || "").trim();

  const pushReady =
    mode !== "push"
      ? true
      : (
          !!topic &&
          typeof store?.subscribe === "function" &&
          typeof store?.on === "function" &&
          typeof store?.unsubscribe === "function"
        );

  let stopped = false;
  let inflight = false;
  let needsRerun = false;

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

    let results = null;

    try {
      if (typeof store?.resolveMany === "function") {
        results = await store.resolveMany(list);
      }
    } catch (_) {
      results = null;
    }

    for (const b of list) {
      const id = String(b.id);
      const res = results ? results[id] : await store.resolve(b);
      rt.bindings[id] = res;

      const prev = slot.__rtData?.[id];

      // Only treat as an error if this binding failed AND we have no last-good value.
      if (res?.ok === false && prev == null) {
        rt.panel.has_error = true;
      }

      // Only treat as missing if the binding succeeded with null AND we have no last-good value.
      if (res?.ok === true && res.value == null && prev == null) {
        rt.panel.has_missing = true;
      }

      const ms = Number(res?.meta?.ms ?? NaN);
      if (Number.isFinite(ms) && ms > 2000) {
        rt.panel.slow_bindings.push(id);
      }

      if (res?.ok) {
        data[id] = res.value;
      } else if (prev !== undefined) {
        // Preserve last good value
        data[id] = prev;
      } else {
        data[id] = null;
      }

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
        tick();
      }
    }
  }

  tick();

  let unsub = null;

  if (mode === "push" && pushReady) {
    store.subscribe(topic);

    unsub = store.on(topic, (msg) => {
      try {
        const scanPrefixes = list
          .filter(b => String(b?.source || "").toLowerCase() === "scan")
          .map(b => String(b?.match || "").trim())
          .filter(m => m.endsWith("*"))
          .map(m => m.slice(0, -1))
          .filter(Boolean);

        const keys = msg?.payload?.keys ?? msg?.data?.payload?.keys;

        for (const k of (Array.isArray(keys) ? keys : [])) {
          if (typeof k !== "string") continue;

          for (const p of scanPrefixes) {
            if (k.startsWith(p)) {
              tick();
              return;
            }
          }
        }

        if (!Array.isArray(keys) || keys.length === 0) {
          tick();
          return;
        }

        for (const k of keys) {
          const ks = (typeof k === "string") ? k.trim() : String(k || "");
          if (ks && panelStateKeys.has(ks)) {
            tick();
            return;
          }
        }
      } catch (_) {
        // Poll fallback still runs
      }
    });
  }

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
      try {
        store.unsubscribe(topic);
      } catch (_) {}
    }
  };
}