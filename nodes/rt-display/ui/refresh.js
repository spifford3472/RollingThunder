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

function extractKeys(msg) {
  const keys =
    msg?.payload?.keys ??
    msg?.payload?.changed_keys ??
    msg?.data?.payload?.keys ??
    msg?.data?.payload?.changed_keys;

  return Array.isArray(keys)
    ? keys.map(k => String(k || "").trim()).filter(Boolean)
    : [];
}

function isBrowseKey(k) {
  return k === "rt:ui:browse" || k.startsWith("rt:ui:browse:");
}

function stateBindingMatchesKey(binding, changedKey) {
  const key = String(binding?.key || "").trim();
  if (!key || !changedKey) return false;

  return (
    key === changedKey ||
    changedKey.startsWith(key + ":") ||
    key.startsWith(changedKey + ":")
  );
}

function bindingIsBrowseRelevant(binding, changedKeys) {
  const key = String(binding?.key || "").trim();
  if (!key || !key.startsWith("rt:ui:browse")) return false;

  return changedKeys.some(k => stateBindingMatchesKey(binding, k));
}

export function startPanelRefresh({ slot, panel, bindings, store, render }) {
  const mode = (panel?.refresh?.mode || "push").toLowerCase();
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

  const panelStateKeys = new Set(
    list
      .filter(b => String(b?.source || "").toLowerCase() === "state")
      .map(b => String(b?.key || "").trim())
      .filter(Boolean)
  );

  async function collectOnce(resolveList = list) {
    const data = {};
    const prevData = slot.__rtData || {};
    const prevRt = prevData.__rt || {};
    const prevBindingResults = prevRt.bindings || {};

    const resolveIds = new Set(
      (Array.isArray(resolveList) ? resolveList : [])
        .map(b => String(b?.id || ""))
        .filter(Boolean)
    );

    const rt = {
      bindings: { ...prevBindingResults },
      ts_ms: Date.now(),
      panel: {
        has_error: false,
        has_missing: false,
        slow_bindings: [],
      },
    };

    let results = null;

    try {
      if (typeof store?.resolveMany === "function" && resolveList.length > 0) {
        results = await store.resolveMany(resolveList);
      }
    } catch (_) {
      results = null;
    }

    for (const b of list) {
      const id = String(b.id);

      if (!resolveIds.has(id)) {
        data[id] = prevData[id] !== undefined ? prevData[id] : null;
        continue;
      }

      const res = results ? results[id] : await store.resolve(b);
      rt.bindings[id] = res;

      const prev = prevData[id];

      if (res?.ok === false && prev == null) {
        rt.panel.has_error = true;
      }

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

  async function tick(resolveList = list, opts = {}) {
    if (stopped) return;

    // Important: never queue a render backlog. If a refresh is already running,
    // drop this event. The next bus event will carry current controller state.
    if (inflight) return;

    inflight = true;
    try {
      const data = await collectOnce(resolveList);
      slot.__rtData = data;

      const life = classifyPanelFromResults(panel, list, data);
      data.__rt.lifecycle = life;

      if (opts.updateHeader !== false) {
        renderHdr(slot, panel, life);
      }

      render(data);
    } finally {
      inflight = false;
    }
  }

  tick();

  let unsub = null;

  if (mode === "push" && pushReady) {
    store.subscribe(topic);

    unsub = store.on(topic, (msg) => {
      try {
        const keys = extractKeys(msg);

        // Malformed or empty payloads must not cause global refresh.
        if (!Array.isArray(keys) || keys.length === 0) {
          return;
        }

        const onlyBrowse = keys.every(isBrowseKey);

        if (onlyBrowse) {
          const browseBindings = list.filter(b =>
            String(b?.source || "").toLowerCase() === "state" &&
            bindingIsBrowseRelevant(b, keys)
          );

          if (browseBindings.length > 0) {
            // Lightweight browse update:
            // resolve only declared browse binding(s), preserve all model data.
            tick(browseBindings, { updateHeader: false });
          }

          return;
        }

        const scanPrefixes = list
          .filter(b => String(b?.source || "").toLowerCase() === "scan")
          .map(b => String(b?.match || "").trim())
          .filter(m => m.endsWith("*"))
          .map(m => m.slice(0, -1))
          .filter(Boolean);

        for (const k of keys) {
          for (const p of scanPrefixes) {
            if (k.startsWith(p)) {
              tick();
              return;
            }
          }
        }

        for (const k of keys) {
          const ks = String(k || "").trim();
          if (!ks) continue;

          if (
            panelStateKeys.has(ks) ||
            [...panelStateKeys].some(pk => ks.startsWith(pk + ":") || pk.startsWith(ks + ":"))
          ) {
            tick();
            return;
          }
        }
      } catch (_) {
        // Event-driven only; no polling fallback.
      }
    });
  }

  // Event-driven only; no panel polling fallback.
  const t = null;

  slot.__rtStop = () => {
    stopped = true;
    if (t) clearInterval(t);

    if (typeof unsub === "function") unsub();

    if (mode === "push" && pushReady) {
      try {
        store.unsubscribe(topic);
      } catch (_) {}
    }
  };
}