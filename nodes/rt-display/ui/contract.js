// contract.js
function isObj(v) { return v && typeof v === "object"; }

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
  if (!loc) return { ok: false, code: s === "state" ? "missing_key" : s === "api" ? "missing_url" : "missing_topic" };

  return { ok: true };
}

/**
 * Contract rules:
 * - requiredBindings: array of binding ids that must not error and should be "present"
 * - allowNull: optional set/array of binding ids that may be null (still OK)
 * - presence: how to interpret "present" for required bindings
 */
export function classifyPanelFromResults(panel, data) {
  const results = data?.__rt?.bindings || {};
  const errors = [];
  const configIssues = [];

  // Validate binding specs (from panel config)
  const bindings = Array.isArray(panel?.bindings) ? panel.bindings : [];
  for (const b of bindings) {
    if (!b?.id) { configIssues.push("binding:missing_id"); continue; }
    const v = validateBindingSpec(b);
    if (!v.ok) configIssues.push(`binding:${b.id}:${v.code}`);
  }

  if (configIssues.length) {
    return { state: "config", issues: configIssues };
  }

  // Contract defaults (safe + backwards compatible)
  const contract = isObj(panel?.meta?.contract) ? panel.meta.contract : {};
  const required = Array.isArray(contract.requiredBindings) ? contract.requiredBindings : bindings.map(b => b.id); // default: all listed
  const allowNullArr = Array.isArray(contract.allowNull) ? contract.allowNull : [];
  const allowNull = new Set(allowNullArr);

  // Determine required binding outcomes
  let anyRequiredPresent = false;

  for (const id of required) {
    const r = results?.[id];
    if (!r) {
      // binding not even attempted (shouldn’t happen with current refresh, but keep honest)
      errors.push(`binding:${id}:missing_result`);
      continue;
    }
    if (r.ok === false) {
      errors.push(`binding:${id}:error`);
      continue;
    }

    // ok === true here; decide presence
    const v = r.value;
    if (v == null) {
      if (allowNull.has(id)) {
        // allowed; doesn't count as "present"
      } else {
        // required but null => empty (not error)
      }
    } else {
      anyRequiredPresent = true;
    }
  }

  if (errors.length) {
    return { state: "error", issues: errors };
  }

  if (!anyRequiredPresent) {
    return { state: "empty", issues: [] };
  }

  return { state: "ok", issues: [] };
}
