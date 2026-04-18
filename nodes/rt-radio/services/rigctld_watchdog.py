#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HOST = os.environ.get("RT_RIGCTLD_HOST", "127.0.0.1")
PORT = int(os.environ.get("RT_RIGCTLD_PORT", "4532"))
CHECK_INTERVAL_S = float(os.environ.get("RT_RIG_WATCHDOG_INTERVAL_S", "5"))
SOCKET_TIMEOUT_S = float(os.environ.get("RT_RIG_WATCHDOG_TIMEOUT_S", "2.5"))
FAIL_THRESHOLD = int(os.environ.get("RT_RIG_WATCHDOG_FAIL_THRESHOLD", "3"))
COOLDOWN_S = float(os.environ.get("RT_RIG_WATCHDOG_COOLDOWN_S", "30"))
SYSTEMCTL = os.environ.get("RT_SYSTEMCTL", "/bin/systemctl")
RIGCTLD_SERVICE = os.environ.get("RT_RIGCTLD_SERVICE", "rigctld")
MODEMRESET = os.environ.get("RT_RIG_WATCHDOG_MODEMRESET", "0") == "1"
CP210X_RESET = os.environ.get("RT_RIG_WATCHDOG_CP210X_RESET", "0") == "1"
LOG_PREFIX = "[rigctld-watchdog]"

_last_recovery_ts = 0.0


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def probe_rigctld() -> tuple[bool, str]:
    """
    Probe rigctld with a simple frequency read.
    Success means:
      - TCP connect works
      - command is accepted
      - non-empty response arrives before timeout
    """
    try:
        with socket.create_connection((HOST, PORT), timeout=SOCKET_TIMEOUT_S) as s:
            s.settimeout(SOCKET_TIMEOUT_S)
            s.sendall(b"f\n")
            chunks: list[bytes] = []
            deadline = time.monotonic() + SOCKET_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                payload = b"".join(chunks)
                if b"RPRT" in payload or b"\n" in payload:
                    break

            raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not raw:
                return False, "empty response"
            return True, raw
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_cmd(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    log("run: " + " ".join(cmd))
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=check,
    )


def restart_rigctld() -> None:
    run_cmd([SYSTEMCTL, "restart", RIGCTLD_SERVICE], check=False)


def try_modemmanager_stop() -> None:
    if not MODEMRESET:
        return
    run_cmd([SYSTEMCTL, "stop", "ModemManager"], check=False)


def try_cp210x_reset() -> None:
    if not CP210X_RESET:
        return
    run_cmd(["/sbin/modprobe", "-r", "cp210x"], check=False)
    time.sleep(1.0)
    run_cmd(["/sbin/modprobe", "cp210x"], check=False)


def service_is_active(name: str) -> bool:
    result = run_cmd([SYSTEMCTL, "is-active", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def maybe_recover() -> None:
    global _last_recovery_ts

    now = time.time()
    if now - _last_recovery_ts < COOLDOWN_S:
        log("recovery suppressed by cooldown")
        return

    _last_recovery_ts = now
    log("starting recovery")

    try_modemmanager_stop()
    restart_rigctld()
    time.sleep(3.0)

    ok, detail = probe_rigctld()
    if ok:
        log(f"recovery succeeded after rigctld restart: {detail!r}")
        return

    log(f"probe still failing after restart: {detail!r}")

    if CP210X_RESET:
        log("trying cp210x driver reset")
        try_cp210x_reset()
        time.sleep(2.0)
        restart_rigctld()
        time.sleep(3.0)
        ok, detail = probe_rigctld()
        if ok:
            log(f"recovery succeeded after cp210x reset: {detail!r}")
            return
        log(f"probe still failing after cp210x reset: {detail!r}")

    log("recovery attempt complete; still unhealthy")


def main() -> int:
    log(
        f"starting host={HOST} port={PORT} interval={CHECK_INTERVAL_S}s "
        f"timeout={SOCKET_TIMEOUT_S}s threshold={FAIL_THRESHOLD} cooldown={COOLDOWN_S}s "
        f"service={RIGCTLD_SERVICE}"
    )

    failures = 0

    while True:
        ok, detail = probe_rigctld()
        if ok:
            if failures:
                log(f"probe recovered: {detail!r}")
            failures = 0
        else:
            failures += 1
            log(f"probe failed ({failures}/{FAIL_THRESHOLD}): {detail}")
            if failures >= FAIL_THRESHOLD:
                maybe_recover()
                failures = 0

        time.sleep(CHECK_INTERVAL_S)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("stopped")
        raise SystemExit(0)