# nodes/rt-controller/config_validator.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SERVICE_ID_RE = re.compile(r"^[a-z0-9_]+$")


class ValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: List[str]
    warnings: List[str]


def _is_nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_positive_int(x: Any) -> bool:
    return isinstance(x, int) and x > 0


def _load_intents(intents_md_path: Path) -> Set[str]:
    """
    Extract intent IDs from docs/INTENTS.md.

    Supports:
    - dot style: ui.page.next, alert.ack, radio.hf.query
    - underscore style: UI_PAGE_NEXT
    """
    text = intents_md_path.read_text(encoding="utf-8")

    dot_style = set(re.findall(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b", text))
    underscore_style = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text))

    return dot_style | underscore_style


def _detect_service_cycles(dep_map: Dict[str, Set[str]]) -> List[List[str]]:
    """
    Deterministic DFS cycle detection.
    Returns a list of cycles as paths (a -> b -> c -> a).
    """
    cycles: List[List[str]] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def dfs(node: str, stack: List[str]) -> None:
        if node in visiting:
            if node in stack:
                i = stack.index(node)
                cycles.append(stack[i:] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for nxt in sorted(dep_map.get(node, set())):
            dfs(nxt, stack)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for n in sorted(dep_map.keys()):
        dfs(n, [])
    return cycles


def validate_config(
    cfg: Dict[str, Any],
    *,
    intents_md_path: Path,
    include_maps: Optional[Dict[str, Dict[str, Path]]] = None,
) -> ValidationReport:
    errors: List[str] = []
    warnings: List[str] = []

    # --- Load INTENTS ---
    try:
        intents = _load_intents(intents_md_path)
    except Exception as e:
        errors.append(f"Unable to read intents from {intents_md_path}: {e}")
        intents = set()

    # --- 3.1 Schema Block ---
    schema = cfg.get("schema")
    if not isinstance(schema, dict):
        errors.append("schema must exist and be an object")
    else:
        if not _is_nonempty_str(schema.get("id")):
            errors.append("schema.id must exist and be non-empty")

        ver = schema.get("version")
        if not _is_nonempty_str(ver) or not SEMVER_RE.match(ver.strip()):
            errors.append("schema.version must exist and be semantic (MAJOR.MINOR.PATCH)")

        compat = schema.get("compat")
        if not isinstance(compat, dict) or compat.get("allowUnknownFields") is not True:
            errors.append("schema.compat.allowUnknownFields must be true")

    # --- 3.2 Globals ---
    globals_block = cfg.get("globals")
    if not isinstance(globals_block, dict):
        errors.append("globals must exist and be an object")
        globals_block = {}

    for req in ("time", "state", "bus", "api"):
        if req not in globals_block or not isinstance(globals_block.get(req), dict):
            errors.append(f"globals.{req} must exist and be an object")

    state_block = globals_block.get("state") if isinstance(globals_block.get("state"), dict) else {}
    if not _is_nonempty_str(state_block.get("namespace")):
        errors.append("globals.state.namespace must be a non-empty string")

    # "Global values must be primitives or objects (no arrays at top level)"
    for k, v in globals_block.items():
        if isinstance(v, list):
            errors.append(f"globals.{k} must not be an array")

    # --- 3.3 Services Catalog ---
    services = cfg.get("services")
    if not isinstance(services, dict):
        errors.append("services must exist and be an object (map of service_id -> service config)")
        services = {}

    valid_scopes = {"always_on", "page_scoped"}
    valid_owners = {"rt-controller", "rt-radio", "rt-display", "external"}

    dep_map: Dict[str, Set[str]] = {}
    service_ids: Set[str] = set(services.keys())

    for sid, sobj in services.items():
        if not _is_nonempty_str(sid):
            errors.append("services contains an empty/non-string service id key")
            continue
        if not SERVICE_ID_RE.match(sid):
            errors.append(f"Service id '{sid}' must be lowercase and underscore-separated")

        if not isinstance(sobj, dict):
            errors.append(f"services['{sid}'] must be an object")
            continue

        if sobj.get("id") != sid:
            errors.append(f"services['{sid}'].id must exist and match the map key")

        scope = sobj.get("scope")
        if scope not in valid_scopes:
            errors.append(f"services['{sid}'].scope must be one of {sorted(valid_scopes)}")

        owner = sobj.get("ownerNode")
        if owner not in valid_owners:
            errors.append(f"services['{sid}'].ownerNode must be one of {sorted(valid_owners)}")

        lifecycle = sobj.get("lifecycle")
        if not isinstance(lifecycle, dict):
            errors.append(f"services['{sid}'].lifecycle must be an object")
        else:
            if "startPolicy" not in lifecycle:
                errors.append(f"services['{sid}'].lifecycle.startPolicy must exist")
            if "stopPolicy" not in lifecycle:
                errors.append(f"services['{sid}'].lifecycle.stopPolicy must exist")

        restart_policy = sobj.get("restartPolicy")
        if restart_policy is not None:
            if not isinstance(restart_policy, dict) or "mode" not in restart_policy:
                errors.append(f"services['{sid}'].restartPolicy.mode must be defined if restartPolicy exists")

        depends = sobj.get("dependsOn", [])
        if depends is None:
            depends = []
        if not isinstance(depends, list) or not all(_is_nonempty_str(x) for x in depends):
            errors.append(f"services['{sid}'].dependsOn must be a list of service IDs")
            depends = []

        missing = sorted(set(x.strip() for x in depends) - service_ids)
        if missing:
            errors.append(f"services['{sid}'] dependsOn unknown service(s): {', '.join(missing)}")
        dep_map[sid] = set(x.strip() for x in depends)

        health = sobj.get("health")
        if not isinstance(health, dict):
            errors.append(f"services['{sid}'].health must be an object")
        else:
            if "type" not in health:
                errors.append(f"services['{sid}'].health.type must be defined")
            if "target" not in health:
                errors.append(f"services['{sid}'].health.target must exist")

        stale = sobj.get("staleAfterMs")
        if stale is not None and not _is_positive_int(stale):
            errors.append(f"services['{sid}'].staleAfterMs must be a positive integer if present")

    cycles = _detect_service_cycles(dep_map)
    for cyc in cycles[:5]:
        errors.append("Circular service dependency detected: " + " -> ".join(cyc))

    # --- 4 / 5 / 6 Includes already resolved; validate lists present ---
    pages = cfg.get("pages")
    panels = cfg.get("panels")
    if not isinstance(pages, list):
        errors.append("pages must be a list (include-resolved)")
        pages = []
    if not isinstance(panels, list):
        errors.append("panels must be a list (include-resolved)")
        panels = []

    # Build ID maps + uniqueness (duplicates should already be caught, but enforce again)
    page_by_id: Dict[str, Dict[str, Any]] = {}
    panel_by_id: Dict[str, Dict[str, Any]] = {}

    def register(domain: str, obj: Any, idx: int, dest: Dict[str, Dict[str, Any]]) -> None:
        if not isinstance(obj, dict):
            errors.append(f"{domain}[{idx}] must be an object")
            return
        oid = obj.get("id")
        if not _is_nonempty_str(oid):
            errors.append(f"{domain}[{idx}].id must exist and be non-empty")
            return
        oid = oid.strip()
        if oid in dest:
            errors.append(f"Duplicate {domain} id: {oid}")
            return
        dest[oid] = obj

    for i, p in enumerate(pages):
        register("pages", p, i, page_by_id)
    for i, p in enumerate(panels):
        register("panels", p, i, panel_by_id)

    # Filename should match id (warning-level)
    if include_maps:
        for pid, fp in include_maps.get("pages", {}).items():
            if fp.stem != pid:
                warnings.append(f"Page file name should match id: id='{pid}' file='{fp.name}'")
        for pid, fp in include_maps.get("panels", {}).items():
            if fp.stem != pid:
                warnings.append(f"Panel file name should match id: id='{pid}' file='{fp.name}'")

    # --- 5. Panel Validation ---
    panels_referenced_by_pages: Set[str] = set()

    for pid, panel in panel_by_id.items():
        for req in ("id", "type", "focusable", "bindings"):
            if req not in panel:
                errors.append(f"panel[{pid}] missing required field '{req}'")

        # Invariant: Panels must not reference services
        if "services" in panel:
            errors.append(f"panel[{pid}] must not reference services (field 'services' is forbidden)")

        bindings = panel.get("bindings")
        if not isinstance(bindings, list):
            errors.append(f"panel[{pid}].bindings must be a list")
            bindings = []

        for bi, b in enumerate(bindings):
            if not isinstance(b, dict):
                errors.append(f"panel[{pid}].bindings[{bi}] must be an object")
                continue

            src = b.get("source")

            # Allow scan now (UI runtime supports it)
            if src not in {"state", "api", "bus", "scan"}:
                errors.append(f"panel[{pid}].bindings[{bi}].source must be one of state|api|bus|scan")
                continue

            if src == "state":
                if not _is_nonempty_str(b.get("key")):
                    errors.append(f"panel[{pid}].bindings[{bi}] state binding must define non-empty 'key'")

            elif src == "api":
                if not _is_nonempty_str(b.get("url")):
                    errors.append(f"panel[{pid}].bindings[{bi}] api binding must define non-empty 'url'")

            elif src == "bus":
                if not _is_nonempty_str(b.get("topic")):
                    errors.append(f"panel[{pid}].bindings[{bi}] bus binding must define non-empty 'topic'")

            elif src == "scan":
                # Required: match
                if not _is_nonempty_str(b.get("match")):
                    errors.append(f"panel[{pid}].bindings[{bi}] scan binding must define non-empty 'match'")

                # Optional: limit (if present) must be positive int
                lim = b.get("limit")
                if lim is not None and not _is_positive_int(lim):
                    errors.append(f"panel[{pid}].bindings[{bi}] scan binding 'limit' must be a positive integer if present")

                # Optional: filter (if present) must be an object
                flt = b.get("filter")
                if flt is not None and not isinstance(flt, dict):
                    errors.append(f"panel[{pid}].bindings[{bi}] scan binding 'filter' must be an object if present")

                # Minimal safety: only allow rt:* patterns (matches your key policy elsewhere)
                m = (b.get("match") or "").strip() if isinstance(b.get("match"), str) else ""
                if m and not m.startswith("rt:"):
                    errors.append(f"panel[{pid}].bindings[{bi}] scan binding 'match' must start with 'rt:'")


        actions = panel.get("actions", [])
        if actions is None:
            actions = []
        if not isinstance(actions, list):
            errors.append(f"panel[{pid}].actions must be a list if present")
            actions = []

        for ai, a in enumerate(actions):
            if not isinstance(a, dict):
                errors.append(f"panel[{pid}].actions[{ai}] must be an object")
                continue
            intent = a.get("intent")
            if not _is_nonempty_str(intent):
                errors.append(f"panel[{pid}].actions[{ai}].intent must exist and be non-empty")
            else:
                intent = intent.strip()
                if intent not in intents:
                    errors.append(f"panel[{pid}].actions[{ai}] references unknown intent '{intent}'")
            params = a.get("params")
            if params is not None and not isinstance(params, dict):
                errors.append(f"panel[{pid}].actions[{ai}].params must be an object if present")

        # Warning: static panel
        if (not actions) and isinstance(bindings, list) and len(bindings) == 0:
            warnings.append(f"panel[{pid}] has no actions and no bindings (static display)")

    # --- 6. Page Validation ---
    page_orders: Dict[int, str] = {}
    services_referenced_by_pages: Set[str] = set()

    for pgid, page in page_by_id.items():
        for req in ("id", "order", "title", "layout", "requires", "optional", "controls", "focusPolicy"):
            if req not in page:
                errors.append(f"page[{pgid}] missing required field '{req}'")

        order = page.get("order")
        if not isinstance(order, int):
            errors.append(f"page[{pgid}].order must be an integer")
        else:
            if order in page_orders:
                errors.append(f"Page order {order} duplicated by page[{page_orders[order]}] and page[{pgid}]")
            else:
                page_orders[order] = pgid

        layout = page.get("layout")
        if not isinstance(layout, dict):
            errors.append(f"page[{pgid}].layout must be an object")
            layout = {}

        top = layout.get("top")
        middle = layout.get("middle")
        bottom = layout.get("bottom")

        if not isinstance(top, list):
            errors.append(f"page[{pgid}].layout.top must be an array")
            top = []
        if not isinstance(bottom, list):
            errors.append(f"page[{pgid}].layout.bottom must be an array")
            bottom = []
        if not isinstance(middle, list):
            errors.append(f"page[{pgid}].layout.middle must be an array of arrays")
            middle = []

        if isinstance(middle, list):
            if not (1 <= len(middle) <= 3):
                errors.append(f"page[{pgid}].layout.middle must contain 1–3 columns (got {len(middle)})")
            for ci, col in enumerate(middle):
                if not isinstance(col, list):
                    errors.append(f"page[{pgid}].layout.middle[{ci}] must be an array")
                    continue
                seen: Set[str] = set()
                for p in col:
                    if not _is_nonempty_str(p):
                        errors.append(f"page[{pgid}].layout.middle[{ci}] panel IDs must be non-empty strings")
                        continue
                    p = p.strip()
                    if p in seen:
                        errors.append(f"page[{pgid}] panel '{p}' appears twice in layout column {ci}")
                    seen.add(p)

        def collect_panel_ids() -> List[str]:
            ids: List[str] = []
            for x in top:
                if _is_nonempty_str(x):
                    ids.append(x.strip())
            for x in bottom:
                if _is_nonempty_str(x):
                    ids.append(x.strip())
            if isinstance(middle, list):
                for col in middle:
                    if isinstance(col, list):
                        for x in col:
                            if _is_nonempty_str(x):
                                ids.append(x.strip())
            return ids

        page_panel_ids = collect_panel_ids()
        if len(page_panel_ids) == 0:
            warnings.append(f"page[{pgid}] has empty layout (no panels referenced)")
        for pid in page_panel_ids:
            panels_referenced_by_pages.add(pid)
            if pid not in panel_by_id:
                errors.append(f"page[{pgid}] references unknown panel '{pid}'")

        # Services required/optional
        reqs = page.get("requires")
        opts = page.get("optional")
        if not isinstance(reqs, list) or not all(_is_nonempty_str(x) for x in reqs):
            errors.append(f"page[{pgid}].requires must be a list of service IDs")
            reqs = []
        if not isinstance(opts, list) or not all(_is_nonempty_str(x) for x in opts):
            errors.append(f"page[{pgid}].optional must be a list of service IDs")
            opts = []

        reqs_set = {x.strip() for x in reqs}
        opts_set = {x.strip() for x in opts}

        both = sorted(reqs_set.intersection(opts_set))
        if both:
            errors.append(f"page[{pgid}] service(s) appear in both requires and optional: {', '.join(both)}")

        missing_reqs = sorted(reqs_set - service_ids)
        missing_opts = sorted(opts_set - service_ids)
        if missing_reqs:
            errors.append(f"page[{pgid}] requires unknown service(s): {', '.join(missing_reqs)}")
        if missing_opts:
            errors.append(f"page[{pgid}] optional unknown service(s): {', '.join(missing_opts)}")

        services_referenced_by_pages |= reqs_set
        services_referenced_by_pages |= opts_set

        # Controls.allowedIntents
        controls = page.get("controls")
        if not isinstance(controls, dict):
            errors.append(f"page[{pgid}].controls must be an object")
            controls = {}
        allowed = controls.get("allowedIntents")
        if not isinstance(allowed, list) or not all(_is_nonempty_str(x) for x in allowed):
            errors.append(f"page[{pgid}].controls.allowedIntents must be a list of intent IDs")
            allowed = []
        for it in allowed:
            it = it.strip()
            if it not in intents:
                errors.append(f"page[{pgid}] references unknown intent '{it}' in controls.allowedIntents")

        # Focus policy
        fp = page.get("focusPolicy")
        if not isinstance(fp, dict):
            errors.append(f"page[{pgid}].focusPolicy must be an object")
            fp = {}

        dp = fp.get("defaultPanel")
        if not _is_nonempty_str(dp):
            errors.append(f"page[{pgid}].focusPolicy.defaultPanel must exist and be non-empty")
        else:
            dp = dp.strip()
            if dp not in page_panel_ids:
                errors.append(f"page[{pgid}] focusPolicy.defaultPanel '{dp}' must exist in the page layout")

        rot = fp.get("rotation", [])
        if rot is None:
            rot = []
        if not isinstance(rot, list) or not all(_is_nonempty_str(x) for x in rot):
            errors.append(f"page[{pgid}].focusPolicy.rotation must be a list of panel IDs if present")
            rot = []
        for r in rot:
            r = r.strip()
            if r not in page_panel_ids:
                errors.append(f"page[{pgid}] focusPolicy.rotation references panel '{r}' not in page layout")

        # Warning: empty middle layout
        if isinstance(middle, list) and all(isinstance(col, list) and len(col) == 0 for col in middle):
            warnings.append(f"page[{pgid}] has empty middle layout")

    # --- 8 Warning-Level conditions ---
    unused_panels = sorted(set(panel_by_id.keys()) - panels_referenced_by_pages)
    for pid in unused_panels:
        warnings.append(f"panel[{pid}] is never referenced by any page")

    unused_services = sorted(service_ids - services_referenced_by_pages)
    for sid in unused_services:
        warnings.append(f"service '{sid}' is never required/optional on any page")

    return ValidationReport(ok=(len(errors) == 0), errors=errors, warnings=warnings)


def validate_or_raise(
    cfg: Dict[str, Any],
    *,
    intents_md_path: Path,
    include_maps: Optional[Dict[str, Dict[str, Path]]] = None,
) -> ValidationReport:
    rep = validate_config(cfg, intents_md_path=intents_md_path, include_maps=include_maps)
    if not rep.ok:
        msg = "CONFIG VALIDATION FAILED\n------------------------\n" + "\n".join(f"- {e}" for e in rep.errors)
        raise ValidationError(msg)
    return rep
