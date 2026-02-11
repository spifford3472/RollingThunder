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
  const slow = Array.isArray(slot?.__rt_last_slow) ? slot.__rt_last_slow : [];

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

  // If panel requests push, but runtime doesn't implement it yet,
  // fall back to polling at a safe cadence.
  const pushFallbackMs = Math.max(250, Number(panel?.refresh?.fallbackPollMs || 1000));
  const intervalMs = (mode === "push") ? pushFallbackMs : pollIntervalMs;


  let stopped = false;

  async function collectOnce() {
    const data = {};
    const list = (Array.isArray(bindings) ? bindings : []).filter(b => b?.id && b?.source);

    const rt = { bindings: {}, ts_ms: Date.now() };
    rt.panel = {
      has_error: false,
      has_missing: false,
      slow_bindings: [], // list of binding ids
    };


    for (const b of list) {
      const id = String(b.id);
      const res = await store.resolve(b); // {ok,value,err,meta}
      rt.bindings[id] = res;
      if (res?.ok === false) rt.panel.has_error = true;
      if (res?.ok === true && res.value == null) rt.panel.has_missing = true;

      const ms = Number(res?.meta?.ms ?? NaN);
      if (Number.isFinite(ms) && ms > 1000) rt.panel.slow_bindings.push(id);
      // back-compat: renderers still read raw values
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

    const data = await collectOnce();
    slot.__rtData = data; // debug hook

    let life = classifyPanelFromResults(panel, data);

    // If refresh mode is push but not implemented, force CONFIG semantics
    // (but we still render + poll so the UI stays usable).
    if (mode === "push") {
      const issues = Array.isArray(life?.issues) ? life.issues.slice() : [];
      issues.unshift("refresh:push_unimplemented");
      life = { state: "config", issues };
    }

    data.__rt.lifecycle = life;

    renderHdr(slot, panel, life);
    render(data);
  }

  tick();

  // Always run a timer for now.
  // For "push" we are intentionally in fallback polling mode until bus subscriptions exist.
  const t = setInterval(tick, intervalMs);
  slot.__rtStop = () => { stopped = true; clearInterval(t); };
}
