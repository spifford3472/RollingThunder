#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
app_json="${repo_root}/config/app.json"

python3 - <<'PY' "$app_json"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

rv = data.setdefault("runtimeVersion", {})
rv["major"] = int(rv.get("major", 0))
rv["minor"] = int(rv.get("minor", 0))
rv["build"] = int(rv.get("build", 0)) + 1

with path.open("w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print(f"Bumped runtimeVersion to {rv['major']}.{rv['minor']}.{rv['build']}")
PY