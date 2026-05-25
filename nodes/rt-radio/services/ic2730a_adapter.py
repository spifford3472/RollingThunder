#!/usr/bin/env python3
"""
RollingThunder IC-2730A adapter boundary.

Phase 8B scope:
- Isolated IC-2730A adapter module only.
- Safe by default.
- Supports disabled, dry_run, hamlib_readonly, and hamlib_write_test modes.
- Keeps all IC-2730A / Python Hamlib details inside this file.
- Does not publish Redis.
- Does not write rt:ui:bus.
- Does not call rigctl.
- Does not use rigctld.
- Does not clear memories.
- Does not bulk-write memories.
- Does not start or stop scanning.
- Does not program Side B.
- Does not expose PTT/transmit controls.

Phase 8B adds a controlled request path for one explicitly configured
sacrificial memory write test. Because the exact Python Hamlib memory-write
API for the IC-2730A has not been verified here, hamlib_write_test validates
all gates and the exact target/payload, then returns status=not_implemented
with operation_performed=false rather than guessing at memory-write calls.

Important IC-2730A / Hamlib finding:
Python Hamlib may print:
  "Rig does not have VFO A/B?"
  "Mapping VFOA=Main"

That warning alone is not treated as a failure if the read-only frequency
query succeeds.

Read-only exception:
For this IC-2730A/Hamlib combination, set_vfo(Hamlib.RIG_VFO_A) may be
needed to establish the Main/VFOA mapping before get_freq(). In read-only
status detection, that operation is allowed only inside the explicitly named
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

SUPPORTED_CONTROL_MODES = {"disabled", "dry_run", "hamlib_readonly", "hamlib_write_test"}


DEFAULT_WRITE_TEST = {
    "enabled": False,
    "allow_single_memory_write": False,
    "sacrificial_group": "D",
    "sacrificial_channel": 99,
    "frequency_mhz": 146.520,
    "mode": "FM",
    "name": "RT TEST",
    "tone_hz": None,
    "offset_mhz": 0.0,
    "duplex": "simplex",
}


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


def _safe_optional_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return float(value)
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


def _write_test_from_ic(ic_config: Dict[str, Any]) -> Dict[str, Any]:
    raw = ic_config.get("write_test", {})
    if not isinstance(raw, dict):
        raw = {}

    return {
        "enabled": _safe_bool(raw.get("enabled"), bool(DEFAULT_WRITE_TEST["enabled"])),
        "allow_single_memory_write": _safe_bool(
            raw.get("allow_single_memory_write"),
            bool(DEFAULT_WRITE_TEST["allow_single_memory_write"]),
        ),
        "sacrificial_group": _safe_group_text(raw.get("sacrificial_group", DEFAULT_WRITE_TEST["sacrificial_group"])),
        "sacrificial_channel": _safe_int(
            raw.get("sacrificial_channel"),
            int(DEFAULT_WRITE_TEST["sacrificial_channel"]),
            minimum=1,
        ),
        "frequency_mhz": _safe_float(raw.get("frequency_mhz"), float(DEFAULT_WRITE_TEST["frequency_mhz"]), minimum=0.0),
        "mode": _safe_str(raw.get("mode"), str(DEFAULT_WRITE_TEST["mode"])).upper(),
        "name": _safe_str(raw.get("name"), str(DEFAULT_WRITE_TEST["name"])),
        "tone_hz": _safe_optional_float(raw.get("tone_hz"), DEFAULT_WRITE_TEST["tone_hz"]),
        "offset_mhz": _safe_float(raw.get("offset_mhz"), float(DEFAULT_WRITE_TEST["offset_mhz"])),
        "duplex": _safe_str(raw.get("duplex"), str(DEFAULT_WRITE_TEST["duplex"])).lower(),
    }


def _safe_group_text(group: Any) -> str:
    text = str(group or "").strip().upper()
    if text in {"C", "D"}:
        return text
    return str(DEFAULT_WRITE_TEST["sacrificial_group"])


def normalize_memory_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    return {
        "name": _safe_str(raw.get("name"), str(DEFAULT_WRITE_TEST["name"])),
        "frequency_mhz": round(_safe_float(raw.get("frequency_mhz"), float(DEFAULT_WRITE_TEST["frequency_mhz"]), minimum=0.0), 6),
        "mode": _safe_str(raw.get("mode"), str(DEFAULT_WRITE_TEST["mode"])).upper(),
        "tone_hz": _safe_optional_float(raw.get("tone_hz"), DEFAULT_WRITE_TEST["tone_hz"]),
        "offset_mhz": round(_safe_float(raw.get("offset_mhz"), float(DEFAULT_WRITE_TEST["offset_mhz"])), 6),
        "duplex": _safe_str(raw.get("duplex"), str(DEFAULT_WRITE_TEST["duplex"])).lower(),
    }


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

    write_test_enabled: bool = False
    write_test_allow_single_memory_write: bool = False
    write_test_sacrificial_group: str = "D"
    write_test_sacrificial_channel: int = 99
    write_test_payload: Dict[str, Any] = None  # type: ignore[assignment]

    @classmethod
    def from_app_config(cls, app_config: Dict[str, Any]) -> "IC2730AConfig":
        vhf = _vhf_config_from_app(app_config)
        ic = _ic2730a_config_from_vhf(vhf)
        write_test = _write_test_from_ic(ic)

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

        expected_payload = normalize_memory_payload(
            {
                "name": write_test["name"],
                "frequency_mhz": write_test["frequency_mhz"],
                "mode": write_test["mode"],
                "tone_hz": write_test["tone_hz"],
                "offset_mhz": write_test["offset_mhz"],
                "duplex": write_test["duplex"],
            }
        )

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
            write_test_enabled=bool(write_test["enabled"]),
            write_test_allow_single_memory_write=bool(write_test["allow_single_memory_write"]),
            write_test_sacrificial_group=str(write_test["sacrificial_group"]),
            write_test_sacrificial_channel=int(write_test["sacrificial_channel"]),
            write_test_payload=expected_payload,
        )


class IC2730AAdapter:
    """
    Safe IC-2730A adapter boundary.

    Business logic may call this class, but business logic must not contain
    Hamlib constants, VFO details, serial paths, CI-V details, or IC-2730A
    command details.
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
            "writes_enabled": bool(cfg.writes_enabled),
            "scan_control_enabled": bool(cfg.scan_control_enabled),
            "side_b_programming_enabled": bool(cfg.side_b_programming_enabled),
            "memory_programming_enabled": bool(cfg.memory_programming_enabled),
            "write_test_enabled": bool(cfg.write_test_enabled),
            "write_test_allow_single_memory_write": bool(cfg.write_test_allow_single_memory_write),
            "write_test_sacrificial_group": cfg.write_test_sacrificial_group,
            "write_test_sacrificial_channel": cfg.write_test_sacrificial_channel,
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

        if cfg.control_mode == "hamlib_write_test":
            if not cfg.detect_enabled:
                return self._result(
                    ok=False,
                    status="unavailable",
                    available=False,
                    reason="IC-2730A hamlib_write_test detection disabled in config.",
                )
            # Detection remains read-only even when the special write-test mode
            # exists. The actual write-test request path is separately gated.
            return self._hamlib_readonly_detect()

        return self._result(
            ok=False,
            status="error",
            available=False,
            reason=f"Unsupported IC-2730A control mode: {cfg.control_mode}",
        )

    def get_status(self) -> Dict[str, Any]:
        """Safe status query."""
        return self.detect()

    def query_identity(self) -> Dict[str, Any]:
        """
        Return safe adapter/radio identity information.

        This does not use rigctl and does not perform risky radio control.
        In hamlib_readonly/hamlib_write_test modes it may include the result of
        a read-only frequency query because the IC-2730A identity/caps path is
        not relied on for this phase.
        """

        status = self.detect()

        identity = {
            "adapter": SOURCE,
            "radio": self.config.radio_name,
            "hamlib_model": self.config.hamlib_model,
            "serial_port": self.config.serial_port,
            "control_mode": self.config.control_mode,
            "phase": "8B",
            "rigctl_used": False,
            "writes_enabled": bool(self.config.writes_enabled),
            "scan_control_enabled": bool(self.config.scan_control_enabled),
            "side_b_programming_enabled": bool(self.config.side_b_programming_enabled),
            "memory_programming_enabled": bool(self.config.memory_programming_enabled),
            "write_test_enabled": bool(self.config.write_test_enabled),
            "write_test_allow_single_memory_write": bool(self.config.write_test_allow_single_memory_write),
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
        Phase 8B has no verified safe scan-state read source.

        Return unknown/not_supported rather than pretending to know.
        """

        return self._result(
            ok=True,
            status="not_supported",
            available=bool(self.detect().get("available", False)),
            reason="IC-2730A scan-state readback is not implemented in Phase 8B.",
            extra={
                "actual_scan_state": "unknown",
            },
        )

    def query_hamlib_api_inventory(self, *, connected_readonly: bool = False) -> Dict[str, Any]:
        """
        Phase 8C-1 safe Python Hamlib API inventory.

        This method is inspection/proof only.

        It inventories:
        - Hamlib constants
        - Rig method names
        - Channel object fields
        - caps fields

        Optional connected_readonly mode opens the IC-2730A and checks method
        presence only. It uses the existing documented read-only VFOA/Main
        mapping exception, but does not call memory-write, frequency-write,
        mode-write, scan, group-clear, group-switch, Side B programming, PTT,
        transmit, or rigctl paths.
        """

        cfg = self.config

        def safe_repr(value: Any) -> str:
            try:
                return repr(value)
            except Exception as exc:
                return f"<repr error: {exc}>"

        def safe_getattr(obj: Any, name: str) -> Any:
            try:
                return getattr(obj, name)
            except Exception as exc:
                return f"<error reading: {exc}>"

        def names_containing(obj: Any, tokens: list[str], *, lower: bool = False) -> list[str]:
            out: list[str] = []
            for name in sorted(dir(obj)):
                comparable = name.lower() if lower else name.upper()
                for token in tokens:
                    wanted = token.lower() if lower else token.upper()
                    if wanted in comparable:
                        out.append(name)
                        break
            return out

        def inventory_constants(Hamlib: Any) -> Dict[str, Any]:
            fixed_names = [
                "__version__",
                "RIG_MODEL_IC2730",
                "RIG_MODEL_IC2730A",
                "RIG_PORT_SERIAL",
                "RIG_VFO_A",
                "RIG_VFO_B",
                "RIG_VFO_MAIN",
                "RIG_VFO_SUB",
                "RIG_VFO_MEM",
                "RIG_MODE_FM",
                "RIG_MODE_FMN",
                "RIG_DUPLEX_NONE",
                "RIG_DUPLEX_PLUS",
                "RIG_DUPLEX_MINUS",
            ]

            fixed: Dict[str, str] = {}
            for name in fixed_names:
                fixed[name] = safe_repr(safe_getattr(Hamlib, name))

            tokens = ["CHAN", "MEM", "SCAN", "TONE", "DUPLEX", "RPT", "VFO", "MODE", "SPLIT"]
            filtered: Dict[str, str] = {}
            for name in names_containing(Hamlib, tokens):
                filtered[name] = safe_repr(safe_getattr(Hamlib, name))

            return {
                "fixed": fixed,
                "filtered_names": filtered,
                "has_Channel": hasattr(Hamlib, "Channel"),
            }

        def inventory_rig_methods(Hamlib: Any, model: int) -> Dict[str, Any]:
            rig = Hamlib.Rig(model)

            method_tokens = [
                "chan",
                "mem",
                "scan",
                "vfo",
                "mode",
                "tone",
                "duplex",
                "rptr",
                "split",
                "freq",
            ]

            exact_names = [
                "get_freq",
                "set_freq",
                "get_mode",
                "set_mode",
                "get_mem",
                "set_mem",
                "get_channel",
                "set_channel",
                "get_vfo",
                "set_vfo",
                "scan",
                "get_split_vfo",
                "set_split_vfo",
                "get_split_freq",
                "set_split_freq",
                "get_split_mode",
                "set_split_mode",
                "get_rptr_shift",
                "set_rptr_shift",
                "get_rptr_offs",
                "set_rptr_offs",
                "get_ctcss_tone",
                "set_ctcss_tone",
                "get_ctcss_sql",
                "set_ctcss_sql",
                "get_dcs_code",
                "set_dcs_code",
            ]

            return {
                "methods_containing_tokens": names_containing(rig, method_tokens, lower=True),
                "exact_presence": {name: hasattr(rig, name) for name in exact_names},
            }

        def inventory_channel(Hamlib: Any) -> Dict[str, Any]:
            Channel = getattr(Hamlib, "Channel", None)
            if Channel is None:
                return {
                    "available": False,
                    "fields": {},
                }

            ch = Channel()
            fields: Dict[str, str] = {}
            for name in sorted(dir(ch)):
                if name.startswith("_"):
                    continue
                fields[name] = safe_repr(safe_getattr(ch, name))

            return {
                "available": True,
                "fields": fields,
            }

        def inventory_caps(Hamlib: Any, model: int) -> Dict[str, Any]:
            rig = Hamlib.Rig(model)
            caps = getattr(rig, "caps", None)

            if caps is None:
                return {
                    "available": False,
                    "fields": {},
                }

            tokens = ["chan", "mem", "scan", "vfo", "mode", "tone", "duplex", "rptr", "split"]
            fields: Dict[str, str] = {}
            for name in names_containing(caps, tokens, lower=True):
                fields[name] = safe_repr(safe_getattr(caps, name))

            return {
                "available": True,
                "fields": fields,
            }

        def connected_inventory(Hamlib: Any, model: int) -> Dict[str, Any]:
            rig = None
            result: Dict[str, Any] = {
                "attempted": True,
                "opened": False,
                "set_vfo_a_result": None,
                "method_presence_after_open": {},
                "error": None,
            }

            try:
                rig = Hamlib.Rig(model)
                rig.state.rigport.type.rig = Hamlib.RIG_PORT_SERIAL
                rig.state.rigport.pathname = cfg.serial_port
                rig.state.rigport.parm.serial.rate = cfg.baud

                rig.open()
                result["opened"] = True

                try:
                    rig.set_vfo(Hamlib.RIG_VFO_A)
                    result["set_vfo_a_result"] = "ok"
                except Exception as exc:
                    result["set_vfo_a_result"] = f"failed: {exc}"

                for method_name in [
                    "get_freq",
                    "get_mode",
                    "get_mem",
                    "get_channel",
                    "set_mem",
                    "set_channel",
                    "scan",
                ]:
                    result["method_presence_after_open"][method_name] = hasattr(rig, method_name)

            except Exception as exc:
                result["error"] = str(exc)

            finally:
                if rig is not None:
                    try:
                        rig.close()
                    except Exception:
                        pass

            return result

        try:
            import Hamlib  # type: ignore
        except Exception as exc:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason="Python Hamlib module is not available for Phase 8C-1 inventory.",
                detail=str(exc),
                extra={
                    "action": "query_hamlib_api_inventory",
                    "operation_performed": False,
                },
            )

        model = int(getattr(Hamlib, "RIG_MODEL_IC2730", cfg.hamlib_model))

        inventory = {
            "action": "query_hamlib_api_inventory",
            "phase": "8C-1",
            "operation_performed": False,
            "writes_performed": False,
            "memory_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "connected_readonly_requested": bool(connected_readonly),
            "hamlib_model_used": model,
            "constants": inventory_constants(Hamlib),
            "rig_methods": inventory_rig_methods(Hamlib, model),
            "channel": inventory_channel(Hamlib),
            "caps": inventory_caps(Hamlib, model),
            "connected_readonly": {
                "attempted": False,
            },
        }

        if connected_readonly:
            inventory["connected_readonly"] = connected_inventory(Hamlib, model)

        return self._result(
            ok=True,
            status="inventory",
            available=True,
            reason="Phase 8C-1 safe Python Hamlib API inventory completed; no write/control operation was performed.",
            extra=inventory,
        )

    def write_single_memory_test(self, group: str, channel: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and optionally execute one explicitly configured sacrificial
        memory write test.

        Safety behavior:
        - target group/channel and payload are validated before dry-run success.
        - dry_run returns dry_run and operation_performed=false only for the
          configured sacrificial target/payload.
        - disabled/hamlib_readonly reject write requests.
        - hamlib_write_test requires all write gates and exact target/payload.
        - Because exact Python Hamlib memory-write calls are not verified here,
          the real write path returns not_implemented, operation_performed=false.
        """

        cfg = self.config
        target_group = self._safe_group(group)
        try:
            target_channel = int(channel)
        except Exception:
            target_channel = -1
        normalized_payload = normalize_memory_payload(payload if isinstance(payload, dict) else {})

        common = {
            "action": "write_single_memory_test",
            "operation_performed": False,
            "target_group": target_group,
            "target_channel": target_channel,
            "payload": normalized_payload,
            "expected_group": cfg.write_test_sacrificial_group,
            "expected_channel": cfg.write_test_sacrificial_channel,
        }

        # Phase 8B safety ordering:
        # Even dry-run requests must target only the configured sacrificial
        # group/channel and payload. Dry-run means "no radio command", not
        # "accept any write-shaped request."
        if target_group != cfg.write_test_sacrificial_group or target_channel != cfg.write_test_sacrificial_channel:
            available = False if (not cfg.enabled or cfg.control_mode == "disabled") else bool(self.detect().get("available", False))
            return self._result(
                ok=False,
                status="rejected",
                available=available,
                reason="Request target does not match configured sacrificial group/channel.",
                extra=common,
            )

        if normalized_payload != cfg.write_test_payload:
            available = False if (not cfg.enabled or cfg.control_mode == "disabled") else bool(self.detect().get("available", False))
            return self._result(
                ok=False,
                status="rejected",
                available=available,
                reason="Request payload does not match configured sacrificial test payload.",
                extra={
                    **common,
                    "expected_payload": cfg.write_test_payload,
                },
            )

        if not cfg.enabled or cfg.control_mode == "disabled":
            return self._result(
                ok=False,
                status="rejected",
                available=False,
                reason="IC-2730A adapter is disabled; single memory write test rejected.",
                extra=common,
            )

        if cfg.control_mode == "dry_run":
            return self._result(
                ok=True,
                status="dry_run",
                available=True,
                reason="Dry-run only; no radio command was sent.",
                extra=common,
            )

        if cfg.control_mode == "hamlib_readonly":
            available = bool(self.detect().get("available", False))
            return self._result(
                ok=False,
                status="rejected",
                available=available,
                reason="hamlib_readonly mode cannot perform memory writes.",
                extra=common,
            )

        if cfg.control_mode != "hamlib_write_test":
            return self._result(
                ok=False,
                status="rejected",
                available=False,
                reason=f"Unsupported control mode for single memory write test: {cfg.control_mode}",
                extra=common,
            )

        gate_failures = []
        if not cfg.writes_enabled:
            gate_failures.append("writes_enabled=false")
        if not cfg.memory_programming_enabled:
            gate_failures.append("memory_programming_enabled=false")
        if not cfg.write_test_enabled:
            gate_failures.append("write_test.enabled=false")
        if not cfg.write_test_allow_single_memory_write:
            gate_failures.append("write_test.allow_single_memory_write=false")

        if gate_failures:
            available = bool(self.detect().get("available", False))
            return self._result(
                ok=False,
                status="rejected",
                available=available,
                reason="Single memory write test is disabled by config: " + ", ".join(gate_failures),
                extra={**common, "gate_failures": gate_failures},
            )

        available = bool(self.detect().get("available", False))
        if not available:
            return self._result(
                ok=False,
                status="rejected",
                available=False,
                reason="IC-2730A is not available for the gated single memory write test.",
                extra=common,
            )

        return self._result(
            ok=False,
            status="not_implemented",
            available=True,
            reason=(
                "All Phase 8B gates passed, but exact Python Hamlib IC-2730A "
                "memory-write API calls are not verified; no radio command was sent."
            ),
            extra={
                **common,
                "operation_performed": False,
                "hamlib_write_api_verified": False,
            },
        )

    def set_side_b_146520_fm(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="set_side_b_146520_fm",
            reason="Side B programming is disabled in Phase 8B.",
        )

    def prepare_memory_group(self, group: str) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="prepare_memory_group",
            reason="Memory group clearing/preparation is disabled in Phase 8B.",
            extra={"group": self._safe_group(group)},
        )

    def program_memory(self, group: str, channel: int, repeater: Dict[str, Any]) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="program_memory",
            reason="Bulk/general memory programming is disabled in Phase 8B.",
            extra={
                "group": self._safe_group(group),
                "channel": channel,
                "repeater_preview": self._safe_repeater_preview(repeater),
            },
        )

    def select_memory_group(self, group: str) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="select_memory_group",
            reason="Memory group selection is disabled in Phase 8B.",
            extra={"group": self._safe_group(group)},
        )

    def start_scan(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="start_scan",
            reason="Scan start is disabled in Phase 8B.",
        )

    def stop_scan(self) -> Dict[str, Any]:
        return self._stubbed_risky_operation(
            action="stop_scan",
            reason="Scan stop is disabled in Phase 8B.",
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
            reason=f"{reason} Real radio operation is gated off in Phase 8B.",
            extra={
                "action": action,
                "operation_performed": False,
                **(extra or {}),
            },
        )

    def _hamlib_readonly_detect(self) -> Dict[str, Any]:
        """
        Minimal read-only Python Hamlib detect/status path.

        Allowed behavior:
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

            # Documented read-only exception:
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

    Default behavior prints one structured JSON status object and exits.

    Optional Phase 8C-1 inventory modes:
      --hamlib-api-inventory
      --hamlib-api-inventory --connected-readonly

    These inventory modes do not publish Redis and do not perform memory writes,
    scan start, Side B programming, PTT/transmit, or rigctl subprocess calls.
    """

    adapter = IC2730AAdapter()

    if "--hamlib-api-inventory" in sys.argv:
        connected = "--connected-readonly" in sys.argv
        print(json.dumps(adapter.query_hamlib_api_inventory(connected_readonly=connected), indent=2, sort_keys=True))
        return 0

    print(json.dumps(adapter.get_status(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
