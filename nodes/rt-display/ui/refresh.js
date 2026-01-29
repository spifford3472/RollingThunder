export function startPanelRefresh({ slot, panel, bindings, store, render }) {
  const mode = panel?.refresh?.mode || "poll";
  const intervalMs = Math.max(250, Number(panel?.refresh?.intervalMs || 1000));

  let stopped = false;

  async function collectOnce() {
    const data = {};
    const list = (Array.isArray(bindings) ? bindings : []).filter(b => b?.id && b?.source);

    for (const b of list) {
      try {
        data[b.id] = await store.resolve(b);
      } catch (e) {
        data[b.id] = null;
        data.__errors = data.__errors || {};
        data.__errors[b.id] = String(e?.message || e);
      }
    }
    return data;
  }

  async function tick() {
    if (stopped) return;
    const data = await collectOnce();
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
