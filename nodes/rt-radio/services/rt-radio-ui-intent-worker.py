#!/usr/bin/env python3
"""
Shim to keep rt-controller ExecStart invariants:
- rt-controller units must ExecStart /opt/rollingthunder/services/*.py
Real implementation lives in /opt/rollingthunder/tools/ui_intent_worker.py
"""
from pathlib import Path
import runpy
import sys

TARGET = Path("/opt/rollingthunder/tools/ui_intent_worker.py")

if not TARGET.exists():
    raise SystemExit(f"missing {TARGET}")

# Execute as __main__ so argparse/env handling behaves normally
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")