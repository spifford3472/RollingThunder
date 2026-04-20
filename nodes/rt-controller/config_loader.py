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
        full_pat = (base_dir / pat).as_posix()
        for m in glob.glob(full_pat):
            matches.append(Path(m).resolve())

    matches = sorted(set(matches), key=lambda p: p.as_posix())
    return matches


def _resolve_manifest(manifest_path: Path, kind: str) -> List[Path]:
    """
    Resolve a manifest file of the form {"files": ["home.json", "hf.json", ...]}
    into a list of absolute Paths, relative to the manifest's own directory.
    """
    obj = _load_json_file(manifest_path)
    if not isinstance(obj, dict):
        raise ConfigError(f"{kind} manifest must be a JSON object: {manifest_path}")
    files = obj.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ConfigError(
            f"{kind} manifest 'files' must be a list of strings: {manifest_path}"
        )
    manifest_dir = manifest_path.parent
    resolved = []
    for f in files:
        p = (manifest_dir / f).resolve()
        if not p.exists():
            raise ConfigError(
                f"{kind} manifest references missing file '{f}': {p}"
            )
        resolved.append(p)
    return resolved


def _is_manifest(path: Path) -> bool:
    """
    A file is treated as a manifest if it is a JSON object containing a 'files' list.
    Does not raise — returns False on any parse/read error.
    """
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(obj, dict) and isinstance(obj.get("files"), list)
    except Exception:
        return False


def _expand_include_patterns(base_dir: Path, inc: List[str], kind: str) -> List[Path]:
    """
    Expand include patterns, transparently resolving manifest files.

    For each pattern:
      - If it resolves to a single existing .json file that is a manifest
        (i.e. {"files": [...]}) → expand the manifest's file list.
      - Otherwise → treat as a glob pattern and expand normally.

    Returns a deduplicated, sorted list of resolved Paths.
    """
    all_files: List[Path] = []

    for pat in inc:
        candidate = (base_dir / pat).resolve()

        # Single existing file — check if it's a manifest
        if candidate.exists() and candidate.is_file() and _is_manifest(candidate):
            all_files.extend(_resolve_manifest(candidate, kind))
        else:
            # Normal glob expansion
            expanded = _expand_globs(base_dir, [pat])
            all_files.extend(expanded)

    # Deduplicate, preserve deterministic order
    seen: set = set()
    result: List[Path] = []
    for p in all_files:
        if p not in seen:
            seen.add(p)
            result.append(p)

    return result


def _resolve_include_block(
    base_dir: Path, block: Any, kind: str
) -> Tuple[List[Dict[str, Any]], List[Path], Dict[str, Path]]:
    """
    block is expected to be an object like:
      { "include": ["pages.manifest.json"] }
      { "include": ["pages/*.json"] }

    Manifest files ({"files": [...]}) are transparently expanded.

    Returns:
      (list_of_objects, list_of_files_loaded, id_to_file_map)

    Rules enforced:
    - include must match at least one file
    - each file must contain exactly one JSON object
    - object must contain non-empty string 'id'
    - duplicate IDs are a hard error
    """
    if not isinstance(block, dict):
        raise ConfigError(f"'{kind}' must be an object; got {type(block).__name__}")

    inc = block.get("include")
    if inc is None:
        return [], [], {}

    if not isinstance(inc, list) or not all(isinstance(x, str) for x in inc):
        raise ConfigError(f"'{kind}.include' must be a list of strings")

    files = _expand_include_patterns(base_dir, inc, kind)
    if not files:
        raise ConfigError(f"No files matched {kind}.include patterns: {inc}")

    objects: List[Dict[str, Any]] = []
    id_to_file: Dict[str, Path] = {}

    for fp in files:
        obj = _load_json_file(fp)
        if not isinstance(obj, dict):
            raise ConfigError(
                f"{kind} include file must contain a single JSON object: {fp}"
            )

        obj_id = obj.get("id")
        if not isinstance(obj_id, str) or not obj_id.strip():
            raise ConfigError(
                f"{kind} include file missing required non-empty string 'id': {fp}"
            )

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

    Supports both manifest indirection and direct glob patterns transparently:
      "include": ["pages.manifest.json"]   <- manifest file
      "include": ["pages/*.json"]           <- direct glob

    Returns:
      (resolved_config_dict, ResolvedIncludes)
    """
    app_json_path = app_json_path.expanduser().resolve()
    # base_dir is the directory containing app.json (i.e. config/)
    # include patterns are relative to this directory
    base_dir = app_json_path.parent

    raw = _load_json_file(app_json_path)
    if not isinstance(raw, dict):
        raise ConfigError(
            f"app.json must be a JSON object at top-level: {app_json_path}"
        )

    resolved = dict(raw)  # shallow copy

    pages_objs: List[Dict[str, Any]] = []
    pages_files: List[Path] = []
    page_id_to_file: Dict[str, Path] = {}

    panels_objs: List[Dict[str, Any]] = []
    panels_files: List[Path] = []
    panel_id_to_file: Dict[str, Path] = {}

    if "pages" in resolved:
        pages_objs, pages_files, page_id_to_file = _resolve_include_block(
            base_dir, resolved["pages"], "pages"
        )
        if pages_objs:
            resolved["pages"] = pages_objs

    if "panels" in resolved:
        panels_objs, panels_files, panel_id_to_file = _resolve_include_block(
            base_dir, resolved["panels"], "panels"
        )
        if panels_objs:
            resolved["panels"] = panels_objs

    includes = ResolvedIncludes(
        pages_files=pages_files,
        panels_files=panels_files,
        page_id_to_file=page_id_to_file,
        panel_id_to_file=panel_id_to_file,
    )

    return resolved, includes