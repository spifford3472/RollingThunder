#!/usr/bin/env python3
"""
deploy_reporter.py — RollingThunder (rt-controller)

Publishes a bounded "what is actually deployed and running" report into Redis:
  rt:deploy:report:<node_id>

- Intended to be run periodically via systemd timer (oneshot).
- Controller writes its own report directly (no MQTT hop required).

Report includes:
- deployed_commit (from /opt/rollingthunder/.deploy/DEPLOYED_COMMIT)
- git_head + dirty (if /opt/rollingthunder is a git repo)
- sha256 hashes of:
  - controller-relevant systemd unit files under /etc/systemd/system/
  - root-owned executables under /opt/rollingthunder/services/*.py
- runtime state for those units (active state + MainPID)

All output is bounded and safe for UI consumption.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import socket
import subprocess
import time
from typing import Any, Dict, Optional

import redis


# -----------------------------
# Env config
# -----------------------------
NODE_ID = os.environ.get("RT_NODE_ID") or socket.gethostname()
ROLE = os.environ.get("RT_NODE_ROLE", "controller")

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

DEPLOY_KEY_PREFIX = os.environ.get("RT_KEY_DEPLOY_REPORT_PREFIX", "rt:deploy:report:")
DEPLOY_TTL_SEC = int(float(os.environ.get("RT_DEPLOY_TTL_SEC", "300")))
DEPLOYED_COMMIT_FILE = os.environ.get("RT_DEPLOYED_COMMIT_FILE", "/opt/rollingthunder/.deploy/DEPLOYED_COMMIT")

# Unit set: defaults to controller's always-on units; can be overridden by env CSV.
DEFAULT_UNITS = [
    "rollingthunder-api.service",
    "rollingthunder-controller.service",
    "rt-node-presence-ingestor.service",
    "rt-service-state-publisher.service",
    "rt-ui-snapshot-api.service",
]
UNITS_CSV = os.environ.get("RT_DEPLOY_UNITS_CSV", "")
UNITS = [u.strip() for u in UNITS_CSV.split(",") if u.strip()] if UNITS_CSV.strip() else DEFAULT_UNITS

SYSTEMD_DIR = os.environ.get("RT_SYSTEMD_DIR", "/etc/systemd/system")
SERVICES_GLOB = os.environ.get("RT_ROOT_SERVICES_GLOB", "/opt/rollingthunder/services/*.py")

MAX_ITEMS = int(os.environ.get("RT_DEPLOY_MAX_ITEMS", "200"))


# -----------------------------
# Helpers
# -----------------------------
def now_ms() -> int:
    return int(time.time() * 1000)


def read_text_file(path: str, max_chars: int = 2000) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read(max_chars + 1)
        s = s.strip()
        return s if s else None
    except Exception:
        return None


def sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except Exception:
        return None


def run_cmd(args: list[str], cwd: Optional[str] = None, timeout_sec: float = 1.5) -> Optional[str]:
    try:
        p = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec,
            check=False,
            text=True,
        )
        out = (p.stdout or "").strip()
        return out if out else None
    except Exception:
        return None


def git_head_and_dirty(repo_dir: str) -> tuple[Optional[str], Optional[bool]]:
    # If not a git repo, return (None, None)
    is_repo = run_cmd(["git", "-C", repo_dir, "rev-parse", "--is-inside-work-tree"])
    if is_repo != "true":
        return None, None

    head = run_cmd(["git", "-C", repo_dir, "rev-parse", "HEAD"], timeout_sec=2.0)
    dirty_out = run_cmd(["git", "-C", repo_dir, "status", "--porcelain"], timeout_sec=2.0)
    dirty = None
    if dirty_out is not None:
        dirty = True if dirty_out.strip() else False

    return head, dirty


def systemd_unit_runtime(unit: str) -> Dict[str, Any]:
    # Bounded runtime data: active + pid only
    active = run_cmd(["systemctl", "is-active", unit], timeout_sec=1.5) or "unknown"
    pid_s = run_cmd(["systemctl", "show", unit, "-p", "MainPID", "--value"], timeout_sec=1.5) or ""
    pid = None
    try:
        pid_i = int(pid_s.strip()) if pid_s.strip() else 0
        pid = pid_i if pid_i > 0 else None
    except Exception:
        pid = None
    return {"active": active, "main_pid": pid}


def clamp_dict(d: Dict[str, Any], max_items: int) -> Dict[str, Any]:
    if len(d) <= max_items:
        return d
    # deterministic truncation: sort keys and take first max_items
    out: Dict[str, Any] = {}
    for k in sorted(d.keys())[:max_items]:
        out[k] = d[k]
    out["_truncated"] = True
    out["_kept"] = max_items
    out["_total"] = len(d)
    return out


def main() -> int:
    deployed_commit = read_text_file(DEPLOYED_COMMIT_FILE, max_chars=64)
    git_head, dirty = git_head_and_dirty("/opt/rollingthunder")

    # Unit file hashes + runtime
    units_hash: Dict[str, Any] = {}
    unit_runtime: Dict[str, Any] = {}
    for u in UNITS:
        unit_path = os.path.join(SYSTEMD_DIR, u)
        h = sha256_file(unit_path) if os.path.isfile(unit_path) else None
        units_hash[u] = h or "missing"
        unit_runtime[u] = systemd_unit_runtime(u)

    # Root-owned executables hashes (controller-side truth)
    services_hash: Dict[str, Any] = {}
    for p in sorted(glob.glob(SERVICES_GLOB)):
        base = os.path.basename(p)
        h = sha256_file(p)
        services_hash[base] = h or "missing"

    report: Dict[str, Any] = {
        "schema": "deploy.report.v1",
        "node_id": NODE_ID,
        "role": ROLE,
        "ts_ms": now_ms(),
        "deployed_commit": deployed_commit,
        "git_head": git_head,
        "dirty": dirty,
        "units": clamp_dict(units_hash, MAX_ITEMS),
        "unit_runtime": clamp_dict(unit_runtime, MAX_ITEMS),
        "services": clamp_dict(services_hash, MAX_ITEMS),
    }

    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
    )

    key = f"{DEPLOY_KEY_PREFIX}{NODE_ID}"
    payload = json.dumps(report, separators=(",", ":"), ensure_ascii=False)

    # Write report + TTL
    r.set(key, payload)
    try:
        r.expire(key, DEPLOY_TTL_SEC)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
