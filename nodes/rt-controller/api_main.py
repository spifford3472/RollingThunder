# nodes/rt-controller/api_main.py
from __future__ import annotations

import argparse
from pathlib import Path

from config_loader import load_and_resolve_app_config
from api_server import create_app


def _default_app_json_path() -> Path:
    here = Path(__file__).resolve().parent
    return (here / ".." / ".." / "config" / "app.json").resolve()


def main() -> int:
    p = argparse.ArgumentParser(description="RollingThunder API Server (read-only)")
    p.add_argument("--config", type=Path, default=_default_app_json_path())
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()

    cfg, _ = load_and_resolve_app_config(args.config)

    app = create_app(cfg)
    app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
