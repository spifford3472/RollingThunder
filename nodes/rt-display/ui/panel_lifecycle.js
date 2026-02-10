// panel_lifecycle.js

function isObj(v) { return v && typeof v === "object"; }

function hasRequiredLocator(binding) {
  const src = String(binding?.source || "").toLowerCase();
  if (src === "state") return !!String(binding?.key || "").trim();
  if (src === "api") return !!String(binding?.url || "").trim();
  if (src === "bus") return !!String(binding?.topic || "").trim();
  return false;
}

export function classifyPanelLifecycle(panel, data) {
  const issues = [];
  const results = isObj(data?.__rt?.bindings) ? data.__rt.bindings : null;

  // Check config-level binding sanity (misconfig)
  const bindings = Array.isArray(panel?.bindings) ? panel.bindings : null; // runtime usually passes coerced list, but keep conservative
  // We'll also infer from __rt.bindings if available.
  const bindingIds = results ? Object.keys(results) : [];

  // If we have results, validate their meta
  if (results) {
    for (const id of bindingIds) {
      const r = results[id];
      if (!r?.meta?.source) issues.push(`binding:${id}:missing_source`);
      if (r?.meta?.source && !r?.meta?.locator) issues.push(`binding:${id}:missing_locator`);
      if (r?.ok === false && r?.err) issues.push(`binding:${id}:error`);
    }
  }

  // If any binding is missing locator in config, call it CONFIG
  // We can’t reliably see original binding specs here unless refresh passes them; refresh will add these issues too.
  const hasConfigIssue = issues.some(s => s.includes("missing_locator") || s.includes("missing_source"));

  // Determine state
  const hasError = results && bindingIds.some(id => results[id]?.ok === false);
  const anyOkValue = results && bindingIds.some(id => results[id]?.ok && results[id]?.value != null);

  let state = "ok";
  if (hasConfigIssue) state = "config";
  else if (hasError) state = "error";
  else if (!anyOkValue) state = "empty";

  return { state, issues };
}
