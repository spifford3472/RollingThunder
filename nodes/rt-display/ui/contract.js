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
  return "";
}

export function validateBindingSpec(binding) {
  const s = src(binding);
  if (!s) return { ok: false, code: "missing_source" };
  if (s !== "state" && s !== "api" && s !== "bus") return { ok: false, code: `unknown_source:${s}` };

  const loc = locator(binding);
  if (!loc) return {
    ok: false,
    code: s === "state" ? "missing_key" : s === "api" ? "missing_url" : "missing_topic",
  };

  return { ok: true };
}

/**
 * classifyPanelFromResults(panel, data)
 *
 * Produces one of:
 *   ok | empty | error | config
 *
 * Rules:
 * - config: any binding spec invalid (missing key/url, unknown source, missing id)
 * - error: any REQUIRED binding returned ok=false
 * - ok: at least one REQUIRED binding has a non-null value, and no required errors
 * - empty: no required errors, but also no required binding has a non-null value
 *
 * Contract is declared in panel.meta.contract:
 * {
 *   requiredBindings: ["id1","id2"],   // defaults to all bindings listed
 *   allowNull: ["idX"]                // ids allowed to be null without counting against "ok"
 * }
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

  // Evaluate required results
  const issues = [];
  let anyRequiredPresent = false;

  for (const id of required) {
    const r = results?.[id];

    if (!r) {
      issues.push(`binding:${id}:missing_result`);
      continue;
    }

    if (r.ok === false) {
      issues.push(`binding:${id}:error`);
      continue;
    }

    // ok === true
    const v = r.value;
    if (v == null) {
      // null is allowed for some bindings; it just doesn't count as "present"
      // For non-allowed required bindings, this contributes to EMPTY but not ERROR.
      // (No issue recorded; keep it calm.)
      continue;
    }

    // non-null
    if (!allowNull.has(id)) {
      anyRequiredPresent = true;
    } else {
      // allowNull bindings are allowed to be null, but if they *do* have data,
      // we still shouldn't use them to consider the panel OK unless you want that.
      // Keep strict: allowNull does not contribute to OK.
    }
  }

  if (issues.length) {
    return { state: "error", issues };
  }

  if (!anyRequiredPresent) {
    return { state: "empty", issues: [] };
  }

  return { state: "ok", issues: [] };
}
