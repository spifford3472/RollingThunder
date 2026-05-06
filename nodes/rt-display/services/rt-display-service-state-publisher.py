#!/usr/bin/env python3
"""
Shim to keep RollingThunder service filenames unique across nodes.

Real implementation lives in:
/opt/rollingthunder/tools/service_state_publisher.py
"""
from pathlib import Path
import runpy
import sys

TARGET = Path("/opt/rollingthunder/tools/service_state_publisher.py")

if not TARGET.exists():
    raise SystemExit(f"missing {TARGET}")

sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")