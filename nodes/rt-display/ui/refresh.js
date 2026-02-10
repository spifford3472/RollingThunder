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

  // driving-safe: one short reason max
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
  const mode = panel?.refresh?.mode || "poll";
  const intervalMs = Math.max(250, Number(panel?.refresh?.intervalMs || 1000));

  let stopped = false;

  async function collectOnce() {
    const data = {};
    const list = (Array.isArray(bindings) ? bindings : []).filter(b => b?.id && b?.source);

    const rt = { bindings: {}, ts_ms: Date.now() };

    for (const b of list) {
      const id = String(b.id);
      const res = await store.resolve(b); // {ok,value,err,meta}
      rt.bindings[id] = res;
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

    // ✅ Contract-based lifecycle
    const life = classifyPanelFromResults(panel, data);
    data.__rt.lifecycle = life;

    renderHdr(slot, panel, life);
    render(data);
  }

  tick();

  if (mode === "poll") {
    const t = setInterval(tick, intervalMs);
    slot.__rtStop = () => { stopped = true; clearInterval(t); };
  } else {
    slot.__rtStop = () => { stopped = true; };
  }
}
