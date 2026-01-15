# nodes/rt-controller/rt_controller.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config_loader import load_and_resolve_app_config, ConfigError


def _default_app_json_path() -> Path:
    """
    Assumes repo layout:
      repo/
        config/app.json
        nodes/rt-controller/rt_controller.py  (this file)

    Default: ../../config/app.json from this script’s directory.
    """
    here = Path(__file__).resolve().parent
    return (here / ".." / ".." / "config" / "app.json").resolve()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RollingThunder rt-controller bootstrap (Phase 2)")
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_app_json_path(),
        help="Path to config/app.json",
    )
    # in argparse:
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print resolved config after the summary (human-readable)",
    )
    parser.add_argument(
        "--print-config-json",
        action="store_true",
        help="Print resolved config JSON only (machine-readable; no banner/summary)",
    )

    args = parser.parse_args(argv)

    try:
        cfg, includes = load_and_resolve_app_config(args.config)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # after cfg is loaded:
    if args.print_config_json:
        print(json.dumps(cfg, indent=2, sort_keys=False))
        return 0


    print("RollingThunder Controller Bootstrap")
    print("----------------------------------")
    
    pages = cfg.get("pages") if isinstance(cfg.get("pages"), list) else []
    panels = cfg.get("panels") if isinstance(cfg.get("panels"), list) else []
    services = cfg.get("services")
    services_count = len(services) if isinstance(services, dict) else 0

    print(f"Loaded: {args.config}")
    if includes.pages_files:
        print(f"Pages:  {len(pages)} (from {len(includes.pages_files)} files)")
    else:
        print(f"Pages:  {len(pages)}")

    if includes.panels_files:
        print(f"Panels: {len(panels)} (from {len(includes.panels_files)} files)")
    else:
        print(f"Panels: {len(panels)}")

    print(f"Services: {services_count}")
    print("Validation: NOT RUN")
    print("Redis: NOT CONNECTED")
    print("MQTT: NOT CONNECTED")

    if args.print_config:
        print("\n--- RESOLVED CONFIG (JSON) ---")
        print(json.dumps(cfg, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
