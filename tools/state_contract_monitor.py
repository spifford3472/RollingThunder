# RUN THIS LIKE SO:
#   python tools/state_contract_monitor.py --writer=heartbeat
# Requires redis-cli in PATH.


import argparse
import json
import re
import subprocess
from pathlib import Path

from tests.support.redis_write_guard import OwnershipRegistry, OwnershipRule


HSET_RE = re.compile(r'.*\bHSET\b\s+(\S+)\s+(.*)$')


def load_registry(path: str) -> OwnershipRegistry:
    raw = json.loads(Path(path).read_text())
    rules = []
    for k in raw["keys"]:
        rules.append(OwnershipRule(pattern=k["pattern"], fields=k["fields"]))
    return OwnershipRegistry(rules)


def parse_fields(rest: str):
    # rest is "field1 value1 field2 value2 ..."
    parts = rest.split()
    fields = parts[0::2]
    return fields


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="config/state_ownership.json")
    ap.add_argument("--writer", required=True, help="writer id, e.g. heartbeat|node_presence_ingestor|state_publisher")
    ap.add_argument("--redis-cli", default="redis-cli")
    args = ap.parse_args()

    reg = load_registry(args.registry)

    proc = subprocess.Popen([args.redis_cli, "monitor"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    print(f"[monitor] watching Redis writes as writer='{args.writer}' (CTRL+C to stop)")
    for line in proc.stdout:
        m = HSET_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        fields = parse_fields(m.group(2))
        for f in fields:
            allowed = reg.allowed_writers(key, f)
            if allowed is None:
                print(f"[VIOLATION] unknown key='{key}' field='{f}'  line={line.strip()}")
            elif args.writer not in allowed:
                print(f"[VIOLATION] writer='{args.writer}' wrote key='{key}' field='{f}' allowed={allowed}  line={line.strip()}")


if __name__ == "__main__":
    main()
