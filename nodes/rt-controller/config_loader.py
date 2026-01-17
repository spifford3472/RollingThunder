# nodes/rt-controller/config_loader.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
import glob


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedIncludes:
    pages_files: List[Path]
    panels_files: List[Path]
    page_id_to_file: Dict[str, Path]
    panel_id_to_file: Dict[str, Path]

def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}")


def _expand_globs(base_dir: Path, patterns: List[str]) -> List[Path]:
    """
    Expand glob patterns relative to base_dir.
    Deterministic ordering: lexical sort of fully-resolved paths.
    """
    matches: List[Path] = []
    for pat in patterns:
        # Resolve pattern relative to base_dir
        full_pat = (base_dir / pat).as_posix()
        for m in glob.glob(full_pat):
            matches.append(Path(m).resolve())

    # Deterministic order
    matches = sorted(set(matches), key=lambda p: p.as_posix())
    return matches


def _resolve_include_block(
    base_dir: Path, block: Any, kind: str
) -> Tuple[List[Dict[str, Any]], List[Path], Dict[str, Path]]:
    """
    block is expected to be an object like: { "include": ["config/pages/*.json"] }

    Returns:
      (list_of_objects, list_of_files_loaded, id_to_file_map)

    Rules enforced here:
    - include must match at least one file
    - each file must contain exactly one JSON object (loader enforces object)
    - object must contain non-empty string 'id'
    - duplicate IDs are a hard error
    """
    if not isinstance(block, dict):
        raise ConfigError(f"'{kind}' must be an object; got {type(block).__name__}")

    inc = block.get("include")
    if inc is None:
        # Not include-based; allow empty list here to keep Phase 2 narrow.
        return [], [], {}

    if not isinstance(inc, list) or not all(isinstance(x, str) for x in inc):
        raise ConfigError(f"'{kind}.include' must be a list of strings")

    files = _expand_globs(base_dir, inc)
    if not files:
        raise ConfigError(f"No files matched {kind}.include patterns: {inc}")

    objects: List[Dict[str, Any]] = []
    id_to_file: Dict[str, Path] = {}

    for fp in files:
        obj = _load_json_file(fp)
        if not isinstance(obj, dict):
            raise ConfigError(f"{kind} include file must contain a single JSON object: {fp}")

        obj_id = obj.get("id")
        if not isinstance(obj_id, str) or not obj_id.strip():
            raise ConfigError(f"{kind} include file missing required non-empty string 'id': {fp}")

        obj_id = obj_id.strip()
        if obj_id in id_to_file:
            prev = id_to_file[obj_id]
            raise ConfigError(
                f"Duplicate {kind} id '{obj_id}' in include files: {prev} and {fp}"
            )

        id_to_file[obj_id] = fp
        objects.append(obj)

    return objects, files, id_to_file


def load_and_resolve_app_config(app_json_path: Path) -> Tuple[Dict[str, Any], ResolvedIncludes]:
    """
    Loads config/app.json and resolves include-based pages/panels into inline lists.

    Returns:
      (resolved_config_dict, ResolvedIncludes)
    """
    app_json_path = app_json_path.expanduser().resolve()
    # app.json lives at <repo_root>/config/app.json
    # include patterns in app.json are repo-root-relative (e.g. "config/pages/*.json")
    repo_root = app_json_path.parent.parent
    base_dir = repo_root


    raw = _load_json_file(app_json_path)
    if not isinstance(raw, dict):
        raise ConfigError(f"app.json must be a JSON object at top-level: {app_json_path}")

    resolved = dict(raw)  # shallow copy

    pages_objs, pages_files = [], []
    panels_objs, panels_files = [], []

    if "pages" in resolved:
        pages_objs, pages_files, page_id_to_file = _resolve_include_block(base_dir, resolved["pages"], "pages")
        if pages_objs:
            resolved["pages"] = pages_objs

    if "panels" in resolved:
        panels_objs, panels_files, panel_id_to_file = _resolve_include_block(base_dir, resolved["panels"], "panels")
        if panels_objs:
            resolved["panels"] = panels_objs

    includes = ResolvedIncludes(
        pages_files=pages_files,
        panels_files=panels_files,
        page_id_to_file=page_id_to_file,
        panel_id_to_file=panel_id_to_file,
    )
    
    return resolved, includes
