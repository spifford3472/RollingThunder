function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"
  }[c]));
}

function pill(kind, label) {
  const cls =
    kind === "ok" ? "rt-pill ok" :
    kind === "warn" ? "rt-pill warn" :
    "rt-pill bad";
  return `<span class="${cls}">${esc(label)}</span>`;
}

/**
 * renderRadioStatus(container, panel, data)
 * Expects binding id: snapshot
 *
 * snapshot shape (future):
 * {
 *   "freq_hz": 28305000,
 *   "mode": "SSB",
 *   "ptt": false,
 *   "tx": false,
 *   "power_w": 100,
 *   "swr": 1.2,
 *   "updated_ms": 1700000000000
 * }
 */
export function renderRadioStatus(container, panel, data) {
  const snap = data?.snapshot;

  // If the binding store isn't wired for "state" yet, snap will be null.
  if (!snap || typeof snap !== "object") {
    container.innerHTML = `
      <div class="rt-panel">
        <div class="rt-title">HF Status</div>
        <div class="rt-muted">No HF snapshot yet.</div>
        <div class="rt-muted" style="margin-top:6px;">
          Waiting on <span class="rt-mono">state:${esc(panel?.bindings?.[0]?.key || "rt:hf:snapshot")}</span>
        </div>
      </div>
    `;
    return;
  }

  const freqHz = Number(snap.freq_hz ?? snap.frequency_hz ?? NaN);
  const freqMHz = Number.isFinite(freqHz) ? (freqHz / 1e6).toFixed(3) : "-";
  const mode = snap.mode ?? "-";
  const ptt = !!(snap.ptt ?? snap.is_ptt ?? false);
  const tx  = !!(snap.tx ?? snap.is_tx ?? false);

  const updatedMs = Number(snap.updated_ms ?? snap.ts_ms ?? NaN);
  const ageSec = Number.isFinite(updatedMs) ? Math.max(0, Math.floor((Date.now() - updatedMs) / 1000)) : null;

  // Super simple “freshness” for now.
  const freshness =
    ageSec == null ? pill("warn", "Unknown age") :
    ageSec <= 2 ? pill("ok", `Fresh (${ageSec}s)`) :
    ageSec <= 10 ? pill("warn", `Stale (${ageSec}s)`) :
    pill("bad", `Old (${ageSec}s)`);

  const pttPill = ptt ? pill("warn", "PTT") : pill("ok", "RX");
  const txPill  = tx  ? pill("bad", "TX")  : pill("ok", "Idle");

  container.innerHTML = `
    <div class="rt-panel">
      <div class="rt-title">HF Status</div>

      <div style="display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 10px;">
        ${freshness}
        ${pttPill}
        ${txPill}
      </div>

      <div class="rt-kv">
        <div class="rt-muted">Freq</div><div><span class="rt-mono">${esc(freqMHz)}</span> MHz</div>
        <div class="rt-muted">Mode</div><div>${esc(mode)}</div>
      </div>
    </div>
  `;
}
