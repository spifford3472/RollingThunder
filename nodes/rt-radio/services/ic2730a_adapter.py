#!/usr/bin/env python3
"""
RollingThunder IC-2730A adapter boundary.

Phase 7B-1 scope:
- Isolated IC-2730A adapter module only.
- Safe by default.
- Supports disabled, dry_run, and hamlib_readonly modes.
- Keeps all IC-2730A / Python Hamlib details inside this file.
- Does not publish Redis.
- Does not write rt:ui:bus.
- Does not call rigctl.
- Does not use rigctld.
- Does not clear memories.
- Does not write memories.
- Does not start or stop scanning.
- Does not program Side B.
- Does not expose PTT/transmit controls.

Important IC-2730A / Hamlib finding:
Python Hamlib may print:
  "Rig does not have VFO A/B?"
  "Mapping VFOA=Main"

That warning alone is not treated as a failure if the read-only frequency
query succeeds.

Read-only exception:
For this IC-2730A/Hamlib combination, set_vfo(Hamlib.RIG_VFO_A) may be
needed to establish the Main/VFOA mapping before get_freq(). In Phase 7B,
that operation is allowed only inside the explicitly named
_hamlib_readonly_detect() path before a read-only get_freq() call.

No other write/control operation is allowed in hamlib_readonly mode.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


SOURCE = "ic2730a_adapter"
APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

DEFAULT_RADIO_NAME = "Icom IC-2730A"
DEFAULT_CONTROL_MODE = "dry_run"
DEFAULT_HAMLIB_MODEL = 3085
DEFAULT_SERIAL_PORT = "/dev/ic2730a"
DEFAULT_BAUD = 9600
DEFAULT_TIMEOUT_SECONDS = 2.0

SUPPORTED_CONTROL_MODES = {"disabled", "dry_run", "hamlib_readonly"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_str(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
        if minimum is not None and parsed < minimum:
            return default
        return parsed
    except Exception:
        return default


def _safe_float(value: Any, default: float, minimum: Optional[float] = None) -> float:
    try:
        parsed = float(value)
        if minimum is not None and parsed < minimum:
            return default
        return parsed
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False

    return default


def load_app_config(path: Path = APP_CONFIG_PATH) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"{SOURCE}: WARN: unable to read {path}: {exc}", file=sys.stderr)
        return {}


def _vhf_config_from_app(app_config: Dict[str, Any]) -> Dict[str, Any]:
    raw = app_config.get("vhf", {})
    return raw if isinstance(raw, dict) else {}


def _ic2730a_config_from_vhf(vhf_config: Dict[str, Any]) -> Dict[str, Any]:
    raw = vhf_config.get("ic2730a", {})
    return raw if isinstance(raw, dict) else {}


@dataclass(frozen=True)
class IC2730AConfig:
    radio_name: str = DEFAULT_RADIO_NAME
    enabled: bool = True
    control_mode: str = DEFAULT_CONTROL_MODE
    hamlib_model: int = DEFAULT_HAMLIB_MODEL
    serial_port: str = DEFAULT_SERIAL_PORT
    baud: int = DEFAULT_BAUD
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    writes_enabled: bool = False
    scan_control_enabled: bool = False
    side_b_programming_enabled: bool = False
    memory_programming_enabled: bool = False
    detect_enabled: bool = True

    @classmethod
    def from_app_config(cls, app_config: Dict[str, Any]) -> "IC2730AConfig":
        vhf = _vhf_config_from_app(app_config)
        ic = _ic2730a_config_from_vhf(vhf)

        # The top-level vhf.radio_control_mode remains supported as a fallback,
        # but the IC-2730A-specific block wins when present.
        fallback_control_mode = _safe_str(
            vhf.get("radio_control_mode"),
            DEFAULT_CONTROL_MODE,
        ).lower()

        control_mode = _safe_str(
            ic.get("control_mode"),
            fallback_control_mode,
        ).lower()

        if control_mode not in SUPPORTED_CONTROL_MODES:
            control_mode = DEFAULT_CONTROL_MODE

        return cls(
            radio_name=_safe_str(vhf.get("radio_name"), DEFAULT_RADIO_NAME),
            enabled=_safe_bool(ic.get("enabled"), True),
            control_mode=control_mode,
            hamlib_model=_safe_int(ic.get("hamlib_model"), DEFAULT_HAMLIB_MODEL, minimum=1),
            serial_port=_safe_str(ic.get("serial_port"), DEFAULT_SERIAL_PORT),
            baud=_safe_int(ic.get("baud"), DEFAULT_BAUD, minimum=1),
            timeout_seconds=_safe_float(
                ic.get("timeout_seconds"),
                DEFAULT_TIMEOUT_SECONDS,
                minimum=0.1,
            ),
            writes_enabled=_safe_bool(ic.get("writes_enabled"), False),
            scan_control_enabled=_safe_bool(ic.get("scan_control_enabled"), False),
            side_b_programming_enabled=_safe_bool(ic.get("side_b_programming_enabled"), False),
            memory_programming_enabled=_safe_bool(ic.get("memory_programming_enabled"), False),
            detect_enabled=_safe_bool(ic.get("detect_enabled"), True),
        )


class IC2730AAdapter:
    """
    Safe IC-2730A adapter boundary.

    Business logic may call this class, but business logic must not contain
    Hamlib constants, VFO details, serial paths, CI-V details, or IC-2730A
    command details.

    In Phase 7B, all risky operations are stubbed or dry-run only.
    """

    def __init__(self, config: Optional[IC2730AConfig | Dict[str, Any]] = None) -> None:
        if config is None:
            self.config = IC2730AConfig.from_app_config(load_app_config())
        elif isinstance(config, IC2730AConfig):
            self.config = config
        elif isinstance(config, dict):
            self.config = IC2730AConfig.from_app_config(config)
        else:
            raise TypeError("config must be IC2730AConfig, dict, or None")

    def _base_result(self) -> Dict[str, Any]:
        cfg = self.config
        return {
            "radio": cfg.radio_name,
            "control_mode": cfg.control_mode,
            "hamlib_model": cfg.hamlib_model,
            "serial_port": cfg.serial_port,
            "writes_enabled": False,
            "scan_control_enabled": False,
            "side_b_programming_enabled": False,
            "memory_programming_enabled": False,
            "source": SOURCE,
            "updated_utc": utc_now(),
        }

    def _result(
        self,
        *,
        ok: bool,
        status: str,
        available: bool,
        reason: str,
        detail: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model = self._base_result()
        model.update(
            {
                "ok": ok,
                "status": status,
                "available": available,
                "reason": reason,
            }
        )

        if detail:
            model["detail"] = detail

        if extra:
            model.update(extra)

        return model

    def detect(self) -> Dict[str, Any]:
        """
        Return disabled, dry_run, unavailable, or detected/read-only status.

        This method never performs memory writes, frequency changes, mode changes,
        scan starts/stops, Side B programming, or PTT/transmit.
        """

        cfg = self.config

        if not cfg.enabled:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason="IC-2730A adapter disabled in config.",
            )

        if cfg.control_mode == "disabled":
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason="IC-2730A control path disabled.",
            )

        if cfg.control_mode == "dry_run":
            return self._result(
                ok=True,
                status="dry_run",
                available=True,
                reason="IC-2730A adapter running in dry-run mode.",
            )

        if cfg.control_mode == "hamlib_readonly":
            if not cfg.detect_enabled:
                return self._result(
                    ok=False,
                    status="unavailable",
                    available=False,
                    reason="IC-2730A hamlib_readonly detection disabled in config.",
                )
            return self._hamlib_readonly_detect()

        return self._result(
            ok=False,
            status="error",
            available=False,
            reason=f"Unsupported IC-2730A control mode: {cfg.control_mode}",
        )

    def get_status(self) -> Dict[str, Any]:
        """
        Safe status query.

        In Phase 7B this is intentionally equivalent to detect().
        """
        return self.detect()

    def query_identity(self) -> Dict[str, Any]:
        """
        Return safe adapter/radio identity information.

        This does not use rigctl and does not perform risky radio control.
        In hamlib_readonly mode it may include the result of a read-only
        frequency query because the IC-2730A identity/caps path is not relied
        on for this phase.
        """

        status = self.detect()

        identity = {
            "adapter": SOURCE,
            "radio": self.config.radio_name,
            "hamlib_model": self.config.hamlib_model,
            "serial_port": self.config.serial_port,
            "control_mode": self.config.control_mode,
            "phase": "7B-1",
            "rigctl_used": False,
            "writes_enabled": False,
            "scan_control_enabled": False,
            "side_b_programming_enabled": False,
            "memory_programming_enabled": False,
        }

        return self._result(
            ok=bool(status.get("ok")),
            status=str(status.get("status", "unknown")),
            available=bool(status.get("available")),
            reason="Safe IC-2730A adapter identity returned.",
            extra={
                "identity": identity,
                "detect_status": status,
            },
        )

    def query_scan_state(self) -> Dict[str, Any]:
        """
        Phase 7B has no verified safe scan-state read source.

        Return unknown/not_supported rather than pretending to know.
        """

        return self._result(
            ok=True,
            status="not_supported",
            available=bool(self.detect().get("available", False)),
            reason="IC-2730A scan-state readback is not implemented in Phase 7B.",
            extra={
                "actual_scan_state": "unknown",
            },
        )

    def set_side_b_146520_fm(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="set_side_b_146520_fm",
            reason="Side B programming is disabled in Phase 7B.",
        )

    def prepare_memory_group(self, group: str) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="prepare_memory_group",
            reason="Memory group clearing/preparation is disabled in Phase 7B.",
            extra={"group": self._safe_group(group)},
        )

    def program_memory(self, group: str, channel: int, repeater: Dict[str, Any]) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="program_memory",
            reason="Memory programming is disabled in Phase 7B.",
            extra={
                "group": self._safe_group(group),
                "channel": channel,
                "repeater_preview": self._safe_repeater_preview(repeater),
            },
        )

    def select_memory_group(self, group: str) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="select_memory_group",
            reason="Memory group selection is disabled in Phase 7B.",
            extra={"group": self._safe_group(group)},
        )

    def start_scan(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="start_scan",
            reason="Scan start is disabled in Phase 7B.",
        )

    def stop_scan(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="stop_scan",
            reason="Scan stop is disabled in Phase 7B.",
        )

    def _stubbed_risky_operation(
        self,
        *,
        action: str,
        reason: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Return a structured stub result for a risky operation.

        This method intentionally never calls Hamlib.
        """

        cfg = self.config

        if cfg.control_mode == "disabled" or not cfg.enabled:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason=f"{reason} Adapter is disabled.",
                extra={
                    "action": action,
                    "operation_performed": False,
                    **(extra or {}),
                },
            )

        if cfg.control_mode == "dry_run":
            return self._result(
                ok=True,
                status="dry_run",
                available=True,
                reason=f"{reason} Dry-run only; no radio command was sent.",
                extra={
                    "action": action,
                    "operation_performed": False,
                    **(extra or {}),
                },
            )

        return self._result(
            ok=False,
            status="disabled",
            available=bool(self.detect().get("available", False)),
            reason=f"{reason} Real radio operation is gated off in Phase 7B.",
            extra={
                "action": action,
                "operation_performed": False,
                **(extra or {}),
            },
        )

    def _hamlib_readonly_detect(self) -> Dict[str, Any]:
        """
        Minimal read-only Python Hamlib detect/status path.

        Allowed Phase 7B behavior:
        - import Python Hamlib
        - create Hamlib.Rig for IC-2730A
        - configure serial path and baud
        - open the rig
        - call set_vfo(Hamlib.RIG_VFO_A) only to establish Main/VFOA mapping
        - call get_freq(Hamlib.RIG_VFO_A)
        - close the rig

        Forbidden here:
        - set_freq
        - set_mode
        - set_mem
        - memory writes
        - scan start/stop
        - Side B programming
        - PTT/transmit
        - rigctl subprocess
        """

        cfg = self.config
        rig = None

        try:
            import Hamlib  # type: ignore
        except Exception as exc:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason="Python Hamlib module is not available.",
                detail=str(exc),
            )

        try:
            model = getattr(Hamlib, "RIG_MODEL_IC2730", cfg.hamlib_model)
            rig = Hamlib.Rig(model)

            # These attribute paths match the working Phase 7A test.
            rig.state.rigport.type.rig = Hamlib.RIG_PORT_SERIAL
            rig.state.rigport.pathname = cfg.serial_port
            rig.state.rigport.parm.serial.rate = cfg.baud

            rig.open()

            # Phase 7B documented read-only exception:
            # set_vfo(VFO_A) is allowed only here to establish the IC-2730A
            # Main/VFOA mapping before get_freq(). Hamlib may warn that it is
            # mapping VFOA=Main; that warning is acceptable if get_freq works.
            rig.set_vfo(Hamlib.RIG_VFO_A)

            freq_hz = float(rig.get_freq(Hamlib.RIG_VFO_A))
            freq_mhz = round(freq_hz / 1_000_000.0, 6)

            return self._result(
                ok=True,
                status="detected",
                available=True,
                reason="IC-2730A responded to safe Hamlib read-only query.",
                extra={
                    "frequency_a_hz": int(freq_hz),
                    "frequency_a_mhz": freq_mhz,
                    "hamlib_readonly_vfo_mapping_note": (
                        "set_vfo(RIG_VFO_A) used only to establish Main/VFOA "
                        "mapping before read-only get_freq()."
                    ),
                },
            )

        except Exception as exc:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason="IC-2730A did not respond to safe Hamlib read-only query.",
                detail=str(exc),
            )

        finally:
            if rig is not None:
                try:
                    rig.close()
                except Exception:
                    pass

    @staticmethod
    def _safe_group(group: str) -> str:
        text = str(group or "").strip().upper()
        if text in {"C", "D"}:
            return text
        return "unknown"

    @staticmethod
    def _safe_repeater_preview(repeater: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(repeater, dict):
            return {}

        allowed_keys = [
            "callsign",
            "name",
            "frequency_mhz",
            "input_mhz",
            "offset_mhz",
            "tone_hz",
            "mode",
            "distance_miles",
            "bearing_degrees",
        ]

        preview: Dict[str, Any] = {}
        for key in allowed_keys:
            if key in repeater:
                preview[key] = repeater[key]
        return preview


def main() -> int:
    """
    Small manual diagnostic entrypoint.

    This prints one structured JSON status object and exits. It does not publish
    Redis and does not perform risky radio operations.
    """

    adapter = IC2730AAdapter()
    print(json.dumps(adapter.get_status(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())