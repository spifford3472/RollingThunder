// contract.js
function isObj(v) { return v && typeof v === "object"; }

function coerceBindings(panel) {
  const b = panel?.bindings;
  if (Array.isArray(b)) return b;
  if (b && typeof b === "object") {
    return Object.entries(b).map(([id, spec]) => ({ id, ...(spec || {}) }));
  }
  return [];
}

function src(binding) {
  return String(binding?.source || "").toLowerCase().trim();
}

function locator(binding) {
  const s = src(binding);
  if (s === "state") return String(binding?.key || "").trim();
  if (s === "api") return String(binding?.url || "").trim();
  if (s === "bus") return String(binding?.topic || "").trim();

  // NEW: scan bindings identify by their match/prefix
  if (s === "scan") return String(binding?.match || binding?.prefix || "").trim();

  return "";
}

export function validateBindingSpec(binding) {
  const s = src(binding);
  if (!s) return { ok: false, code: "missing_source" };

  // NEW: allow scan
  if (s !== "state" && s !== "api" && s !== "bus" && s !== "scan") {
    return { ok: false, code: `unknown_source:${s}` };
  }

  const loc = locator(binding);
  if (!loc) {
    return {
      ok: false,
      code:
        s === "state" ? "missing_key" :
        s === "api" ? "missing_url" :
        s === "bus" ? "missing_topic" :
        "missing_match", // scan
    };
  }

  return { ok: true };
}

/**
 * classifyPanelFromResults(panel, bindings, data)
 *
 * Produces one of: ok | empty | error | config
 */
export function classifyPanelFromResults(panel, bindings, data) {
  const results = data?.__rt?.bindings || {};

  const list = Array.isArray(bindings) ? bindings : [];
  const configIssues = [];

  for (const b of list) {
    if (!b?.id) { configIssues.push("binding:missing_id"); continue; }
    const v = validateBindingSpec(b);
    if (!v.ok) configIssues.push(`binding:${b.id}:${v.code}`);
  }

  if (configIssues.length) return { state: "config", issues: configIssues };

  const contract = isObj(panel?.meta?.contract) ? panel.meta.contract : {};
  const required = Array.isArray(contract.requiredBindings)
    ? contract.requiredBindings
    : list.map(b => b.id);

  const allowNull = new Set(Array.isArray(contract.allowNull) ? contract.allowNull : []);

  const issues = [];
  let anyRequiredPresent = false;

  for (const id of required) {
    const r = results?.[id];

    if (!r) { issues.push(`binding:${id}:missing_result`); continue; }
    if (r.ok === false) { issues.push(`binding:${id}:error`); continue; }

    const v = r.value;
    if (v == null) continue;

    if (!allowNull.has(id)) anyRequiredPresent = true;
  }

  if (issues.length) return { state: "error", issues };
  if (!anyRequiredPresent) return { state: "empty", issues: [] };
  return { state: "ok", issues: [] };
}
