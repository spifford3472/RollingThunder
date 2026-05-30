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
import time
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

DEFAULT_DIRECT_CIV_ENABLED = False
DEFAULT_DIRECT_CIV_READONLY_PROBE_ENABLED = False
DEFAULT_DIRECT_CIV_SERIAL_PORT = DEFAULT_SERIAL_PORT
DEFAULT_DIRECT_CIV_BAUD = DEFAULT_BAUD
DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX = "E0"
DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX = "90"
DEFAULT_DIRECT_CIV_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

DEFAULT_DIRECT_CIV_READONLY_PROBE_COMMANDS = (
    "transceiver_id",
    "operating_frequency",
    "operating_mode",
    "duplex",
    "offset",
    "rx_tx_status",
)

DEFAULT_DIRECT_CIV_TONE_READONLY_PROBE_ENABLED = False

DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_ENABLED = False

DEFAULT_DIRECT_CIV_SIDE_A_WRITE_PLAN_ENABLED = False

DEFAULT_DIRECT_CIV_SIDE_A_REAL_TUNE_TEST_ENABLED = False

DEFAULT_DIRECT_CIV_SIDE_A_REPEATER_TUNE_TEST_ENABLED = False

DEFAULT_DIRECT_CIV_SIDE_A_DUPLEX_PROOF_ENABLED = False

DEFAULT_DIRECT_CIV_CD_MEMORY_READ_PROOF_ENABLED = False

STANDARD_CTCSS_TONES_HZ = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4,
    88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9,
    114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2,
    151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8,
    177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8,
    250.3, 254.1,
)

DEFAULT_DIRECT_CIV_READONLY_TONE_PROBE_COMMANDS = (
    "tone_setting",
    "repeater_tone_frequency",
    "tone_squelch_frequency",
    "dtcs_code_polarity",
)

DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_COMMANDS = (
    "rx_tx_status",
    "operating_frequency",
    "operating_mode",
    "duplex",
    "offset",
    "tone_setting",
    "repeater_tone_frequency",
    "tone_squelch_frequency",
    "dtcs_code_polarity",
)


DIRECT_CIV_READONLY_TONE_COMMANDS = {
    "tone_setting": {
        "documented_command_code": "1A 00",
        "command": bytes([0x1A, 0x00]),
        "response_prefix": bytes([0x1A, 0x00]),
        "dual_purpose_family": True,
    },
    "repeater_tone_frequency": {
        "documented_command_code": "1B 00",
        "command": bytes([0x1B, 0x00]),
        "response_prefix": bytes([0x1B, 0x00]),
        "dual_purpose_family": True,
    },
    "tone_squelch_frequency": {
        "documented_command_code": "1B 01",
        "command": bytes([0x1B, 0x01]),
        "response_prefix": bytes([0x1B, 0x01]),
        "dual_purpose_family": True,
    },
    "dtcs_code_polarity": {
        "documented_command_code": "1B 02",
        "command": bytes([0x1B, 0x02]),
        "response_prefix": bytes([0x1B, 0x02]),
        "dual_purpose_family": True,
    },
}

DIRECT_CIV_READONLY_COMMANDS = {
    "transceiver_id": {
        "documented_command_code": "19 00",
        "command": bytes([0x19, 0x00]),
        "response_prefix": bytes([0x19, 0x00]),
    },
    "operating_frequency": {
        "documented_command_code": "03",
        "command": bytes([0x03]),
        "response_prefix": bytes([0x03]),
    },
    "operating_mode": {
        "documented_command_code": "04",
        "command": bytes([0x04]),
        "response_prefix": bytes([0x04]),
    },
    "duplex": {
        "documented_command_code": "0F",
        "command": bytes([0x0F]),
        "response_prefix": bytes([0x0F]),
    },
    "offset": {
        "documented_command_code": "0C",
        "command": bytes([0x0C]),
        "response_prefix": bytes([0x0C]),
    },
    "rx_tx_status": {
        "documented_command_code": "1C 00",
        "command": bytes([0x1C, 0x00]),
        "response_prefix": bytes([0x1C, 0x00]),
    },
}

DIRECT_CIV_SIDE_A_READINESS_COMMANDS = {
    **DIRECT_CIV_READONLY_COMMANDS,
    **DIRECT_CIV_READONLY_TONE_COMMANDS,
}

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

IC2730A_CD_BANK_CHANNEL_RANGES = {
    "C": (100, 149),
    "D": (150, 199),
}

IC2730A_CD_BANK_SIZE = 50

DEFAULT_CD_RELOAD = {
    "cd_bank_reload_enabled": False,
    "memory_clear_enabled": False,
    "dry_run_cd_reload": True,
    "allow_banks": ["C", "D"],
    "max_bank_channels": IC2730A_CD_BANK_SIZE,
    "require_rx_not_tx_before_write": True,
    "require_stop_scan_before_clear": True,
    "require_readback_after_write": True,
}

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _safe_hex_byte(value: Any, default: str) -> int:
    text = str(value if value is not None else default).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if text.lower().endswith("h"):
        text = text[:-1]

    try:
        parsed = int(text, 16)
        if 0 <= parsed <= 255:
            return parsed
    except Exception:
        pass

    return int(str(default).replace("0x", "").replace("h", ""), 16)

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
    memory_clear_enabled: bool = False
    cd_bank_reload_enabled: bool = False
    dry_run_cd_reload: bool = True
    allowed_reload_banks: tuple[str, ...] = ("C", "D")
    max_bank_channels: int = IC2730A_CD_BANK_SIZE
    require_rx_not_tx_before_write: bool = True
    require_stop_scan_before_clear: bool = True
    require_readback_after_write: bool = True
    detect_enabled: bool = True

    direct_civ_enabled: bool = DEFAULT_DIRECT_CIV_ENABLED
    direct_civ_readonly_probe_enabled: bool = DEFAULT_DIRECT_CIV_READONLY_PROBE_ENABLED
    direct_civ_tone_readonly_probe_enabled: bool = DEFAULT_DIRECT_CIV_TONE_READONLY_PROBE_ENABLED
    direct_civ_side_a_readiness_probe_enabled: bool = DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_ENABLED
    direct_civ_side_a_write_plan_enabled: bool = DEFAULT_DIRECT_CIV_SIDE_A_WRITE_PLAN_ENABLED
    direct_civ_side_a_real_tune_test_enabled: bool = DEFAULT_DIRECT_CIV_SIDE_A_REAL_TUNE_TEST_ENABLED
    direct_civ_side_a_repeater_tune_test_enabled: bool = DEFAULT_DIRECT_CIV_SIDE_A_REPEATER_TUNE_TEST_ENABLED
    direct_civ_side_a_duplex_proof_enabled: bool = DEFAULT_DIRECT_CIV_SIDE_A_DUPLEX_PROOF_ENABLED
    direct_civ_cd_memory_read_proof_enabled: bool = DEFAULT_DIRECT_CIV_CD_MEMORY_READ_PROOF_ENABLED
    direct_civ_side_a_readiness_probe_commands: tuple[str, ...] = DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_COMMANDS
    direct_civ_serial_port: str = DEFAULT_DIRECT_CIV_SERIAL_PORT
    direct_civ_baud: int = DEFAULT_DIRECT_CIV_BAUD
    direct_civ_controller_address_hex: str = DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX
    direct_civ_transceiver_address_hex: str = DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX
    direct_civ_timeout_seconds: float = DEFAULT_DIRECT_CIV_TIMEOUT_SECONDS
    direct_civ_readonly_probe_commands: tuple[str, ...] = DEFAULT_DIRECT_CIV_READONLY_PROBE_COMMANDS
    direct_civ_readonly_tone_probe_commands: tuple[str, ...] = DEFAULT_DIRECT_CIV_READONLY_TONE_PROBE_COMMANDS

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

        direct_raw_commands = ic.get(
            "direct_civ_readonly_probe_commands",
            DEFAULT_DIRECT_CIV_READONLY_PROBE_COMMANDS,
        )
        direct_commands: list[str] = []

        if isinstance(direct_raw_commands, list):
            for item in direct_raw_commands:
                name = str(item or "").strip()
                if name in DIRECT_CIV_READONLY_COMMANDS and name not in direct_commands:
                    direct_commands.append(name)

        if not direct_commands:
            direct_commands = list(DEFAULT_DIRECT_CIV_READONLY_PROBE_COMMANDS)

        tone_raw_commands = ic.get(
            "direct_civ_readonly_tone_probe_commands",
            DEFAULT_DIRECT_CIV_READONLY_TONE_PROBE_COMMANDS,
        )
        tone_commands: list[str] = []

        if isinstance(tone_raw_commands, list):
            for item in tone_raw_commands:
                name = str(item or "").strip()
                if name in DIRECT_CIV_READONLY_TONE_COMMANDS and name not in tone_commands:
                    tone_commands.append(name)

        if not tone_commands:
            tone_commands = list(DEFAULT_DIRECT_CIV_READONLY_TONE_PROBE_COMMANDS)

        side_a_readiness_raw_commands = ic.get(
            "direct_civ_side_a_readiness_probe_commands",
            DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_COMMANDS,
        )
        side_a_readiness_commands: list[str] = []

        if isinstance(side_a_readiness_raw_commands, list):
            for item in side_a_readiness_raw_commands:
                name = str(item or "").strip()
                if name in DIRECT_CIV_SIDE_A_READINESS_COMMANDS and name not in side_a_readiness_commands:
                    side_a_readiness_commands.append(name)

        if not side_a_readiness_commands:
            side_a_readiness_commands = list(DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_COMMANDS)

        cd_reload = ic.get("cd_reload", {})
        if not isinstance(cd_reload, dict):
            cd_reload = {}

        def cd_value(name: str, default: Any) -> Any:
            return ic.get(name, cd_reload.get(name, default))

        allowed_raw = cd_value("allow_banks", DEFAULT_CD_RELOAD["allow_banks"])
        allowed_banks: list[str] = []
        if isinstance(allowed_raw, list):
            for item in allowed_raw:
                bank = str(item or "").strip().upper()
                if bank in IC2730A_CD_BANK_CHANNEL_RANGES and bank not in allowed_banks:
                    allowed_banks.append(bank)

        if not allowed_banks:
            allowed_banks = list(DEFAULT_CD_RELOAD["allow_banks"])

        max_bank_channels = _safe_int(
            cd_value("max_bank_channels", DEFAULT_CD_RELOAD["max_bank_channels"]),
            int(DEFAULT_CD_RELOAD["max_bank_channels"]),
            minimum=1,
        )
        if max_bank_channels > IC2730A_CD_BANK_SIZE:
            max_bank_channels = IC2730A_CD_BANK_SIZE

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
            memory_clear_enabled=_safe_bool(
                cd_value("memory_clear_enabled", DEFAULT_CD_RELOAD["memory_clear_enabled"]),
                bool(DEFAULT_CD_RELOAD["memory_clear_enabled"]),
            ),
            cd_bank_reload_enabled=_safe_bool(
                cd_value("cd_bank_reload_enabled", DEFAULT_CD_RELOAD["cd_bank_reload_enabled"]),
                bool(DEFAULT_CD_RELOAD["cd_bank_reload_enabled"]),
            ),
            dry_run_cd_reload=_safe_bool(
                cd_value("dry_run_cd_reload", DEFAULT_CD_RELOAD["dry_run_cd_reload"]),
                bool(DEFAULT_CD_RELOAD["dry_run_cd_reload"]),
            ),
            allowed_reload_banks=tuple(allowed_banks),
            max_bank_channels=max_bank_channels,
            require_rx_not_tx_before_write=_safe_bool(
                cd_value("require_rx_not_tx_before_write", DEFAULT_CD_RELOAD["require_rx_not_tx_before_write"]),
                bool(DEFAULT_CD_RELOAD["require_rx_not_tx_before_write"]),
            ),
            require_stop_scan_before_clear=_safe_bool(
                cd_value("require_stop_scan_before_clear", DEFAULT_CD_RELOAD["require_stop_scan_before_clear"]),
                bool(DEFAULT_CD_RELOAD["require_stop_scan_before_clear"]),
            ),
            require_readback_after_write=_safe_bool(
                cd_value("require_readback_after_write", DEFAULT_CD_RELOAD["require_readback_after_write"]),
                bool(DEFAULT_CD_RELOAD["require_readback_after_write"]),
            ),
            detect_enabled=_safe_bool(ic.get("detect_enabled"), True),

            direct_civ_enabled=_safe_bool(
                ic.get("direct_civ_enabled"),
                DEFAULT_DIRECT_CIV_ENABLED,
            ),
            direct_civ_readonly_probe_enabled=_safe_bool(
                ic.get("direct_civ_readonly_probe_enabled"),
                DEFAULT_DIRECT_CIV_READONLY_PROBE_ENABLED,
            ),
            direct_civ_tone_readonly_probe_enabled=_safe_bool(
                ic.get("direct_civ_tone_readonly_probe_enabled"),
                DEFAULT_DIRECT_CIV_TONE_READONLY_PROBE_ENABLED,
            ),
            direct_civ_side_a_readiness_probe_enabled=_safe_bool(
                ic.get("direct_civ_side_a_readiness_probe_enabled"),
                DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_ENABLED,
            ),
            direct_civ_side_a_write_plan_enabled=_safe_bool(
                ic.get("direct_civ_side_a_write_plan_enabled"),
                DEFAULT_DIRECT_CIV_SIDE_A_WRITE_PLAN_ENABLED,
            ),
            direct_civ_side_a_real_tune_test_enabled=_safe_bool(
                ic.get("direct_civ_side_a_real_tune_test_enabled"),
                DEFAULT_DIRECT_CIV_SIDE_A_REAL_TUNE_TEST_ENABLED,
            ),
            direct_civ_side_a_repeater_tune_test_enabled=_safe_bool(
                ic.get("direct_civ_side_a_repeater_tune_test_enabled"),
                DEFAULT_DIRECT_CIV_SIDE_A_REPEATER_TUNE_TEST_ENABLED,
            ),
            direct_civ_side_a_duplex_proof_enabled=_safe_bool(
                ic.get("direct_civ_side_a_duplex_proof_enabled"),
                DEFAULT_DIRECT_CIV_SIDE_A_DUPLEX_PROOF_ENABLED,
            ),
            direct_civ_cd_memory_read_proof_enabled=_safe_bool(
                cd_value("cd_memory_read_proof_enabled", DEFAULT_DIRECT_CIV_CD_MEMORY_READ_PROOF_ENABLED),
                DEFAULT_DIRECT_CIV_CD_MEMORY_READ_PROOF_ENABLED,
            ),
            direct_civ_serial_port=_safe_str(
                ic.get("direct_civ_serial_port"),
                _safe_str(ic.get("serial_port"), DEFAULT_DIRECT_CIV_SERIAL_PORT),
            ),
            direct_civ_baud=_safe_int(
                ic.get("direct_civ_baud"),
                _safe_int(ic.get("baud"), DEFAULT_DIRECT_CIV_BAUD, minimum=1),
                minimum=1,
            ),
            direct_civ_controller_address_hex=str(
                ic.get("direct_civ_controller_address_hex", DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX)
            ).strip(),
            direct_civ_transceiver_address_hex=str(
                ic.get("direct_civ_transceiver_address_hex", DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX)
            ).strip(),
            direct_civ_timeout_seconds=_safe_float(
                ic.get("direct_civ_timeout_seconds"),
                _safe_float(ic.get("timeout_seconds"), DEFAULT_DIRECT_CIV_TIMEOUT_SECONDS, minimum=0.1),
                minimum=0.1,
            ),
            direct_civ_readonly_probe_commands=tuple(direct_commands),
            direct_civ_readonly_tone_probe_commands=tuple(tone_commands),
            direct_civ_side_a_readiness_probe_commands=tuple(side_a_readiness_commands),

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
            "memory_clear_enabled": bool(cfg.memory_clear_enabled),
            "cd_bank_reload_enabled": bool(cfg.cd_bank_reload_enabled),
            "cd_memory_read_proof_enabled": bool(cfg.direct_civ_cd_memory_read_proof_enabled),
            "dry_run_cd_reload": bool(cfg.dry_run_cd_reload),
            "allowed_reload_banks": list(cfg.allowed_reload_banks),
            "max_bank_channels": int(cfg.max_bank_channels),
            "require_rx_not_tx_before_write": bool(cfg.require_rx_not_tx_before_write),
            "require_stop_scan_before_clear": bool(cfg.require_stop_scan_before_clear),
            "require_readback_after_write": bool(cfg.require_readback_after_write),
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
        Phase 8.1C no-command scan-state contract.

        The uploaded IC-2730A bank-manager sample identifies:
        - CMD 14 0B as a scan-state read attempt.
        - fallback scan detection by frequency movement.

        This phase does not open serial and does not send the command yet.
        """

        return self._result(
            ok=True,
            status="dry_run",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason="Phase 8.1C scan-state query contract built; no CI-V command was sent.",
            extra={
                "action": "query_scan_state",
                "phase": "8.1C",
                "operation_performed": False,
                "serial_opened": False,
                "civ_command_sent": False,
                "read_only": True,
                "actual_scan_state": "unknown",
                "planned_read_command": {
                    "name": "read_scan_state_attempt",
                    "documented_command_code": "14 0B",
                    "sent": False,
                },
                "fallback_detection": {
                    "method": "frequency_movement",
                    "enabled_for_future_phase": True,
                    "performed": False,
                },
                "safety": {
                    "writes_performed": False,
                    "memory_write_performed": False,
                    "memory_clear_performed": False,
                    "bank_write_performed": False,
                    "scan_start_performed": False,
                    "scan_stop_performed": False,
                    "ptt_or_transmit_control_added": False,
                    "rigctl_used": False,
                    "ui_bus_written": False,
                    "redis_written_by_adapter": False,
                },
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
    
    def side_a_tune_candidate_test(self, candidate: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Phase 8C-4 Side-A/Main-band candidate tune/check request contract.

        This is a dry-run contract only.

        It validates and echoes a candidate payload for a future direct CI-V
        Side-A/Main-band tune/check action, but it intentionally does not:

        - open serial
        - import Hamlib
        - call rigctl
        - send any CI-V command
        - select A band as Main
        - write frequency
        - write mode
        - write duplex
        - write offset
        - write tone
        - write memory
        - write bank/group
        - start scan
        - program Side B
        - add PTT/transmit control
        - publish Redis
        - write rt:ui:bus
        - write rt:system:bus
        """

        raw = candidate if isinstance(candidate, dict) else {}
        errors: list[str] = []

        def candidate_float(name: str, *, required: bool, minimum: Optional[float] = None, maximum: Optional[float] = None) -> Optional[float]:
            value = raw.get(name)

            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    errors.append(f"{name} is required")
                return None

            try:
                parsed = float(value)
            except Exception:
                errors.append(f"{name} must be numeric")
                return None

            if minimum is not None and parsed < minimum:
                errors.append(f"{name} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                errors.append(f"{name} must be <= {maximum}")

            return parsed

        def candidate_str(name: str, default: str = "") -> str:
            value = raw.get(name)
            if value is None:
                return default
            return str(value).strip()

        frequency_mhz = candidate_float("frequency_mhz", required=True, minimum=118.0, maximum=550.0)

        mode = candidate_str("mode", "FM").upper()
        if mode != "FM":
            errors.append("mode must be FM")

        duplex_raw = candidate_str("duplex", "simplex").lower()
        duplex_aliases = {
            "simplex": "simplex",
            "none": "none",
            "plus": "plus",
            "+": "plus",
            "dup+": "dup+",
            "minus": "minus",
            "-": "minus",
            "dup-": "dup-",
        }

        duplex = duplex_aliases.get(duplex_raw)
        if duplex is None:
            errors.append("duplex must be one of: simplex, plus, minus, dup+, dup-, none")
            duplex = duplex_raw or "unknown"

        offset_mhz = candidate_float("offset_mhz", required=False, minimum=0.0)
        if offset_mhz is None:
            offset_mhz = 0.0

        tone_hz = candidate_float("tone_hz", required=False, minimum=50.0, maximum=300.0)

        tone_mode_raw = candidate_str("tone_mode", "none").lower()
        tone_mode_aliases = {
            "": "none",
            "none": "none",
            "off": "none",
            "tone": "tone",
            "ctcss": "tone",
            "tsql": "tsql",
            "tone_sql": "tsql",
            "tone_squelch": "tsql",
            "dtcs": "dtcs",
            "dcs": "dtcs",
        }
        tone_mode = tone_mode_aliases.get(tone_mode_raw, tone_mode_raw)

        normalized_candidate = {
            "frequency_mhz": round(float(frequency_mhz), 6) if frequency_mhz is not None else None,
            "mode": mode,
            "duplex": duplex,
            "offset_mhz": round(float(offset_mhz), 6),
            "tone_hz": round(float(tone_hz), 1) if tone_hz is not None else None,
            "tone_mode": tone_mode,
        }

        planned_direct_civ_sequence = [
            {
                "name": "read_rx_tx_status",
                "documented_command_code": "1C 00",
                "approved_for_future_write_phase": True,
                "phase_8c4_sent": False,
            },
            {
                "name": "read_operating_frequency",
                "documented_command_code": "03",
                "approved_for_future_write_phase": True,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_select_a_band_as_main",
                "documented_command_code": "07 D0",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_write_frequency",
                "documented_command_code": "05",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_select_fm_mode",
                "documented_command_code": "06 05",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_write_duplex",
                "documented_command_code": "10/11/12",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_write_offset",
                "documented_command_code": "0D",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_write_repeater_tone_frequency",
                "documented_command_code": "1B 00",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
            {
                "name": "future_write_tone_setting",
                "documented_command_code": "1A 00",
                "approved_for_future_write_phase": False,
                "phase_8c4_sent": False,
            },
        ]

        safety_flags = {
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "frequency_write_performed": False,
            "mode_write_performed": False,
            "duplex_write_performed": False,
            "offset_write_performed": False,
            "tone_write_performed": False,
            "repeater_tone_write_performed": False,
            "tone_squelch_write_performed": False,
            "dtcs_write_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_a_main_select_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "serial_opened": False,
            "civ_command_sent": False,
            "redis_written_by_adapter": False,
            "ui_bus_written": False,
            "system_bus_written_by_adapter": False,
        }

        common = {
            "action": "side_a_tune_candidate_test",
            "phase": "8C-4",
            **safety_flags,
            "candidate": normalized_candidate,
            "planned_direct_civ_sequence": planned_direct_civ_sequence,
            "dry_run_requested": bool(dry_run),
        }

        if errors:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason="Phase 8C-4 dry-run request contract rejected: " + "; ".join(errors),
                extra={
                    **common,
                    "validation_errors": errors,
                },
            )

        if not dry_run:
            return self._result(
                ok=False,
                status="not_implemented",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason="Phase 8C-4 does not allow real Side-A tune/check writes; no radio command was sent.",
                extra=common,
            )

        return self._result(
            ok=True,
            status="dry_run",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason="Phase 8C-4 dry-run request contract accepted; no radio command was sent.",
            extra=common,
        )

    def direct_civ_side_a_write_plan(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 8C-6 direct CI-V Side-A/Main-band candidate write-plan builder.

        This method is CLI-only and plan-only in Phase 8C-6.

        It may validate a candidate and, when explicitly gated, call the existing
        Phase 8C-5 read-only readiness probe to read current Main-band state.

        It must not:
        - select A band as Main
        - write frequency
        - write mode
        - write duplex/simplex
        - write offset
        - write tone
        - write memory
        - write bank/group
        - start scan
        - program Side B
        - expose PTT/transmit control
        - publish Redis
        - write rt:ui:bus
        - write rt:system:bus
        """

        cfg = self.config
        raw = candidate if isinstance(candidate, dict) else {}

        safety_flags = {
            "read_only": True,
            "plan_only": True,
            "writes_performed": False,
            "frequency_write_performed": False,
            "mode_write_performed": False,
            "duplex_write_performed": False,
            "offset_write_performed": False,
            "tone_write_performed": False,
            "repeater_tone_write_performed": False,
            "tone_squelch_write_performed": False,
            "dtcs_write_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_a_main_select_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "system_bus_written": False,
            "write_commands_sent": False,
        }

        def base_result() -> Dict[str, Any]:
            return {
                "action": "direct_civ_side_a_write_plan",
                "phase": "8C-6",
                "radio": cfg.radio_name,
                "control_path": "direct_civ",
                "serial_port": cfg.direct_civ_serial_port,
                "baud": cfg.direct_civ_baud,
                **safety_flags,
                "ready_for_future_write_phase": False,
                "updated_utc": utc_now(),
            }

        def result_with(
            *,
            status: str,
            ok: bool,
            operation_performed: bool,
            serial_opened: bool,
            civ_command_sent: bool,
            reason: str,
            normalized_candidate: Optional[Dict[str, Any]] = None,
            current: Optional[Dict[str, Any]] = None,
            plan: Optional[list[Dict[str, Any]]] = None,
            summary: Optional[Dict[str, Any]] = None,
            validation_errors: Optional[list[str]] = None,
            readback_summary: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            out = base_result()
            out.update(
                {
                    "status": status,
                    "ok": bool(ok),
                    "operation_performed": bool(operation_performed),
                    "serial_opened": bool(serial_opened),
                    "civ_command_sent": bool(civ_command_sent),
                    "candidate": normalized_candidate,
                    "current": current,
                    "plan": plan or [],
                    "summary": summary
                    or {
                        "readback_ok": False,
                        "rx_not_tx": False,
                        "changes_required": False,
                        "change_count": 0,
                        "ready_for_future_write_phase": False,
                    },
                    "reason": reason,
                    "updated_utc": utc_now(),
                }
            )
            if validation_errors:
                out["validation_errors"] = validation_errors
            if readback_summary is not None:
                out["readback_summary"] = readback_summary
            return out

        errors: list[str] = []

        def candidate_float(
            name: str,
            *,
            required: bool,
            minimum: Optional[float] = None,
            maximum: Optional[float] = None,
        ) -> Optional[float]:
            value = raw.get(name)

            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    errors.append(f"{name} is required")
                return None

            try:
                parsed = float(value)
            except Exception:
                errors.append(f"{name} must be numeric")
                return None

            if minimum is not None and parsed < minimum:
                errors.append(f"{name} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                errors.append(f"{name} must be <= {maximum}")

            return parsed

        def candidate_str(name: str, default: str = "") -> str:
            value = raw.get(name)
            if value is None:
                return default
            return str(value).strip()

        if "__candidate_json_parse_error" in raw:
            errors.append(f"candidate JSON could not be parsed: {raw.get('__candidate_json_parse_error')}")

        frequency_mhz = candidate_float("frequency_mhz", required=True, minimum=118.0, maximum=550.0)

        mode = candidate_str("mode", "FM").upper()
        if mode != "FM":
            errors.append("mode must be FM")

        duplex_raw = candidate_str("duplex", "simplex").lower()
        duplex_aliases = {
            "simplex": "simplex",
            "none": "none",
            "plus": "plus",
            "+": "plus",
            "dup+": "plus",
            "minus": "minus",
            "-": "minus",
            "dup-": "minus",
        }

        duplex = duplex_aliases.get(duplex_raw)
        if duplex is None:
            errors.append("duplex must be one of: simplex, none, plus, minus, dup+, dup-")
            duplex = duplex_raw or "unknown"

        offset_mhz = candidate_float("offset_mhz", required=False, minimum=0.0)
        if offset_mhz is None:
            offset_mhz = 0.0

        tone_hz_present = raw.get("tone_hz") is not None and not (
            isinstance(raw.get("tone_hz"), str) and not str(raw.get("tone_hz")).strip()
        )
        tone_hz = candidate_float("tone_hz", required=False, minimum=50.0, maximum=300.0)

        tone_mode_raw = candidate_str("tone_mode", "none").lower()
        tone_mode_aliases = {
            "": "none",
            "none": "none",
            "off": "none",
            "tone": "tone",
            "ctcss": "tone",
            "tsql": "tsql",
            "tone_sql": "tsql",
            "tone_squelch": "tsql",
            "dtcs": "dtcs",
            "dcs": "dtcs",
        }
        tone_mode = tone_mode_aliases.get(tone_mode_raw)

        if tone_mode is None:
            errors.append("tone_mode must normalize to one of: none, tone, tsql, dtcs")
            tone_mode = tone_mode_raw or "unknown"

        if tone_mode in {"tone", "tsql"} and not tone_hz_present:
            errors.append("tone_hz is required when tone_mode is tone or tsql")

        normalized_candidate = {
            "frequency_mhz": round(float(frequency_mhz), 6) if frequency_mhz is not None else None,
            "mode": mode,
            "duplex": duplex,
            "offset_mhz": round(float(offset_mhz), 6),
            "tone_hz": round(float(tone_hz), 1) if tone_hz is not None else None,
            "tone_mode": tone_mode,
        }

        if errors:
            return result_with(
                status="rejected",
                ok=False,
                operation_performed=False,
                serial_opened=False,
                civ_command_sent=False,
                reason="Phase 8C-6 write-plan candidate rejected before serial open: " + "; ".join(errors),
                normalized_candidate=normalized_candidate,
                current=None,
                plan=[],
                validation_errors=errors,
            )

        if (
            not cfg.direct_civ_enabled
            or not cfg.direct_civ_side_a_readiness_probe_enabled
            or not cfg.direct_civ_side_a_write_plan_enabled
        ):
            gate_failures = []
            if not cfg.direct_civ_enabled:
                gate_failures.append("direct_civ_enabled=false")
            if not cfg.direct_civ_side_a_readiness_probe_enabled:
                gate_failures.append("direct_civ_side_a_readiness_probe_enabled=false")
            if not cfg.direct_civ_side_a_write_plan_enabled:
                gate_failures.append("direct_civ_side_a_write_plan_enabled=false")

            return result_with(
                status="disabled",
                ok=False,
                operation_performed=False,
                serial_opened=False,
                civ_command_sent=False,
                reason="Direct CI-V Side-A write-plan builder disabled by config: " + ", ".join(gate_failures),
                normalized_candidate=None,
                current=None,
                plan=[],
            )

        readiness = self.direct_civ_side_a_readiness_probe(candidate=normalized_candidate)
        commands = readiness.get("commands") if isinstance(readiness.get("commands"), list) else []
        readback_summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}

        by_name = {str(cmd.get("name")): cmd for cmd in commands if isinstance(cmd, dict)}

        def parsed_for(name: str) -> Dict[str, Any]:
            cmd = by_name.get(name, {})
            parsed = cmd.get("parsed") if isinstance(cmd, dict) else {}
            return parsed if isinstance(parsed, dict) else {}

        rx_tx_parsed = parsed_for("rx_tx_status")
        freq_parsed = parsed_for("operating_frequency")
        mode_parsed = parsed_for("operating_mode")
        duplex_parsed = parsed_for("duplex")
        offset_parsed = parsed_for("offset")
        tone_setting_parsed = parsed_for("tone_setting")
        repeater_tone_parsed = parsed_for("repeater_tone_frequency")
        tone_sql_parsed = parsed_for("tone_squelch_frequency")
        dtcs_parsed = parsed_for("dtcs_code_polarity")

        current_offset_raw = offset_parsed.get("offset_raw_bcd_integer")
        current_offset_mhz = None
        if isinstance(current_offset_raw, (int, float)):
            # Observed Phase 8C-5 example: raw BCD integer 6000 represents 0.600 MHz.
            current_offset_mhz = round(float(current_offset_raw) / 10000.0, 6)

        current_tone_setting_code = tone_setting_parsed.get("tone_setting_code_hex")
        current_tone_mode = None
        if isinstance(current_tone_setting_code, str):
            code = current_tone_setting_code.replace(" ", "").upper()
            current_tone_mode = {
                "00": "none",
                "01": "tone",
                "02": "tsql",
                "03": "dtcs",
            }.get(code, "unknown" if code else None)

        current_duplex_raw = duplex_parsed.get("duplex")
        current_duplex = {
            "simplex": "simplex",
            "none": "none",
            "dup-": "minus",
            "minus": "minus",
            "dup+": "plus",
            "plus": "plus",
        }.get(str(current_duplex_raw or "").lower(), current_duplex_raw)

        current: Dict[str, Any] = {
            "rx_not_tx": rx_tx_parsed.get("rx_not_tx"),
            "frequency_mhz": freq_parsed.get("frequency_mhz"),
            "mode": mode_parsed.get("mode"),
            "duplex": current_duplex,
            "offset_raw_bcd_integer": current_offset_raw,
            "offset_mhz_tentative": current_offset_mhz,
            "tone_setting_code_hex": current_tone_setting_code,
            "tone_mode_tentative": current_tone_mode,
            "repeater_tone_hz": repeater_tone_parsed.get("tone_hz"),
            "tone_squelch_hz": tone_sql_parsed.get("tone_hz"),
            "dtcs_code_display": dtcs_parsed.get("dtcs_code_display"),
        }

        plan: list[Dict[str, Any]] = []

        def numeric_changed(current_value: Any, candidate_value: Any, tolerance: float) -> bool:
            if current_value is None:
                return True
            try:
                return abs(float(current_value) - float(candidate_value)) > tolerance
            except Exception:
                return True

        def add_plan(
            *,
            name: str,
            documented_command_code: str,
            required: bool,
            reason: str,
            current_value: Any = None,
            candidate_value: Any = None,
        ) -> None:
            entry: Dict[str, Any] = {
                "name": name,
                "documented_command_code": documented_command_code,
                "would_send": False,
                "required": bool(required),
                "reason": reason,
            }
            if current_value is not None:
                entry["current"] = current_value
            if candidate_value is not None:
                entry["candidate"] = candidate_value
            plan.append(entry)

        current_frequency = current.get("frequency_mhz")
        candidate_frequency = normalized_candidate["frequency_mhz"]
        frequency_required = numeric_changed(current_frequency, candidate_frequency, 0.000005)
        add_plan(
            name="future_write_frequency",
            documented_command_code="05",
            required=frequency_required,
            current_value=current_frequency,
            candidate_value=candidate_frequency,
            reason=(
                "Candidate frequency differs from current readback."
                if frequency_required
                else "Candidate frequency matches current readback."
            ),
        )

        current_mode = str(current.get("mode") or "").upper()
        mode_required = current_mode != normalized_candidate["mode"]
        add_plan(
            name="future_set_fm_mode",
            documented_command_code="06 05",
            required=mode_required,
            current_value=current.get("mode"),
            candidate_value=normalized_candidate["mode"],
            reason=(
                "Candidate mode differs from current readback."
                if mode_required
                else "Candidate mode already matches current readback."
            ),
        )

        candidate_duplex = str(normalized_candidate["duplex"])
        current_duplex_text = str(current.get("duplex") or "")
        duplex_required = current_duplex_text != candidate_duplex

        if candidate_duplex in {"simplex", "none"}:
            duplex_name = "future_set_simplex"
            duplex_code = "0F 10"
        elif candidate_duplex == "minus":
            duplex_name = "future_set_dup_minus"
            duplex_code = "0F 11"
        elif candidate_duplex == "plus":
            duplex_name = "future_set_dup_plus"
            duplex_code = "0F 12"
        else:
            duplex_name = "future_set_duplex_unknown"
            duplex_code = "0F 10/11/12"

        add_plan(
            name=duplex_name,
            documented_command_code=duplex_code,
            required=duplex_required,
            current_value=current.get("duplex"),
            candidate_value=candidate_duplex,
            reason=(
                "Candidate duplex setting differs from current readback."
                if duplex_required
                else "Candidate duplex setting already matches current readback."
            ),
        )

        candidate_offset = normalized_candidate["offset_mhz"]
        offset_required = numeric_changed(current_offset_mhz, candidate_offset, 0.0005)
        add_plan(
            name="future_write_offset",
            documented_command_code="0D",
            required=offset_required,
            current_value=current_offset_mhz,
            candidate_value=candidate_offset,
            reason=(
                "Candidate offset differs from current readback. Current offset interpretation is based on the Phase 8C-5 observed BCD scale."
                if offset_required
                else "Candidate offset appears to match current readback using the Phase 8C-5 observed BCD scale."
            ),
        )

        candidate_tone_mode = str(normalized_candidate["tone_mode"])
        candidate_tone_hz = normalized_candidate["tone_hz"]
        current_repeater_tone_hz = current.get("repeater_tone_hz")

        tone_frequency_required = False
        if candidate_tone_mode in {"tone", "tsql"} and candidate_tone_hz is not None:
            tone_frequency_required = numeric_changed(current_repeater_tone_hz, candidate_tone_hz, 0.05)

        add_plan(
            name="future_write_repeater_tone_frequency",
            documented_command_code="1B 00",
            required=tone_frequency_required,
            current_value=current_repeater_tone_hz,
            candidate_value=candidate_tone_hz,
            reason=(
                "Candidate repeater tone differs from current readback."
                if tone_frequency_required
                else "Repeater tone frequency write is not needed for this candidate, or current readback already matches."
            ),
        )

        tone_setting_required = current_tone_mode != candidate_tone_mode
        add_plan(
            name="future_write_tone_mode",
            documented_command_code="16 42",
            required=tone_setting_required,
            current_value=current_tone_mode,
            candidate_value=candidate_tone_mode,
            reason=(
                "Candidate tone mode differs from current readback; Phase 8C-9 uses supplied IC-2730A sample-program command 16 42 for tone-mode activation, not 1A 00."
                if tone_setting_required
                else "Candidate tone mode already matches current readback."
            ),
        )

        change_count = sum(1 for item in plan if item.get("required") is True)
        changes_required = change_count > 0

        plan.insert(
            0,
            {
                "name": "future_select_a_band_as_main",
                "documented_command_code": "07 D0",
                "would_send": False,
                "required": bool(changes_required),
                "reason": (
                    "Future write phase must explicitly target A band/Main before changing Side-A candidate settings."
                    if changes_required
                    else "No candidate setting changes are required, so A band/Main selection is not required in this plan."
                ),
            },
        )

        if changes_required:
            change_count += 1

        readback_ok = all(
            [
                readback_summary.get("rx_tx_status_ok"),
                readback_summary.get("frequency_ok"),
                readback_summary.get("mode_ok"),
                readback_summary.get("duplex_ok"),
                readback_summary.get("offset_ok"),
                readback_summary.get("tone_setting_ok"),
                readback_summary.get("repeater_tone_frequency_ok"),
            ]
        )

        rx_not_tx = bool(readback_summary.get("rx_not_tx") is True)

        if not readback_ok or not rx_not_tx:
            status = "partial"
            ok = False
            reason = (
                "Direct CI-V Side-A candidate write plan built partially from readback. "
                "No write/control commands were sent."
            )
        elif changes_required:
            status = "planned"
            ok = True
            reason = "Direct CI-V Side-A candidate write plan built. No write/control commands were sent."
        else:
            status = "no_change_needed"
            ok = True
            reason = "Direct CI-V Side-A candidate already matches current readback. No write/control commands were sent."

        summary = {
            "readback_ok": bool(readback_ok),
            "rx_not_tx": bool(rx_not_tx),
            "changes_required": bool(changes_required),
            "change_count": int(change_count),
            "ready_for_future_write_phase": False,
        }

        return result_with(
            status=status,
            ok=ok,
            operation_performed=bool(readiness.get("operation_performed")),
            serial_opened=bool(readiness.get("serial_opened")),
            civ_command_sent=bool(readiness.get("civ_command_sent")),
            reason=reason,
            normalized_candidate=normalized_candidate,
            current=current,
            plan=plan,
            summary=summary,
            readback_summary=readback_summary,
        )

    def _cd_bank_range(self, group: str) -> Optional[tuple[int, int]]:
        bank = self._safe_group(group)
        return IC2730A_CD_BANK_CHANNEL_RANGES.get(bank)

    def _cd_memory_channel(self, group: str, channel: int) -> Optional[int]:
        bank_range = self._cd_bank_range(group)
        if bank_range is None:
            return None

        start, end = bank_range

        try:
            parsed = int(channel)
        except Exception:
            return None

        # Accept either a relative bank slot 0-49 or the absolute IC-2730A memory channel.
        if 0 <= parsed < IC2730A_CD_BANK_SIZE:
            return start + parsed

        if start <= parsed <= end:
            return parsed

        return None

    def _normalize_cd_repeater_candidate(self, repeater: Dict[str, Any]) -> tuple[Dict[str, Any], list[str]]:
        raw = repeater if isinstance(repeater, dict) else {}
        errors: list[str] = []

        def read_str(name: str, default: str = "") -> str:
            value = raw.get(name, default)
            if value is None:
                return default
            return str(value).strip()

        def read_float(
            name: str,
            *,
            required: bool,
            default: Optional[float] = None,
            minimum: Optional[float] = None,
            maximum: Optional[float] = None,
        ) -> Optional[float]:
            value = raw.get(name)

            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    errors.append(f"{name} is required")
                return default

            try:
                parsed = float(value)
            except Exception:
                errors.append(f"{name} must be numeric")
                return default

            if minimum is not None and parsed < minimum:
                errors.append(f"{name} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                errors.append(f"{name} must be <= {maximum}")

            return parsed

        frequency_mhz = read_float(
            "frequency_mhz",
            required=True,
            minimum=118.0,
            maximum=550.0,
        )

        mode = read_str("mode", "FM").upper()
        if mode != "FM":
            errors.append("mode must be FM for IC-2730A repeater memory planning")

        duplex_raw = read_str("duplex", "simplex").lower()
        duplex_aliases = {
            "": "simplex",
            "none": "simplex",
            "off": "simplex",
            "simplex": "simplex",
            "+": "plus",
            "plus": "plus",
            "dup+": "plus",
            "duplex+": "plus",
            "-": "minus",
            "minus": "minus",
            "dup-": "minus",
            "duplex-": "minus",
        }
        duplex = duplex_aliases.get(duplex_raw)
        if duplex is None:
            errors.append("duplex must be one of: simplex, plus, minus, dup+, dup-, +, -")
            duplex = duplex_raw or "unknown"

        offset_mhz = read_float(
            "offset_mhz",
            required=False,
            default=0.0,
            minimum=0.0,
        )
        if offset_mhz is None:
            offset_mhz = 0.0

        tone_hz = read_float(
            "tone_hz",
            required=False,
            default=None,
            minimum=50.0,
            maximum=300.0,
        )

        tone_mode_raw = read_str("tone_mode", "").lower()
        if not tone_mode_raw:
            tone_mode_raw = "tone" if tone_hz is not None else "none"

        tone_mode_aliases = {
            "": "none",
            "none": "none",
            "off": "none",
            "no": "none",
            "tone": "tone",
            "encode": "tone",
            "ctcss": "tone",
            "tsql": "tsql",
            "tone_sql": "tsql",
            "tonesql": "tsql",
            "tone_squelch": "tsql",
        }
        tone_mode = tone_mode_aliases.get(tone_mode_raw)
        if tone_mode is None:
            errors.append("tone_mode must be one of: none, off, tone, encode, ctcss, tsql")
            tone_mode = tone_mode_raw or "unknown"

        if tone_mode in {"tone", "tsql"} and tone_hz is None:
            errors.append("tone_hz is required when tone_mode is tone or tsql")

        label = (
            read_str("label")
            or read_str("callsign")
            or read_str("name")
            or read_str("repeater_id")
            or "RT RPT"
        )

        normalized = {
            "label": label[:16],
            "callsign": read_str("callsign"),
            "name": read_str("name"),
            "frequency_mhz": round(float(frequency_mhz), 6) if frequency_mhz is not None else None,
            "mode": mode,
            "duplex": duplex,
            "offset_mhz": round(float(offset_mhz), 6),
            "tone_hz": round(float(tone_hz), 1) if tone_hz is not None else None,
            "tone_mode": tone_mode,
            "source_id": read_str("id") or read_str("repeater_id"),
            "distance_miles": raw.get("distance_miles"),
            "bearing_degrees": raw.get("bearing_degrees"),
        }

        return normalized, errors

    def _cd_safety_flags(self) -> Dict[str, Any]:
        return {
            "operation_performed": False,
            "serial_opened": False,
            "civ_command_sent": False,
            "writes_performed": False,
            "memory_write_performed": False,
            "memory_clear_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "scan_stop_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "ui_bus_written": False,
            "redis_written_by_adapter": False,
            "system_bus_written_by_adapter": False,
        }

    def _cd_gate_summary(self) -> Dict[str, Any]:
        cfg = self.config
        return {
            "enabled": bool(cfg.enabled),
            "control_mode": cfg.control_mode,
            "direct_civ_enabled": bool(cfg.direct_civ_enabled),
            "writes_enabled": bool(cfg.writes_enabled),
            "memory_programming_enabled": bool(cfg.memory_programming_enabled),
            "memory_clear_enabled": bool(cfg.memory_clear_enabled),
            "scan_control_enabled": bool(cfg.scan_control_enabled),
            "cd_bank_reload_enabled": bool(cfg.cd_bank_reload_enabled),
            "cd_memory_read_proof_enabled": bool(cfg.direct_civ_cd_memory_read_proof_enabled),
            "dry_run_cd_reload": bool(cfg.dry_run_cd_reload),
            "allowed_reload_banks": list(cfg.allowed_reload_banks),
            "max_bank_channels": int(cfg.max_bank_channels),
            "require_rx_not_tx_before_write": bool(cfg.require_rx_not_tx_before_write),
            "require_stop_scan_before_clear": bool(cfg.require_stop_scan_before_clear),
            "require_readback_after_write": bool(cfg.require_readback_after_write),
        }

    def direct_civ_memory_channel_read_proof(self, group: str, channel: int) -> Dict[str, Any]:
        """
        Phase 8.1D-2 gated direct CI-V CMD 08 current-memory-channel read proof.

        Uploaded IC-2730A bank-manager sample says:
        - CMD 0x08 with no data reads the currently selected memory channel.
        - There is no direct read-current-bank command.
        - Bank is derived from current memory channel number.

        This proof sends only:
          08

        It does not select a memory channel and does not send a channel argument.
        """
        cfg = self.config
        expected_bank = self._safe_group(group)

        try:
            expected_requested_channel = int(channel)
        except Exception:
            expected_requested_channel = -1

        expected_absolute_memory_channel = self._cd_memory_channel(
            expected_bank,
            expected_requested_channel,
        )
        expected_bank_range = self._cd_bank_range(expected_bank)

        safety = {
            "read_only": True,
            "writes_performed": False,
            "frequency_write_performed": False,
            "mode_write_performed": False,
            "duplex_write_performed": False,
            "offset_write_performed": False,
            "tone_write_performed": False,
            "memory_write_performed": False,
            "memory_clear_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "scan_stop_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "hamlib_used": False,
            "ui_bus_written": False,
            "redis_written_by_adapter": False,
            "system_bus_written_by_adapter": False,
        }

        def bank_for_channel(memory_channel: Optional[int]) -> Optional[str]:
            if memory_channel is None:
                return None
            all_banks = {
                "A": (0, 49),
                "B": (50, 99),
                "C": (100, 149),
                "D": (150, 199),
                "E": (200, 249),
                "F": (250, 299),
                "G": (300, 349),
                "H": (350, 399),
                "I": (400, 449),
                "J": (450, 499),
            }
            for bank, (start, end) in all_banks.items():
                if start <= int(memory_channel) <= end:
                    return bank
            return None

        def base_extra() -> Dict[str, Any]:
            return {
                "action": "direct_civ_memory_channel_read_proof",
                "phase": "8.1D-2",
                "operation_performed": False,
                "serial_opened": False,
                "civ_command_sent": False,
                "control_path": "direct_civ",
                "target_group": expected_bank,
                "target_channel": expected_requested_channel,
                "expected_absolute_memory_channel": expected_absolute_memory_channel,
                "expected_bank_range": {
                    "start": expected_bank_range[0],
                    "end": expected_bank_range[1],
                } if expected_bank_range else None,
                "documented_command_code": "08",
                "command": {},
                "parsed": {},
                "gate_summary": self._cd_gate_summary(),
                "safety": safety,
            }

        errors: list[str] = []

        if expected_bank not in {"C", "D"}:
            errors.append("target_group must be C or D for this C/D proof")

        if expected_bank not in cfg.allowed_reload_banks:
            errors.append(f"bank {expected_bank!r} is not allowed by config")

        if expected_absolute_memory_channel is None:
            errors.append(
                f"target_channel {expected_requested_channel!r} is not valid for bank {expected_bank}; "
                "use relative slot 0-49 or absolute channel in the bank range"
            )

        if errors:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(cfg.enabled and cfg.control_mode != "disabled"),
                reason="Phase 8.1D-2 CMD 08 current-memory-channel proof rejected: " + "; ".join(errors),
                extra={
                    **base_extra(),
                    "validation_errors": errors,
                },
            )

        gate_failures: list[str] = []

        if not cfg.enabled:
            gate_failures.append("enabled=false")
        if cfg.control_mode == "disabled":
            gate_failures.append("control_mode=disabled")
        if not cfg.direct_civ_enabled:
            gate_failures.append("direct_civ_enabled=false")
        if not cfg.direct_civ_cd_memory_read_proof_enabled:
            gate_failures.append("cd_memory_read_proof_enabled=false")

        if gate_failures:
            return self._result(
                ok=False,
                status="disabled",
                available=bool(cfg.enabled and cfg.control_mode != "disabled"),
                reason="Direct CI-V C/D memory read proof disabled by config: " + ", ".join(gate_failures),
                extra={
                    **base_extra(),
                    "gate_failures": gate_failures,
                },
            )

        controller_addr = _safe_hex_byte(
            cfg.direct_civ_controller_address_hex,
            DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
        )
        transceiver_addr = _safe_hex_byte(
            cfg.direct_civ_transceiver_address_hex,
            DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
        )

        # Uploaded sample: CMD 08 with no argument queries current memory channel.
        payload = bytes([0x08])

        result_extra = base_extra()
        result_extra.update(
            {
                "operation_performed": False,
                "serial_opened": False,
                "civ_command_sent": False,
                "controller_address_hex": f"{controller_addr:02X}",
                "transceiver_address_hex": f"{transceiver_addr:02X}",
                "memory_channel_encoding": {
                    "encoding": "sample_cmd08_no_argument_read_current_channel",
                    "request_payload_hex": "08",
                    "response_payload_format": "08 <ch_hundreds_bcd> <ch_tens_ones_bcd>",
                    "example_response_channel_150": "08 01 50",
                },
                "command": {
                    "name": "cmd08_read_current_memory_channel",
                    "documented_command_code": "08",
                    "payload_hex": "08",
                    "sent": False,
                },
            }
        )

        try:
            import serial  # type: ignore
        except Exception as exc:
            return self._result(
                ok=False,
                status="unavailable",
                available=False,
                reason=f"Python pyserial module is not available; no CI-V command was sent: {exc}",
                extra=result_extra,
            )

        serial_opened = False

        try:
            with serial.Serial(
                port=cfg.direct_civ_serial_port,
                baudrate=cfg.direct_civ_baud,
                timeout=cfg.direct_civ_timeout_seconds,
                write_timeout=cfg.direct_civ_timeout_seconds,
            ) as port:
                serial_opened = True

                command_result = self._direct_civ_send_payload_full_window(
                    port=port,
                    name="cmd08_read_current_memory_channel",
                    payload=payload,
                    controller_addr=controller_addr,
                    transceiver_addr=transceiver_addr,
                    timeout_seconds=cfg.direct_civ_timeout_seconds,
                )

                raw = bytes.fromhex(str(command_result.get("raw_response_hex", "")).replace(" ", ""))
                frames = self._direct_civ_split_frames(raw)
                response_payload = self._direct_civ_find_response_payload(
                    frames=frames,
                    controller_addr=controller_addr,
                    transceiver_addr=transceiver_addr,
                    expected_prefix=bytes([0x08]),
                )

                current_memory_channel = None
                active_bank = None

                if response_payload is not None and len(response_payload) >= 3:
                    current_memory_channel = self._direct_civ_decode_memory_channel_bcd_sample(
                        response_payload[1:3]
                    )
                    active_bank = bank_for_channel(current_memory_channel)

                expected_matches = (
                    current_memory_channel is not None
                    and expected_absolute_memory_channel is not None
                    and int(current_memory_channel) == int(expected_absolute_memory_channel)
                )

                parsed = {
                    "response_payload_hex": self._direct_civ_hex_bytes(response_payload or b""),
                    "matching_cmd08_response_found": response_payload is not None,
                    "current_memory_channel": current_memory_channel,
                    "active_bank": active_bank,
                    "expected_memory_channel": expected_absolute_memory_channel,
                    "expected_bank": expected_bank,
                    "expected_matches_current": expected_matches,
                    "ok_ng_status": command_result.get("response_status", {}),
                    "parse_confidence": (
                        "sample_cmd08_bcd_channel"
                        if current_memory_channel is not None
                        else "raw_only"
                    ),
                }

                ok = current_memory_channel is not None
                status = "ok" if ok else "partial"

                result_extra.update(
                    {
                        "operation_performed": True,
                        "serial_opened": True,
                        "civ_command_sent": bool(command_result.get("sent")),
                        "command": command_result,
                        "parsed": parsed,
                        "active_group": active_bank,
                        "active_memory_channel": current_memory_channel,
                        "expected_matches_current": expected_matches,
                        "safety": safety,
                    }
                )

                return self._result(
                    ok=ok,
                    status=status,
                    available=True,
                    reason=(
                        "Phase 8.1D-2 CMD 08 current-memory-channel proof completed; no clear/write/scan command was sent."
                        if ok
                        else "Phase 8.1D-2 CMD 08 command was sent, but current memory channel could not be decoded; see command.raw_response_hex."
                    ),
                    extra=result_extra,
                )

        except Exception as exc:
            result_extra.update(
                {
                    "operation_performed": bool(serial_opened),
                    "serial_opened": bool(serial_opened),
                    "civ_command_sent": False,
                    "safety": safety,
                }
            )
            return self._result(
                ok=False,
                status="error" if serial_opened else "unavailable",
                available=False,
                reason=f"Phase 8.1D-2 CMD 08 current-memory-channel proof failed: {exc}",
                extra=result_extra,
            )

    def plan_clear_bank(self, group: str) -> Dict[str, Any]:
        """
        Phase 8.1C dry-run/no-command bank clear plan.

        Uses uploaded sample reference:
        - C = 100-149
        - D = 150-199
        - CMD 0B clears a memory channel

        No serial open. No CI-V command sent.
        """

        bank = self._safe_group(group)
        bank_range = self._cd_bank_range(bank)
        safety = self._cd_safety_flags()

        if bank_range is None or bank not in self.config.allowed_reload_banks:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason=f"Bank {bank!r} is not allowed for C/D reload planning.",
                extra={
                    "action": "plan_clear_bank",
                    "phase": "8.1C",
                    **safety,
                    "target_group": bank,
                    "gate_summary": self._cd_gate_summary(),
                    "commands": [],
                },
            )

        start, end = bank_range
        commands = [
            {
                "name": "future_clear_memory_channel",
                "documented_command_code": "0B",
                "memory_channel": memory_channel,
                "sent": False,
            }
            for memory_channel in range(start, end + 1)
        ]

        return self._result(
            ok=True,
            status="planned",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason=f"Phase 8.1C dry-run clear plan built for bank {bank}; no CI-V command was sent.",
            extra={
                "action": "plan_clear_bank",
                "phase": "8.1C",
                **safety,
                "target_group": bank,
                "memory_channel_start": start,
                "memory_channel_end": end,
                "planned_clear_count": len(commands),
                "gate_summary": self._cd_gate_summary(),
                "commands": commands,
            },
        )

    def plan_program_channel(self, group: str, channel: int, repeater: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 8.1C dry-run/no-command single memory-channel program plan.

        Uses uploaded sample reference:
        - CMD 08 read/select memory channel
        - CMD 0A write current VFO state to memory channel
        - Frequency/mode/duplex/offset/tone/tone-mode sequence occurs before 0A

        No serial open. No CI-V command sent.
        """

        bank = self._safe_group(group)
        memory_channel = self._cd_memory_channel(bank, channel)
        normalized, errors = self._normalize_cd_repeater_candidate(repeater)
        safety = self._cd_safety_flags()

        if memory_channel is None:
            errors.append(f"channel {channel!r} is not valid for bank {bank}")

        if bank not in self.config.allowed_reload_banks:
            errors.append(f"bank {bank!r} is not allowed by config")

        commands: list[Dict[str, Any]] = []

        if not errors:
            duplex = normalized["duplex"]
            if duplex == "simplex":
                duplex_code = "0F 10"
            elif duplex == "minus":
                duplex_code = "0F 11"
            elif duplex == "plus":
                duplex_code = "0F 12"
            else:
                duplex_code = "0F 10/11/12"

            commands = [
                {
                    "name": "future_select_a_band_as_main",
                    "documented_command_code": "07 D0",
                    "sent": False,
                },
                {
                    "name": "future_write_operating_frequency",
                    "documented_command_code": "05",
                    "frequency_mhz": normalized["frequency_mhz"],
                    "sent": False,
                },
                {
                    "name": "future_set_fm_mode",
                    "documented_command_code": "06 05",
                    "mode": normalized["mode"],
                    "sent": False,
                },
                {
                    "name": "future_write_duplex",
                    "documented_command_code": duplex_code,
                    "duplex": normalized["duplex"],
                    "sent": False,
                },
                {
                    "name": "future_write_offset",
                    "documented_command_code": "0D",
                    "offset_mhz": normalized["offset_mhz"],
                    "sent": False,
                },
            ]

            if normalized["tone_mode"] in {"tone", "tsql"}:
                commands.append(
                    {
                        "name": "future_write_repeater_tone_frequency",
                        "documented_command_code": "1B 00",
                        "tone_hz": normalized["tone_hz"],
                        "sent": False,
                    }
                )

            commands.append(
                {
                    "name": "future_write_tone_mode",
                    "documented_command_code": "16 42",
                    "tone_mode": normalized["tone_mode"],
                    "sent": False,
                }
            )

            commands.append(
                {
                    "name": "future_write_vfo_state_to_memory_channel",
                    "documented_command_code": "0A",
                    "memory_channel": memory_channel,
                    "sent": False,
                }
            )

        if errors:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason="Phase 8.1C program-channel plan rejected: " + "; ".join(errors),
                extra={
                    "action": "plan_program_channel",
                    "phase": "8.1C",
                    **safety,
                    "target_group": bank,
                    "target_channel": channel,
                    "memory_channel": memory_channel,
                    "candidate": normalized,
                    "validation_errors": errors,
                    "gate_summary": self._cd_gate_summary(),
                    "commands": commands,
                },
            )

        return self._result(
            ok=True,
            status="planned",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason=f"Phase 8.1C dry-run program plan built for bank {bank} memory channel {memory_channel}; no CI-V command was sent.",
            extra={
                "action": "plan_program_channel",
                "phase": "8.1C",
                **safety,
                "target_group": bank,
                "target_channel": channel,
                "memory_channel": memory_channel,
                "candidate": normalized,
                "gate_summary": self._cd_gate_summary(),
                "commands": commands,
            },
        )

    def plan_load_bank(
        self,
        group: str,
        repeaters: list[Dict[str, Any]],
        *,
        start_scan_after: bool = False,
    ) -> Dict[str, Any]:
        """
        Phase 8.1C dry-run/no-command full C/D bank reload plan.

        Controller still owns the decision to reload and which bank is inactive.
        Adapter owns the radio-specific channel mapping and future CI-V command plan.
        """

        bank = self._safe_group(group)
        bank_range = self._cd_bank_range(bank)
        safety = self._cd_safety_flags()

        raw_repeaters = repeaters if isinstance(repeaters, list) else []
        errors: list[str] = []

        if bank_range is None:
            errors.append(f"target_group must be C or D, got {bank!r}")

        if bank not in self.config.allowed_reload_banks:
            errors.append(f"bank {bank!r} is not allowed by config")

        if len(raw_repeaters) > int(self.config.max_bank_channels):
            errors.append(
                f"repeaters list has {len(raw_repeaters)} entries but max_bank_channels is {self.config.max_bank_channels}"
            )

        if len(raw_repeaters) > IC2730A_CD_BANK_SIZE:
            errors.append(
                f"repeaters list has {len(raw_repeaters)} entries but IC-2730A C/D bank size is {IC2730A_CD_BANK_SIZE}"
            )

        start = None
        end = None
        if bank_range is not None:
            start, end = bank_range

        channel_plans: list[Dict[str, Any]] = []

        if not errors and start is not None:
            for index, repeater in enumerate(raw_repeaters):
                memory_channel = start + index
                normalized, candidate_errors = self._normalize_cd_repeater_candidate(
                    repeater if isinstance(repeater, dict) else {}
                )

                channel_plans.append(
                    {
                        "slot_index": index,
                        "memory_channel": memory_channel,
                        "candidate": normalized,
                        "valid": not bool(candidate_errors),
                        "validation_errors": candidate_errors,
                        "future_write_command": {
                            "name": "future_write_vfo_state_to_memory_channel",
                            "documented_command_code": "0A",
                            "sent": False,
                        },
                    }
                )

                if candidate_errors:
                    errors.append(f"slot {index} memory {memory_channel}: " + "; ".join(candidate_errors))

        commands: list[Dict[str, Any]] = []
        if start is not None and end is not None:
            commands.append(
                {
                    "name": "future_stop_scan_before_clear_if_required",
                    "documented_command_code": "0E 00",
                    "required_by_config": bool(self.config.require_stop_scan_before_clear),
                    "sent": False,
                }
            )

            commands.append(
                {
                    "name": "future_clear_bank",
                    "documented_command_code": "0B",
                    "memory_channel_start": start,
                    "memory_channel_end": end,
                    "count": IC2730A_CD_BANK_SIZE,
                    "sent": False,
                }
            )

            for item in channel_plans:
                commands.append(
                    {
                        "name": "future_program_memory_channel",
                        "documented_command_code": "0A",
                        "memory_channel": item["memory_channel"],
                        "label": item["candidate"].get("label"),
                        "sent": False,
                    }
                )

            if raw_repeaters:
                commands.append(
                    {
                        "name": "future_select_first_loaded_memory_channel",
                        "documented_command_code": "08",
                        "memory_channel": start,
                        "sent": False,
                    }
                )

            if start_scan_after:
                commands.append(
                    {
                        "name": "future_start_memory_bank_scan",
                        "documented_command_code": "0E 22",
                        "target_group": bank,
                        "sent": False,
                    }
                )

        rejected_count = sum(
            1
            for item in channel_plans
            if isinstance(item, dict) and not bool(item.get("valid"))
        )

        if errors:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason="Planning only rejected before any radio operation: " + "; ".join(errors[:5]),
                extra={
                    "action": "plan_cd_bank_reload",
                    "phase": "8.1D",
                    **safety,
                    "target_group": bank,
                    "requested_count": len(raw_repeaters),
                    "planned_count": 0,
                    "planned_load_count": 0,
                    "rejected_count": max(rejected_count, len(errors)),
                    "memory_channel_start": start,
                    "memory_channel_end": end,
                    "would_clear_bank": bool(start is not None and end is not None),
                    "would_program_channels": False,
                    "would_start_scan_after": bool(start_scan_after),
                    "start_scan_after": bool(start_scan_after),
                    "gate_summary": self._cd_gate_summary(),
                    "channel_plans": channel_plans,
                    "commands": commands,
                    "validation_errors": errors,
                },
            )

        planned_count = len(channel_plans)

        return self._result(
            ok=True,
            status="planned",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason="Planning only; no radio operation performed.",
            extra={
                "action": "plan_cd_bank_reload",
                "phase": "8.1D",
                **safety,
                "target_group": bank,
                "requested_count": len(raw_repeaters),
                "planned_count": planned_count,
                "planned_load_count": planned_count,
                "rejected_count": 0,
                "memory_channel_start": start,
                "memory_channel_end": end,
                "would_clear_bank": True,
                "would_program_channels": planned_count > 0,
                "would_start_scan_after": bool(start_scan_after),
                "start_scan_after": bool(start_scan_after),
                "gate_summary": self._cd_gate_summary(),
                "channel_plans": channel_plans,
                "commands": commands,
                "summary": {
                    "controller_owns_reload_decision": True,
                    "adapter_owns_civ_bytes": True,
                    "ui_renderer_only": True,
                    "ready_for_real_gated_clear_load": False,
                },
            },
        )
    
    def plan_start_memory_bank_scan(self, group: str) -> Dict[str, Any]:
        bank = self._safe_group(group)
        bank_range = self._cd_bank_range(bank)
        safety = self._cd_safety_flags()

        if bank_range is None or bank not in self.config.allowed_reload_banks:
            return self._result(
                ok=False,
                status="rejected",
                available=bool(self.config.enabled and self.config.control_mode != "disabled"),
                reason=f"Bank {bank!r} is not allowed for memory bank scan planning.",
                extra={
                    "action": "plan_start_memory_bank_scan",
                    "phase": "8.1C",
                    **safety,
                    "target_group": bank,
                    "gate_summary": self._cd_gate_summary(),
                    "commands": [],
                },
            )

        start, _end = bank_range

        return self._result(
            ok=True,
            status="planned",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason=f"Phase 8.1C memory bank scan start plan built for bank {bank}; no CI-V command was sent.",
            extra={
                "action": "plan_start_memory_bank_scan",
                "phase": "8.1C",
                **safety,
                "target_group": bank,
                "gate_summary": self._cd_gate_summary(),
                "commands": [
                    {
                        "name": "future_select_first_memory_channel_in_bank",
                        "documented_command_code": "08",
                        "memory_channel": start,
                        "sent": False,
                    },
                    {
                        "name": "future_start_memory_bank_scan",
                        "documented_command_code": "0E 22",
                        "sent": False,
                    },
                ],
            },
        )

    def query_active_bank(self) -> Dict[str, Any]:
        """
        Phase 8.1C no-command active-bank query contract.

        Future implementation should read/select current memory channel with CMD 08
        and derive C/D from the absolute memory channel number.
        """

        return self._result(
            ok=True,
            status="dry_run",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason="Phase 8.1C active-bank query contract built; no CI-V command was sent.",
            extra={
                "action": "query_active_bank",
                "phase": "8.1C",
                **self._cd_safety_flags(),
                "active_group": None,
                "active_memory_channel": None,
                "planned_read_command": {
                    "name": "future_read_active_memory_channel",
                    "documented_command_code": "08",
                    "sent": False,
                },
                "bank_mapping": {
                    "C": {"start": 100, "end": 149},
                    "D": {"start": 150, "end": 199},
                },
                "gate_summary": self._cd_gate_summary(),
            },
        )

    def query_active_memory_data(self) -> Dict[str, Any]:
        """
        Phase 8.1C no-command active-memory-data contract.

        Future implementation should read the active memory channel and publish a
        controller-owned VHF active memory model after successful load/readback.
        """

        return self._result(
            ok=True,
            status="dry_run",
            available=bool(self.config.enabled and self.config.control_mode != "disabled"),
            reason="Phase 8.1C active-memory-data query contract built; no CI-V command was sent.",
            extra={
                "action": "query_active_memory_data",
                "phase": "8.1C",
                **self._cd_safety_flags(),
                "active_memory": {
                    "status": "unknown",
                    "active_group": None,
                    "active_memory_channel": None,
                    "items": [],
                },
                "planned_read_commands": [
                    {
                        "name": "future_read_active_memory_channel",
                        "documented_command_code": "08",
                        "sent": False,
                    }
                ],
                "gate_summary": self._cd_gate_summary(),
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

    def direct_civ_readonly_probe(self) -> Dict[str, Any]:
        """
        Phase 8C-3 direct CI-V read-only probe.

        This manual CLI-only probe:
        - requires explicit config gates
        - opens the configured serial port only after gates pass
        - sends only documented IC-2730A/IC-2730E read-only commands
        - does not publish Redis
        - does not write rt:ui:bus
        - does not write rt:system:bus
        - does not use Hamlib or rigctl
        - does not write frequency, mode, duplex, offset, tone, memory, bank/group, scan, Side B, PTT, or transmit
        """

        cfg = self.config

        disabled_result = {
            "action": "direct_civ_readonly_probe",
            "phase": "8C-3",
            "status": "disabled",
            "ok": False,
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "commands": [],
            "summary": {
                "transceiver_id_ok": False,
                "frequency_ok": False,
                "mode_ok": False,
                "duplex_ok": False,
                "offset_ok": False,
                "rx_tx_status_ok": False,
                "rx_not_tx": False,
            },
            "reason": "Direct CI-V read-only probe disabled by config.",
            "updated_utc": utc_now(),
        }

        if not cfg.direct_civ_enabled or not cfg.direct_civ_readonly_probe_enabled:
            return disabled_result

        controller_addr = _safe_hex_byte(
            cfg.direct_civ_controller_address_hex,
            DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
        )
        transceiver_addr = _safe_hex_byte(
            cfg.direct_civ_transceiver_address_hex,
            DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
        )

        result: Dict[str, Any] = {
            "action": "direct_civ_readonly_probe",
            "phase": "8C-3",
            "status": "starting",
            "ok": False,
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "controller_address_hex": f"{controller_addr:02X}",
            "transceiver_address_hex": f"{transceiver_addr:02X}",
            "commands": [],
            "summary": {
                "transceiver_id_ok": False,
                "frequency_ok": False,
                "mode_ok": False,
                "duplex_ok": False,
                "offset_ok": False,
                "rx_tx_status_ok": False,
                "rx_not_tx": False,
            },
            "reason": "",
            "updated_utc": utc_now(),
        }

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "unavailable",
                    "ok": False,
                    "operation_performed": False,
                    "reason": "Python pyserial module is not available; no CI-V command was sent.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        port = None

        try:
            port = serial.Serial(
                port=cfg.direct_civ_serial_port,
                baudrate=cfg.direct_civ_baud,
                timeout=cfg.direct_civ_timeout_seconds,
                write_timeout=cfg.direct_civ_timeout_seconds,
            )

            result["operation_performed"] = True

            for command_name in cfg.direct_civ_readonly_probe_commands:
                command_result = self._direct_civ_send_readonly_command(
                    port=port,
                    name=command_name,
                    controller_addr=controller_addr,
                    transceiver_addr=transceiver_addr,
                    timeout_seconds=cfg.direct_civ_timeout_seconds,
                )
                result["commands"].append(command_result)

            summary = self._direct_civ_probe_summary(result["commands"])
            result["summary"] = summary
            result["status"] = "ok" if all(
                [
                    summary.get("transceiver_id_ok"),
                    summary.get("frequency_ok"),
                    summary.get("mode_ok"),
                    summary.get("duplex_ok"),
                    summary.get("offset_ok"),
                    summary.get("rx_tx_status_ok"),
                ]
            ) else "partial"

            result["ok"] = bool(result["commands"]) and any(bool(cmd.get("ok")) for cmd in result["commands"])
            result["reason"] = (
                "Direct CI-V read-only probe completed. No write/control commands were included."
            )
            result["updated_utc"] = utc_now()
            return result

        except Exception as exc:
            result.update(
                {
                    "status": "error",
                    "ok": False,
                    "reason": "Direct CI-V read-only probe failed.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass

    def direct_civ_readonly_tone_probe(self) -> Dict[str, Any]:
        """
        Phase 8C-3A direct CI-V tone readback probe.

        This manual CLI-only probe:
        - requires explicit direct CI-V and tone-probe config gates
        - opens the configured serial port only after gates pass
        - sends only documented IC-2730A/IC-2730E tone-related readback command frames
        - sends no tone value bytes, DTCS value bytes, polarity bytes, or write-shaped payloads
        - does not publish Redis
        - does not write rt:ui:bus
        - does not write rt:system:bus
        - does not use Hamlib or rigctl
        - does not write frequency, mode, duplex, offset, tone, memory, bank/group, scan, Side B, PTT, or transmit
        """

        cfg = self.config

        disabled_result = {
            "action": "direct_civ_readonly_tone_probe",
            "phase": "8C-3A",
            "status": "disabled",
            "ok": False,
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "tone_write_performed": False,
            "repeater_tone_write_performed": False,
            "tone_squelch_write_performed": False,
            "dtcs_write_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "system_bus_written": False,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "commands": [],
            "summary": {
                "tone_setting_ok": False,
                "repeater_tone_frequency_ok": False,
                "tone_squelch_frequency_ok": False,
                "dtcs_code_polarity_ok": False,
            },
            "reason": "Direct CI-V tone read-only probe disabled by config.",
            "updated_utc": utc_now(),
        }

        if not cfg.direct_civ_enabled or not cfg.direct_civ_tone_readonly_probe_enabled:
            return disabled_result

        controller_addr = _safe_hex_byte(
            cfg.direct_civ_controller_address_hex,
            DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
        )
        transceiver_addr = _safe_hex_byte(
            cfg.direct_civ_transceiver_address_hex,
            DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
        )

        result: Dict[str, Any] = {
            "action": "direct_civ_readonly_tone_probe",
            "phase": "8C-3A",
            "status": "starting",
            "ok": False,
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "tone_write_performed": False,
            "repeater_tone_write_performed": False,
            "tone_squelch_write_performed": False,
            "dtcs_write_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "system_bus_written": False,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "controller_address_hex": f"{controller_addr:02X}",
            "transceiver_address_hex": f"{transceiver_addr:02X}",
            "commands": [],
            "summary": {
                "tone_setting_ok": False,
                "repeater_tone_frequency_ok": False,
                "tone_squelch_frequency_ok": False,
                "dtcs_code_polarity_ok": False,
            },
            "reason": "",
            "updated_utc": utc_now(),
        }

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "unavailable",
                    "ok": False,
                    "operation_performed": False,
                    "reason": "Python pyserial module is not available; no CI-V tone command was sent.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        port = None

        try:
            port = serial.Serial(
                port=cfg.direct_civ_serial_port,
                baudrate=cfg.direct_civ_baud,
                timeout=cfg.direct_civ_timeout_seconds,
                write_timeout=cfg.direct_civ_timeout_seconds,
            )

            result["operation_performed"] = True

            for command_name in cfg.direct_civ_readonly_tone_probe_commands:
                command_result = self._direct_civ_send_readonly_tone_command(
                    port=port,
                    name=command_name,
                    controller_addr=controller_addr,
                    transceiver_addr=transceiver_addr,
                    timeout_seconds=cfg.direct_civ_timeout_seconds,
                )
                result["commands"].append(command_result)

            summary = self._direct_civ_tone_probe_summary(result["commands"])
            result["summary"] = summary
            result["status"] = "ok" if all(
                [
                    summary.get("tone_setting_ok"),
                    summary.get("repeater_tone_frequency_ok"),
                    summary.get("tone_squelch_frequency_ok"),
                    summary.get("dtcs_code_polarity_ok"),
                ]
            ) else "partial"

            result["ok"] = bool(result["commands"]) and any(bool(cmd.get("ok")) for cmd in result["commands"])
            result["reason"] = (
                "Direct CI-V tone read-only probe completed. "
                "No tone write/control payload values were included."
            )
            result["updated_utc"] = utc_now()
            return result

        except Exception as exc:
            result.update(
                {
                    "status": "error",
                    "ok": False,
                    "reason": "Direct CI-V tone read-only probe failed.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass

    def direct_civ_side_a_real_tune_test(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 8C-7 first real direct CI-V Side-A/Main-band tune test.

        Manual CLI-only.
        No Redis writes.
        No UI bus writes.
        No system bus writes.
        No Hamlib.
        No rigctl.
        No memory programming.
        No scan control.
        No Side B programming.
        No PTT/transmit controls.
        """

        def safety_model() -> Dict[str, Any]:
            return {
                "read_only": False,
                "manual_cli_only": True,
                "ptt_or_transmit_control_added": False,
                "memory_write_performed": False,
                "bank_write_performed": False,
                "scan_start_performed": False,
                "side_b_programming_performed": False,
                "redis_written": False,
                "ui_bus_written": False,
                "system_bus_written": False,
                "rigctl_used": False,
            }

        def base_result(status: str = "aborted", ok: bool = False, reason: str = "") -> Dict[str, Any]:
            return {
                "action": "direct_civ_side_a_real_tune_test",
                "phase": "8C-7",
                "status": status,
                "ok": bool(ok),
                "reason": reason,
                "candidate": candidate if isinstance(candidate, dict) else {},
                "before": {},
                "plan": [],
                "commands_sent": [],
                "after": {},
                "summary": {
                    "readback_before_ok": False,
                    "rx_not_tx_before": False,
                    "writes_attempted": False,
                    "write_count": 0,
                    "readback_after_ok": False,
                    "frequency_matches": False,
                    "mode_matches": False,
                    "duplex_matches": False,
                    "offset_matches": False,
                    "tone_mode_matches": False,
                    "ready_for_future_automation": False,
                },
                "safety": safety_model(),
            }

        def candidate_float(
            raw: Dict[str, Any],
            name: str,
            *,
            required: bool,
            minimum: Optional[float] = None,
            maximum: Optional[float] = None,
        ) -> tuple[Optional[float], list[str]]:
            errors: list[str] = []
            value = raw.get(name)
            if value is None:
                if required:
                    errors.append(f"{name} is required")
                return None, errors
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                errors.append(f"{name} must be numeric")
                return None, errors
            if minimum is not None and parsed < minimum:
                errors.append(f"{name} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                errors.append(f"{name} must be <= {maximum}")
            return parsed, errors

        def candidate_str(raw: Dict[str, Any], name: str, default: str = "") -> str:
            value = raw.get(name, default)
            if value is None:
                return default
            return str(value).strip()

        def normalize_candidate(raw_candidate: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], list[str]]:
            raw = raw_candidate if isinstance(raw_candidate, dict) else {}
            errors: list[str] = []

            if "__candidate_json_parse_error" in raw:
                errors.append(f"candidate JSON could not be parsed: {raw.get('__candidate_json_parse_error')}")

            frequency_mhz, frequency_errors = candidate_float(
                raw, "frequency_mhz", required=True, minimum=118.0, maximum=550.0
            )
            errors.extend(frequency_errors)

            mode = candidate_str(raw, "mode", "FM").upper()
            if mode != "FM":
                errors.append("mode must be FM")

            duplex_raw = candidate_str(raw, "duplex", "simplex").lower()
            duplex_aliases = {
                "simplex": "simplex",
                "none": "simplex",
                "off": "simplex",
                "plus": "plus",
                "+": "plus",
                "dup+": "plus",
                "duplex+": "plus",
                "minus": "minus",
                "-": "minus",
                "dup-": "minus",
                "duplex-": "minus",
            }
            duplex = duplex_aliases.get(duplex_raw)
            if duplex is None:
                errors.append("duplex must be one of: simplex, plus, minus, dup+, dup-, none")
                duplex = duplex_raw or "unknown"

            offset_mhz, offset_errors = candidate_float(
                raw, "offset_mhz", required=False, minimum=0.0
            )
            errors.extend(offset_errors)
            if offset_mhz is None:
                offset_mhz = 0.0

            tone_hz, tone_errors = candidate_float(
                raw, "tone_hz", required=False, minimum=50.0, maximum=300.0
            )
            errors.extend(tone_errors)

            tone_mode_raw = candidate_str(raw, "tone_mode", "none").lower()
            tone_mode_aliases = {
                "none": "none",
                "off": "none",
                "no": "none",
                "": "none",
                "tone": "tone",
                "encode": "tone",
                "tsql": "tsql",
                "tone_sql": "tsql",
                "tonesql": "tsql",
            }
            tone_mode = tone_mode_aliases.get(tone_mode_raw)
            if tone_mode is None:
                errors.append("tone_mode must be one of: none, off, tone, tsql")
                tone_mode = tone_mode_raw or "unknown"

            if tone_mode in {"tone", "tsql"} and tone_hz is None:
                errors.append("tone_hz is required when tone_mode is tone or tsql")
            if tone_mode == "none" and tone_hz is not None:
                errors.append("tone_hz must be null when tone_mode is none")

            normalized = {
                "frequency_mhz": round(float(frequency_mhz or 0.0), 6),
                "mode": mode,
                "duplex": duplex,
                "offset_mhz": round(float(offset_mhz or 0.0), 6),
                "tone_hz": None if tone_hz is None else round(float(tone_hz), 1),
                "tone_mode": tone_mode,
            }

            # Phase 8C-7 is intentionally limited to the first preferred test only.
            if abs(float(normalized["frequency_mhz"]) - 146.520) > 0.000005:
                errors.append("Phase 8C-7 real tune test only allows frequency_mhz 146.520")
            if normalized["mode"] != "FM":
                errors.append("Phase 8C-7 real tune test only allows FM")
            if normalized["duplex"] != "simplex":
                errors.append("Phase 8C-7 real tune test only allows simplex")
            if abs(float(normalized["offset_mhz"])) > 0.000005:
                errors.append("Phase 8C-7 real tune test only allows offset_mhz 0.0")
            if normalized["tone_mode"] != "none":
                errors.append("Phase 8C-7 real tune test only allows tone_mode none")
            if normalized["tone_hz"] is not None:
                errors.append("Phase 8C-7 real tune test only allows tone_hz null")

            return normalized, errors

        def mhz_to_icom_frequency_bcd(frequency_mhz: float) -> bytes:
            # Icom CI-V operating frequency payload is 5 BCD bytes, least-significant pair first.
            hz = int(round(float(frequency_mhz) * 1_000_000.0))
            digits = f"{hz:010d}"
            out = bytearray()
            for idx in range(8, -1, -2):
                lo = int(digits[idx + 1])
                hi = int(digits[idx])
                out.append((hi << 4) | lo)
            return bytes(out)

        def zero_offset_bcd() -> bytes:
            # Phase 8C-7 permits offset write only for 0.0 MHz.
            # The zero value is represented as all-zero BCD data.
            return b"\x00\x00\x00"

        def hex_bytes(data: bytes) -> str:
            return " ".join(f"{b:02X}" for b in data)

        def frame_for(payload: bytes) -> bytes:
            to_addr = int(str(cfg.direct_civ_transceiver_address_hex), 16)
            from_addr = int(str(cfg.direct_civ_controller_address_hex), 16)
            return bytes([0xFE, 0xFE, to_addr, from_addr]) + bytes(payload) + b"\xFD"

        def parse_frames(raw: bytes) -> list[bytes]:
            frames: list[bytes] = []
            start = 0
            while True:
                try:
                    fe = raw.index(b"\xFE\xFE", start)
                    fd = raw.index(b"\xFD", fe)
                except ValueError:
                    break
                frames.append(raw[fe:fd + 1])
                start = fd + 1
            return frames

        def response_status(raw: bytes) -> tuple[str, str]:
            frames = parse_frames(raw)
            for frame in frames:
                if b"\xFB" in frame:
                    return "ok", "CI-V OK response received."
                if b"\xFA" in frame:
                    return "ng", "CI-V NG response received."
            if frames:
                return "unknown", "CI-V response frame received, but no OK/NG byte was found."
            return "unknown", "No complete CI-V response frame was parsed."

        def send_control_command(ser: Any, name: str, payload: bytes) -> Dict[str, Any]:
            import time

            frame = frame_for(payload)
            command_result: Dict[str, Any] = {
                "name": name,
                "payload_hex": hex_bytes(payload),
                "frame_hex": hex_bytes(frame),
                "response_hex": "",
                "status": "unknown",
                "ok": False,
                "reason": "",
            }

            try:
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                try:
                    ser.reset_output_buffer()
                except Exception:
                    pass

                ser.write(frame)
                ser.flush()

                deadline = time.monotonic() + float(cfg.direct_civ_timeout_seconds)
                raw = bytearray()
                while time.monotonic() < deadline:
                    chunk = ser.read(1)
                    if chunk:
                        raw.extend(chunk)
                        if raw.endswith(b"\xFD") and len(raw) >= 6:
                            # Continue briefly in case the adapter echoes then returns OK.
                            if b"\xFB" in raw or b"\xFA" in raw:
                                break
                    else:
                        time.sleep(0.02)

                command_result["response_hex"] = hex_bytes(bytes(raw))
                status, reason = response_status(bytes(raw))
                command_result["status"] = status
                command_result["ok"] = status == "ok"
                command_result["reason"] = reason
                return command_result
            except Exception as exc:
                command_result["status"] = "error"
                command_result["ok"] = False
                command_result["reason"] = f"CI-V control command failed: {exc}"
                return command_result

        def summary_value(summary: Dict[str, Any], *names: str) -> Any:
            for name in names:
                if name in summary:
                    return summary.get(name)
            return None

        cfg = self.config
        result = base_result()

        normalized_candidate, validation_errors = normalize_candidate(candidate if isinstance(candidate, dict) else {})
        result["candidate"] = normalized_candidate if normalized_candidate is not None else {}

        if validation_errors:
            result.update(
                {
                    "status": "rejected",
                    "ok": False,
                    "reason": "Phase 8C-7 real tune candidate rejected before serial open: "
                    + "; ".join(validation_errors),
                }
            )
            return result

        gate_failures: list[str] = []
        if not cfg.direct_civ_enabled:
            gate_failures.append("direct_civ_enabled=false")
        if not cfg.direct_civ_side_a_readiness_probe_enabled:
            gate_failures.append("direct_civ_side_a_readiness_probe_enabled=false")
        if not cfg.direct_civ_side_a_write_plan_enabled:
            gate_failures.append("direct_civ_side_a_write_plan_enabled=false")
        if not cfg.direct_civ_side_a_real_tune_test_enabled:
            gate_failures.append("direct_civ_side_a_real_tune_test_enabled=false")

        if gate_failures:
            result.update(
                {
                    "status": "disabled",
                    "ok": False,
                    "reason": "Direct CI-V Side-A real tune test disabled by config: "
                    + ", ".join(gate_failures),
                }
            )
            return result

        plan_result = self.direct_civ_side_a_write_plan(normalized_candidate)
        result["before"] = {
            "write_plan_status": plan_result.get("status"),
            "write_plan_ok": plan_result.get("ok"),
            "readback_summary": plan_result.get("readback_summary", {}),
        }
        result["plan"] = plan_result.get("plan", []) if isinstance(plan_result.get("plan"), list) else []

        before_summary = result["before"].get("readback_summary", {})
        readback_before_ok = bool(plan_result.get("summary", {}).get("readback_ok"))
        if not readback_before_ok:
            readback_before_ok = bool(
                before_summary.get("rx_tx_status_ok")
                and before_summary.get("frequency_ok")
                and before_summary.get("mode_ok")
                and before_summary.get("duplex_ok")
                and before_summary.get("offset_ok")
                and before_summary.get("tone_setting_ok")
            )

        rx_not_tx_before = bool(before_summary.get("rx_not_tx") is True)

        result["summary"]["readback_before_ok"] = bool(readback_before_ok)
        result["summary"]["rx_not_tx_before"] = bool(rx_not_tx_before)

        if not readback_before_ok:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": "Aborted before write: readiness/write-plan readback was not fully OK.",
                }
            )
            return result

        if not rx_not_tx_before:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": "Aborted before write: radio RX/TX status was not confirmed RX/not-TX.",
                }
            )
            return result

        plan_entries = result["plan"]

        def entry_required_contains(*needles: str) -> bool:
            for entry in plan_entries:
                if not isinstance(entry, dict):
                    continue
                if not bool(entry.get("required")):
                    continue
                text = " ".join(
                    str(entry.get(k, ""))
                    for k in ("name", "command", "description", "reason")
                ).lower()
                if all(needle.lower() in text for needle in needles):
                    return True
            return False

        frequency_required = entry_required_contains("frequency")
        mode_required = entry_required_contains("mode") or entry_required_contains("fm")
        simplex_required = entry_required_contains("simplex") or entry_required_contains("duplex")
        offset_required = entry_required_contains("offset")

        any_setting_write_required = bool(
            frequency_required or mode_required or simplex_required or offset_required
        )

        commands_to_send: list[tuple[str, bytes]] = []

        if any_setting_write_required:
            commands_to_send.append(("select_a_band_as_main", bytes([0x07, 0xD0])))

        if frequency_required:
            commands_to_send.append(
                (
                    "write_operating_frequency_146_520",
                    bytes([0x05]) + mhz_to_icom_frequency_bcd(146.520),
                )
            )

        if mode_required:
            commands_to_send.append(("set_fm_mode", bytes([0x06, 0x05])))

        if simplex_required:
            commands_to_send.append(("set_simplex", bytes([0x0F, 0x10])))

        if offset_required:
            commands_to_send.append(("write_zero_offset", bytes([0x0D]) + zero_offset_bcd()))

        # Deliberately do not write 1A 00 tone-off in Phase 8C-7.
        # Existing tone-setting interpretation is conservative, and the preferred
        # candidate should not need a tone-frequency write.

        if not commands_to_send:
            after_readback = self.direct_civ_side_a_readiness_probe(candidate=normalized_candidate)
            after_summary = after_readback.get("summary", {}) if isinstance(after_readback.get("summary"), dict) else {}
            result["after"] = after_readback
            result["summary"]["readback_after_ok"] = bool(after_readback.get("ok"))
            result["summary"]["frequency_matches"] = abs(
                float(summary_value(after_summary, "frequency_mhz", "operating_frequency_mhz") or 0.0) - 146.520
            ) <= 0.000005
            result["summary"]["mode_matches"] = str(summary_value(after_summary, "mode", "operating_mode") or "").upper() == "FM"
            result["summary"]["duplex_matches"] = str(summary_value(after_summary, "duplex", "duplex_text") or "").lower() in {"simplex", "none"}
            result["summary"]["offset_matches"] = abs(
                float(summary_value(after_summary, "offset_mhz", "frequency_offset_mhz") or 0.0)
            ) <= 0.0005
            result["summary"]["tone_mode_matches"] = str(summary_value(after_summary, "tone_mode", "tone_setting_text") or "none").lower() in {"none", "off", "disabled", ""}
            result.update(
                {
                    "status": "ok",
                    "ok": True,
                    "reason": "Candidate already matched current readback. No write/control commands were sent.",
                }
            )
            return result

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": f"Python pyserial module is not available; no CI-V write/control command was sent: {exc}",
                }
            )
            return result

        serial_opened = False
        try:
            with serial.Serial(
                cfg.direct_civ_serial_port,
                cfg.direct_civ_baud,
                timeout=float(cfg.direct_civ_timeout_seconds),
            ) as ser:
                serial_opened = True
                for name, payload in commands_to_send:
                    command_result = send_control_command(ser, name, payload)
                    result["commands_sent"].append(command_result)
                    if not command_result.get("ok"):
                        result["summary"]["writes_attempted"] = True
                        result["summary"]["write_count"] = len(result["commands_sent"])
                        result.update(
                            {
                                "status": "partial",
                                "ok": False,
                                "reason": f"Aborted after CI-V command failure: {name}",
                            }
                        )
                        break
        except Exception as exc:
            result["summary"]["writes_attempted"] = bool(result["commands_sent"])
            result["summary"]["write_count"] = len(result["commands_sent"])
            result.update(
                {
                    "status": "partial" if serial_opened else "aborted",
                    "ok": False,
                    "reason": f"Direct CI-V real tune serial/control path failed: {exc}",
                }
            )

        result["summary"]["writes_attempted"] = bool(result["commands_sent"])
        result["summary"]["write_count"] = len(result["commands_sent"])

        after_readback = self.direct_civ_side_a_readiness_probe(candidate=normalized_candidate)
        after_summary = after_readback.get("summary", {}) if isinstance(after_readback.get("summary"), dict) else {}
        after_commands = after_readback.get("commands") if isinstance(after_readback.get("commands"), list) else []
        result["after"] = after_readback
        result["summary"]["readback_after_ok"] = bool(after_readback.get("ok"))

        def parsed_after(command_name: str) -> Dict[str, Any]:
            for command in after_commands:
                if not isinstance(command, dict):
                    continue
                if command.get("name") != command_name:
                    continue
                parsed = command.get("parsed")
                return parsed if isinstance(parsed, dict) else {}
            return {}

        after_frequency_parsed = parsed_after("operating_frequency")
        try:
            after_frequency = float(after_frequency_parsed.get("frequency_mhz"))
        except (TypeError, ValueError):
            after_frequency = 0.0
        result["summary"]["frequency_matches"] = abs(after_frequency - 146.520) <= 0.000005

        after_mode_parsed = parsed_after("operating_mode")
        after_mode = str(after_mode_parsed.get("mode") or "").upper()
        result["summary"]["mode_matches"] = after_mode == "FM"

        after_duplex_parsed = parsed_after("duplex")
        after_duplex = str(after_duplex_parsed.get("duplex") or "").lower()
        result["summary"]["duplex_matches"] = after_duplex in {"simplex", "none"}

        after_offset_parsed = parsed_after("offset")
        try:
            after_offset_raw = int(after_offset_parsed.get("offset_raw_bcd_integer"))
        except (TypeError, ValueError):
            after_offset_raw = -1
        result["summary"]["offset_matches"] = after_offset_raw == 0

        # Tone-off write is intentionally not attempted in 8C-7.
        # The readiness probe read back raw tone-setting code 00 for this run.
        # Treat 00 as matching the candidate's no-tone requirement for this manual test,
        # while still not adding a tone-setting write command.
        after_tone_parsed = parsed_after("tone_setting")
        after_tone_code = str(after_tone_parsed.get("tone_setting_code_hex") or "").upper()
        result["summary"]["tone_mode_matches"] = after_tone_code in {"00", ""}

        final_ok = bool(
            result["summary"]["writes_attempted"]
            and result["summary"]["write_count"] > 0
            and result["summary"]["readback_after_ok"]
            and result["summary"]["frequency_matches"]
            and result["summary"]["mode_matches"]
            and result["summary"]["duplex_matches"]
            and result["summary"]["offset_matches"]
        )

        if result["status"] == "partial" and not final_ok:
            return result

        result.update(
            {
                "status": "ok" if final_ok else "partial",
                "ok": bool(final_ok),
                "reason": (
                    "Direct CI-V Side-A real tune test completed and readback matched."
                    if final_ok
                    else "Direct CI-V Side-A real tune test completed, but final readback did not fully match."
                ),
            }
        )
        result["summary"]["ready_for_future_automation"] = False
        return result

    def direct_civ_side_a_repeater_tune_test(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 8C-11 manual direct CI-V Side-A/Main-band repeater-style tune test.

        Phase 8C-11 integrates the Phase 8C-10-proven IC-2730A duplex
        write forms into this existing manual CLI-only repeater tune test:
        - simplex: 0F 10
        - DUP-:    0F 11
        - DUP+:    0F 12

        Manual CLI-only.
        No Redis writes.
        No UI bus writes.
        No system bus writes.
        No Hamlib.
        No rigctl.
        No memory programming.
        No scan control.
        No Side B programming.
        No PTT/transmit controls.
        """

        cfg = self.config

        def safety_model() -> Dict[str, Any]:
            return {
                "read_only": False,
                "manual_cli_only": True,
                "ptt_or_transmit_control_added": False,
                "memory_write_performed": False,
                "bank_write_performed": False,
                "scan_start_performed": False,
                "side_b_programming_performed": False,
                "redis_written": False,
                "ui_bus_written": False,
                "system_bus_written": False,
                "rigctl_used": False,
            }

        def base_result(status: str = "aborted", ok: bool = False, reason: str = "") -> Dict[str, Any]:
            return {
                "action": "direct_civ_side_a_repeater_tune_test",
                "phase": "8C-11",
                "status": status,
                "ok": bool(ok),
                "reason": reason,
                "candidate": candidate if isinstance(candidate, dict) else {},
                "before": {},
                "plan": [],
                "commands_sent": [],
                "after": {},
                "summary": {
                    "readback_before_ok": False,
                    "rx_not_tx_before": False,
                    "writes_attempted": False,
                    "write_count": 0,
                    "readback_after_ok": False,
                    "frequency_matches": False,
                    "mode_matches": False,
                    "duplex_matches": False,
                    "offset_matches": False,
                    "tone_frequency_matches": False,
                    "tone_mode_matches": False,
                    "duplex_write_supported": True,
                    "duplex_write_attempted": False,
                    "duplex_write_succeeded": False,
                    "tone_setting_write_deferred": False,
                    "ready_for_future_automation": False,
                },
                "safety": safety_model(),
                "updated_utc": utc_now(),
            }

        def candidate_float(
            raw: Dict[str, Any],
            name: str,
            *,
            required: bool,
            minimum: Optional[float] = None,
            maximum: Optional[float] = None,
        ) -> tuple[Optional[float], list[str]]:
            errors: list[str] = []
            value = raw.get(name)

            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    errors.append(f"{name} is required")
                return None, errors

            try:
                parsed = float(value)
            except (TypeError, ValueError):
                errors.append(f"{name} must be numeric")
                return None, errors

            if minimum is not None and parsed < minimum:
                errors.append(f"{name} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                errors.append(f"{name} must be <= {maximum}")

            return parsed, errors

        def candidate_str(raw: Dict[str, Any], name: str, default: str = "") -> str:
            value = raw.get(name, default)
            if value is None:
                return default
            return str(value).strip()

        def normalize_candidate(raw_candidate: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], list[str]]:
            raw = raw_candidate if isinstance(raw_candidate, dict) else {}
            errors: list[str] = []

            if not isinstance(raw_candidate, dict):
                errors.append("candidate JSON must be an object")

            if "__candidate_json_parse_error" in raw:
                errors.append(f"candidate JSON could not be parsed: {raw.get('__candidate_json_parse_error')}")

            frequency_mhz, frequency_errors = candidate_float(
                raw, "frequency_mhz", required=True, minimum=118.0, maximum=550.0
            )
            errors.extend(frequency_errors)

            mode = candidate_str(raw, "mode", "FM").upper()
            if mode != "FM":
                errors.append("mode must be FM")

            duplex_raw = candidate_str(raw, "duplex", "simplex").lower()
            duplex_aliases = {
                "simplex": "simplex",
                "none": "simplex",
                "off": "simplex",
                "plus": "plus",
                "+": "plus",
                "dup+": "plus",
                "duplex+": "plus",
                "minus": "minus",
                "-": "minus",
                "dup-": "minus",
                "duplex-": "minus",
            }
            duplex = duplex_aliases.get(duplex_raw)
            if duplex is None:
                errors.append("duplex must be one of: simplex, plus, minus, dup+, dup-, none")
                duplex = duplex_raw or "unknown"

            offset_mhz, offset_errors = candidate_float(
                raw, "offset_mhz", required=False, minimum=0.0
            )
            errors.extend(offset_errors)
            if offset_mhz is None:
                offset_mhz = 0.0

            tone_hz_present = raw.get("tone_hz") is not None and not (
                isinstance(raw.get("tone_hz"), str) and not str(raw.get("tone_hz")).strip()
            )
            tone_hz, tone_errors = candidate_float(
                raw, "tone_hz", required=False, minimum=50.0, maximum=300.0
            )
            errors.extend(tone_errors)

            tone_mode_raw = candidate_str(raw, "tone_mode", "none").lower()
            tone_mode_aliases = {
                "none": "none",
                "off": "none",
                "no": "none",
                "": "none",
                "tone": "tone",
                "encode": "tone",
                "ctcss": "tone",
                "tsql": "tsql",
                "tone_sql": "tsql",
                "tonesql": "tsql",
                "tone_squelch": "tsql",
                "dtcs": "dtcs",
                "dcs": "dtcs",
            }
            tone_mode = tone_mode_aliases.get(tone_mode_raw)
            if tone_mode is None:
                errors.append("tone_mode must be one of: none, off, tone, tsql, dtcs")
                tone_mode = tone_mode_raw or "unknown"

            if tone_mode == "dtcs":
                errors.append("tone_mode dtcs is rejected in Phase 8C-8")

            if tone_mode in {"tone", "tsql"} and not tone_hz_present:
                errors.append("tone_hz is required when tone_mode is tone or tsql")

            if tone_mode == "none" and tone_hz_present:
                errors.append("tone_hz must be null or absent when tone_mode is none")

            if tone_hz is not None:
                normalized_tone = round(float(tone_hz), 1)
                if normalized_tone not in STANDARD_CTCSS_TONES_HZ:
                    errors.append("tone_hz must be one of the standard CTCSS tone values")
            else:
                normalized_tone = None

            normalized = {
                "frequency_mhz": round(float(frequency_mhz or 0.0), 6),
                "mode": mode,
                "duplex": duplex,
                "offset_mhz": round(float(offset_mhz or 0.0), 6),
                "tone_hz": normalized_tone,
                "tone_mode": tone_mode,
            }

            return normalized, errors

        def mhz_to_icom_frequency_bcd(frequency_mhz: float) -> bytes:
            hz = int(round(float(frequency_mhz) * 1_000_000.0))
            digits = f"{hz:010d}"
            out = bytearray()
            for idx in range(8, -1, -2):
                lo = int(digits[idx + 1])
                hi = int(digits[idx])
                out.append((hi << 4) | lo)
            return bytes(out)

        def integer_to_icom_bcd_little(value: int, *, byte_count: int) -> bytes:
            digits = f"{int(value):0{byte_count * 2}d}"
            out = bytearray()
            for idx in range((byte_count * 2) - 2, -1, -2):
                hi = int(digits[idx])
                lo = int(digits[idx + 1])
                out.append((hi << 4) | lo)
            return bytes(out)

        def offset_mhz_to_bcd(offset_mhz: float) -> bytes:
            raw = int(round(float(offset_mhz) * 10000.0))
            return integer_to_icom_bcd_little(raw, byte_count=3)

        def tone_hz_to_bcd_big(tone_hz: float) -> bytes:
            raw_tenths = int(round(float(tone_hz) * 10.0))
            digits = f"{raw_tenths:04d}"
            return bytes([
                (int(digits[0]) << 4) | int(digits[1]),
                (int(digits[2]) << 4) | int(digits[3]),
            ])

        def hex_bytes(data: bytes) -> str:
            return " ".join(f"{b:02X}" for b in data)

        def frame_for(payload: bytes) -> bytes:
            to_addr = int(str(cfg.direct_civ_transceiver_address_hex), 16)
            from_addr = int(str(cfg.direct_civ_controller_address_hex), 16)
            return bytes([0xFE, 0xFE, to_addr, from_addr]) + bytes(payload) + b"\xFD"

        def parse_frames(raw: bytes) -> list[bytes]:
            frames: list[bytes] = []
            start = 0
            while True:
                try:
                    fe = raw.index(b"\xFE\xFE", start)
                    fd = raw.index(b"\xFD", fe)
                except ValueError:
                    break
                frames.append(raw[fe:fd + 1])
                start = fd + 1
            return frames

        def response_status(raw: bytes) -> tuple[str, str]:
            frames = parse_frames(raw)
            for frame in frames:
                if b"\xFB" in frame:
                    return "ok", "CI-V OK response received."
                if b"\xFA" in frame:
                    return "ng", "CI-V NG response received."
            if frames:
                return "unknown", "CI-V response frame received, but no OK/NG byte was found."
            return "unknown", "No complete CI-V response frame was parsed."

        def send_control_command(ser: Any, name: str, payload: bytes) -> Dict[str, Any]:
            frame = frame_for(payload)
            command_result: Dict[str, Any] = {
                "name": name,
                "payload_hex": hex_bytes(payload),
                "frame_hex": hex_bytes(frame),
                "response_hex": "",
                "status": "unknown",
                "ok": False,
                "reason": "",
            }

            try:
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                try:
                    ser.reset_output_buffer()
                except Exception:
                    pass

                ser.write(frame)
                ser.flush()

                deadline = time.monotonic() + float(cfg.direct_civ_timeout_seconds)
                raw = bytearray()
                while time.monotonic() < deadline:
                    chunk = ser.read(1)
                    if chunk:
                        raw.extend(chunk)
                        if raw.endswith(b"\xFD") and len(raw) >= 6:
                            if b"\xFB" in raw or b"\xFA" in raw:
                                break
                    else:
                        time.sleep(0.02)

                command_result["response_hex"] = hex_bytes(bytes(raw))
                status, reason = response_status(bytes(raw))
                command_result["status"] = status
                command_result["ok"] = status == "ok"
                command_result["reason"] = reason
                return command_result
            except Exception as exc:
                command_result["status"] = "error"
                command_result["ok"] = False
                command_result["reason"] = f"CI-V control command failed: {exc}"
                return command_result

        def numeric_changed(current_value: Any, candidate_value: Any, tolerance: float) -> bool:
            if current_value is None:
                return True
            try:
                return abs(float(current_value) - float(candidate_value)) > tolerance
            except Exception:
                return True

        def normalize_duplex_readback(value: Any) -> str:
            return {
                "simplex": "simplex",
                "none": "simplex",
                "10": "simplex",
                "dup-": "minus",
                "minus": "minus",
                "11": "minus",
                "dup+": "plus",
                "plus": "plus",
                "12": "plus",
            }.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        
        def duplex_write_spec_for(target_duplex: str) -> Optional[tuple[str, bytes]]:
            target = str(target_duplex or "").strip().lower()
            if target == "simplex":
                return ("set_duplex_simplex", bytes([0x0F, 0x10]))
            if target == "minus":
                return ("set_duplex_minus", bytes([0x0F, 0x11]))
            if target == "plus":
                return ("set_duplex_plus", bytes([0x0F, 0x12]))
            return None        

        def parsed_after(after_readback: Dict[str, Any], command_name: str) -> Dict[str, Any]:
            commands = after_readback.get("commands") if isinstance(after_readback.get("commands"), list) else []
            for command in commands:
                if not isinstance(command, dict):
                    continue
                if command.get("name") != command_name:
                    continue
                parsed = command.get("parsed")
                return parsed if isinstance(parsed, dict) else {}
            return {}

        result = base_result()

        normalized_candidate, validation_errors = normalize_candidate(candidate if isinstance(candidate, dict) else {})
        result["candidate"] = normalized_candidate if normalized_candidate is not None else {}

        if validation_errors:
            result.update(
                {
                    "status": "rejected",
                    "ok": False,
                    "reason": "Phase 8C-9 repeater tune candidate rejected before serial open: "
                    + "; ".join(validation_errors),
                    "updated_utc": utc_now(),
                }
            )
            return result

        gate_failures: list[str] = []
        if not cfg.direct_civ_enabled:
            gate_failures.append("direct_civ_enabled=false")
        if not cfg.direct_civ_side_a_readiness_probe_enabled:
            gate_failures.append("direct_civ_side_a_readiness_probe_enabled=false")
        if not cfg.direct_civ_side_a_write_plan_enabled:
            gate_failures.append("direct_civ_side_a_write_plan_enabled=false")
        if not cfg.direct_civ_side_a_repeater_tune_test_enabled:
            gate_failures.append("direct_civ_side_a_repeater_tune_test_enabled=false")

        if gate_failures:
            result.update(
                {
                    "status": "disabled",
                    "ok": False,
                    "reason": "Direct CI-V Side-A repeater tune test disabled by config: "
                    + ", ".join(gate_failures),
                    "updated_utc": utc_now(),
                }
            )
            return result

        plan_result = self.direct_civ_side_a_write_plan(normalized_candidate)
        result["before"] = {
            "write_plan_status": plan_result.get("status"),
            "write_plan_ok": plan_result.get("ok"),
            "current": plan_result.get("current", {}),
            "readback_summary": plan_result.get("readback_summary", {}),
        }
        result["plan"] = plan_result.get("plan", []) if isinstance(plan_result.get("plan"), list) else []

        before_summary = result["before"].get("readback_summary", {})
        current = result["before"].get("current", {}) if isinstance(result["before"].get("current"), dict) else {}

        readback_before_ok = bool(plan_result.get("summary", {}).get("readback_ok"))
        if not readback_before_ok:
            readback_before_ok = bool(
                before_summary.get("rx_tx_status_ok")
                and before_summary.get("frequency_ok")
                and before_summary.get("mode_ok")
                and before_summary.get("duplex_ok")
                and before_summary.get("offset_ok")
                and before_summary.get("tone_setting_ok")
                and before_summary.get("repeater_tone_frequency_ok")
            )

        rx_not_tx_before = bool(before_summary.get("rx_not_tx") is True)
        result["summary"]["readback_before_ok"] = bool(readback_before_ok)
        result["summary"]["rx_not_tx_before"] = bool(rx_not_tx_before)

        if not readback_before_ok:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": "Aborted before write: readiness/write-plan readback was not fully OK.",
                    "updated_utc": utc_now(),
                }
            )
            return result

        if not rx_not_tx_before:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": "Aborted before write: radio RX/TX status was not confirmed RX/not-TX.",
                    "updated_utc": utc_now(),
                }
            )
            return result

        candidate_frequency = float(normalized_candidate["frequency_mhz"])
        candidate_mode = str(normalized_candidate["mode"]).upper()
        candidate_duplex = str(normalized_candidate["duplex"])
        candidate_offset = float(normalized_candidate["offset_mhz"])
        candidate_tone_hz = normalized_candidate["tone_hz"]
        candidate_tone_mode = str(normalized_candidate["tone_mode"])

        frequency_required = numeric_changed(current.get("frequency_mhz"), candidate_frequency, 0.000005)
        mode_required = str(current.get("mode") or "").upper() != candidate_mode

        current_duplex_normalized = normalize_duplex_readback(current.get("duplex"))
        duplex_required = current_duplex_normalized != candidate_duplex

        duplex_spec = duplex_write_spec_for(candidate_duplex)
        if duplex_spec is None:
            result.update(
                {
                    "status": "rejected",
                    "ok": False,
                    "reason": f"Candidate duplex {candidate_duplex!r} is not allowed for Phase 8C-11 command send.",
                    "updated_utc": utc_now(),
                }
            )
            result["summary"]["ready_for_future_automation"] = False
            return result

        result["summary"]["duplex_write_supported"] = True
        result["summary"]["duplex_write_attempted"] = False
        result["summary"]["duplex_write_succeeded"] = False
        result["summary"]["duplex_matches"] = not bool(duplex_required)

        for entry in result["plan"]:
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("name", "")).lower()
            entry_code = str(entry.get("documented_command_code", "")).strip().upper()
            if (
                "dup" in entry_name
                or "simplex" in entry_name
                or entry_code in {"0F 10", "0F 11", "0F 12", "0F 10/11/12", "10", "11", "12", "10/11/12"}
            ):
                entry["would_send"] = bool(duplex_required)
                entry["deferred"] = False
                entry["supported"] = True
                entry["reason"] = (
                    "Candidate duplex differs from current readback; Phase 8C-11 will send the proven IC-2730A duplex write command."
                    if duplex_required
                    else "Candidate duplex setting already matches current readback."
                )

        offset_required = numeric_changed(current.get("offset_mhz_tentative"), candidate_offset, 0.0005)

        tone_frequency_required = False
        if candidate_tone_mode in {"tone", "tsql"} and candidate_tone_hz is not None:
            tone_frequency_required = numeric_changed(current.get("repeater_tone_hz"), candidate_tone_hz, 0.05)

        tone_mode_required = str(current.get("tone_mode_tentative") or "") != candidate_tone_mode

        commands_to_send: list[tuple[str, bytes]] = []
        any_setting_write_required = bool(
            frequency_required
            or mode_required
            or duplex_required
            or offset_required
            or tone_frequency_required
            or tone_mode_required
        )

        if any_setting_write_required:
            commands_to_send.append(("select_a_band_as_main", bytes([0x07, 0xD0])))

        if frequency_required:
            commands_to_send.append(
                (
                    f"write_operating_frequency_{candidate_frequency:.6f}_mhz",
                    bytes([0x05]) + mhz_to_icom_frequency_bcd(candidate_frequency),
                )
            )

        if mode_required:
            commands_to_send.append(("set_fm_mode", bytes([0x06, 0x05])))

        if duplex_required:
            duplex_name, duplex_payload = duplex_spec
            commands_to_send.append((duplex_name, duplex_payload))

        if offset_required:
            commands_to_send.append(("write_offset", bytes([0x0D]) + offset_mhz_to_bcd(candidate_offset)))

        if tone_frequency_required and candidate_tone_hz is not None:
            commands_to_send.append(
                (
                    f"write_repeater_ctcss_tone_{candidate_tone_hz:.1f}_hz",
                    bytes([0x1B, 0x00]) + tone_hz_to_bcd_big(candidate_tone_hz),
                )
            )

        if tone_mode_required:
            if candidate_tone_mode == "none":
                commands_to_send.append(("set_tone_mode_off", bytes([0x16, 0x42, 0x00])))
            elif candidate_tone_mode == "tone":
                commands_to_send.append(("set_tone_encode_mode", bytes([0x16, 0x42, 0x01])))
            elif candidate_tone_mode == "tsql":
                commands_to_send.append(("set_tone_squelch_mode", bytes([0x16, 0x42, 0x02])))
            else:
                result.update(
                    {
                        "status": "rejected",
                        "ok": False,
                        "reason": f"Candidate tone_mode {candidate_tone_mode!r} is not allowed for Phase 8C-8 command send.",
                        "updated_utc": utc_now(),
                    }
                )
                return result

        if not commands_to_send:
            after_readback = self.direct_civ_side_a_readiness_probe(candidate=normalized_candidate)
            result["after"] = after_readback
            result["summary"]["readback_after_ok"] = bool(after_readback.get("ok"))
            result["summary"]["frequency_matches"] = True
            result["summary"]["mode_matches"] = True
            result["summary"]["duplex_matches"] = True
            result["summary"]["offset_matches"] = True
            result["summary"]["tone_frequency_matches"] = True
            result["summary"]["tone_mode_matches"] = True
            result["summary"]["duplex_write_supported"] = True
            result["summary"]["duplex_write_attempted"] = False
            result["summary"]["duplex_write_succeeded"] = False
            result["summary"]["duplex_matches"] = True           
            result.update(
                {
                    "status": "ok",
                    "ok": True,
                    "reason": "Repeater candidate already matched current readback. No write/control commands were sent.",
                    "updated_utc": utc_now(),
                }
            )
            return result

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": f"Python pyserial module is not available; no CI-V write/control command was sent: {exc}",
                    "updated_utc": utc_now(),
                }
            )
            return result

        serial_opened = False
        try:
            with serial.Serial(
                cfg.direct_civ_serial_port,
                cfg.direct_civ_baud,
                timeout=float(cfg.direct_civ_timeout_seconds),
            ) as ser:
                serial_opened = True
                for name, payload in commands_to_send:
                    command_result = send_control_command(ser, name, payload)
                    result["commands_sent"].append(command_result)
                    if not command_result.get("ok"):
                        result["summary"]["writes_attempted"] = True
                        result["summary"]["write_count"] = len(result["commands_sent"])
                        result.update(
                            {
                                "status": "partial",
                                "ok": False,
                                "reason": f"Aborted after CI-V command failure: {name}",
                                "updated_utc": utc_now(),
                            }
                        )
                        break
        except Exception as exc:
            result["summary"]["writes_attempted"] = bool(result["commands_sent"])
            result["summary"]["write_count"] = len(result["commands_sent"])
            duplex_command_results = [
                command
                for command in result["commands_sent"]
                if (
                    isinstance(command, dict)
                    and (
                        str(command.get("name", "")).startswith("set_duplex_")
                        or str(command.get("payload_hex", "")).strip().upper() in {"0F 10", "0F 11", "0F 12"}
                    )
                )
            ]
            result["summary"]["duplex_write_attempted"] = bool(duplex_command_results)
            result["summary"]["duplex_write_succeeded"] = bool(duplex_command_results) and all(
                bool(command.get("ok")) for command in duplex_command_results
            )
            result.update(
                {
                    "status": "partial" if serial_opened else "aborted",
                    "ok": False,
                    "reason": f"Direct CI-V repeater tune serial/control path failed: {exc}",
                    "updated_utc": utc_now(),
                }
            )

        result["summary"]["writes_attempted"] = bool(result["commands_sent"])
        result["summary"]["write_count"] = len(result["commands_sent"])

        after_readback = self.direct_civ_side_a_readiness_probe(candidate=normalized_candidate)
        result["after"] = after_readback
        result["summary"]["readback_after_ok"] = bool(after_readback.get("ok"))

        after_frequency_parsed = parsed_after(after_readback, "operating_frequency")
        try:
            after_frequency = float(after_frequency_parsed.get("frequency_mhz"))
        except (TypeError, ValueError):
            after_frequency = 0.0
        result["summary"]["frequency_matches"] = abs(after_frequency - candidate_frequency) <= 0.000005

        after_mode_parsed = parsed_after(after_readback, "operating_mode")
        after_mode = str(after_mode_parsed.get("mode") or "").upper()
        result["summary"]["mode_matches"] = after_mode == candidate_mode

        after_duplex_parsed = parsed_after(after_readback, "duplex")
        after_duplex = normalize_duplex_readback(after_duplex_parsed.get("duplex"))
        result["summary"]["duplex_matches"] = after_duplex == candidate_duplex

        after_offset_parsed = parsed_after(after_readback, "offset")
        try:
            after_offset_raw = int(after_offset_parsed.get("offset_raw_bcd_integer"))
            after_offset_mhz = round(float(after_offset_raw) / 10000.0, 6)
        except (TypeError, ValueError):
            after_offset_mhz = -1.0
        result["summary"]["offset_matches"] = abs(after_offset_mhz - candidate_offset) <= 0.0005

        after_repeater_tone_parsed = parsed_after(after_readback, "repeater_tone_frequency")
        try:
            after_tone_hz = float(after_repeater_tone_parsed.get("tone_hz"))
        except (TypeError, ValueError):
            after_tone_hz = -1.0

        if candidate_tone_hz is None:
            result["summary"]["tone_frequency_matches"] = True
        else:
            result["summary"]["tone_frequency_matches"] = abs(after_tone_hz - float(candidate_tone_hz)) <= 0.05

        after_tone_setting_parsed = parsed_after(after_readback, "tone_setting")
        after_tone_mode = str(after_tone_setting_parsed.get("tone_mode") or "").lower()
        result["summary"]["tone_mode_matches"] = after_tone_mode == candidate_tone_mode

        commands_ok = bool(result["commands_sent"]) and all(
            bool(command.get("ok")) for command in result["commands_sent"] if isinstance(command, dict)
        )

        final_ok = bool(
            result["summary"]["writes_attempted"]
            and result["summary"]["write_count"] > 0
            and commands_ok
            and result["summary"]["readback_after_ok"]
            and result["summary"]["frequency_matches"]
            and result["summary"]["mode_matches"]
            and result["summary"]["duplex_matches"]
            and result["summary"]["offset_matches"]
            and result["summary"]["tone_frequency_matches"]
            and result["summary"]["tone_mode_matches"]
        )

        if result["status"] == "partial" and not final_ok:
            result["summary"]["ready_for_future_automation"] = False
            return result

        result.update(
            {
                "status": "ok" if final_ok else "partial",
                "ok": bool(final_ok),
                "reason": (
                    "Direct CI-V Side-A repeater tune test completed and readback matched."
                    if final_ok
                    else "Direct CI-V Side-A repeater tune test completed, but final readback did not fully match."
                ),
                "updated_utc": utc_now(),
            }
        )
        duplex_command_results = [
            command
            for command in result["commands_sent"]
            if (
                isinstance(command, dict)
                and (
                    str(command.get("name", "")).startswith("set_duplex_")
                    or str(command.get("payload_hex", "")).strip().upper() in {"0F 10", "0F 11", "0F 12"}
                )
            )
        ]

        result["summary"]["duplex_write_supported"] = True
        result["summary"]["duplex_write_attempted"] = bool(duplex_command_results)
        result["summary"]["duplex_write_succeeded"] = bool(duplex_command_results) and all(
            bool(command.get("ok")) for command in duplex_command_results
        )
        result["summary"]["tone_setting_write_deferred"] = False
        result["summary"]["ready_for_future_automation"] = False
        return result

    def direct_civ_side_a_duplex_proof(self, requested: Any) -> Dict[str, Any]:
        """
        Phase 8C-10 manual CLI-only direct CI-V Side-A duplex command proof.

        This proves or rejects only these documented duplex commands:

        - 10 set simplex
        - 11 set DUP-
        - 12 set DUP+

        It does not:
        - write frequency
        - write mode
        - write offset
        - write tone
        - write memory
        - write bank/group
        - start scan
        - program Side B
        - expose PTT/transmit control
        - publish Redis
        - write rt:ui:bus
        - write rt:system:bus
        - use Hamlib
        - use rigctl
        - modify the Phase 8C-9 repeater tune path
        """

        cfg = self.config

        duplex_aliases = {
            "simplex": "simplex",
            "none": "simplex",
            "off": "simplex",
            "minus": "minus",
            "-": "minus",
            "dup-": "minus",
            "duplex-": "minus",
            "plus": "plus",
            "+": "plus",
            "dup+": "plus",
            "duplex+": "plus",
        }

        command_specs = {
            "simplex": {
                "name": "set_simplex",
                "documented_command_code": "0F 10",
                "payload": bytes([0x0F, 0x10]),
            },
            "minus": {
                "name": "set_dup_minus",
                "documented_command_code": "0F 11",
                "payload": bytes([0x0F, 0x11]),
            },
            "plus": {
                "name": "set_dup_plus",
                "documented_command_code": "0F 12",
                "payload": bytes([0x0F, 0x12]),
            },
        }

        def safety_model() -> Dict[str, Any]:
            return {
                "read_only": False,
                "manual_cli_only": True,
                "frequency_write_performed": False,
                "mode_write_performed": False,
                "offset_write_performed": False,
                "tone_write_performed": False,
                "ptt_or_transmit_control_added": False,
                "memory_write_performed": False,
                "bank_write_performed": False,
                "scan_start_performed": False,
                "side_b_programming_performed": False,
                "redis_written": False,
                "ui_bus_written": False,
                "system_bus_written": False,
                "rigctl_used": False,
                "hamlib_used": False,
            }

        def empty_summary() -> Dict[str, Any]:
            return {
                "readback_before_ok": False,
                "rx_not_tx_before": False,
                "writes_attempted": False,
                "write_count": 0,
                "readback_after_ok": False,
                "all_requested_steps_matched": False,
                "simplex_proved": False,
                "minus_proved": False,
                "plus_proved": False,
                "failed_step": None,
                "ready_for_future_repeater_tune_use": False,
                "ready_for_future_automation": False,
            }

        def base_result(status: str = "aborted", ok: bool = False, reason: str = "") -> Dict[str, Any]:
            return {
                "action": "direct_civ_side_a_duplex_proof",
                "phase": "8C-10",
                "status": status,
                "ok": bool(ok),
                "reason": reason,
                "requested_sequence": [],
                "before": {},
                "steps": [],
                "after": {},
                "summary": empty_summary(),
                "safety": safety_model(),
                "radio": cfg.radio_name,
                "control_path": "direct_civ",
                "serial_port": cfg.direct_civ_serial_port,
                "baud": cfg.direct_civ_baud,
                "updated_utc": utc_now(),
            }

        def normalize_sequence(raw_requested: Any) -> tuple[list[str], list[str]]:
            errors: list[str] = []
            values: list[Any] = []

            if isinstance(raw_requested, list):
                values = raw_requested
            elif isinstance(raw_requested, tuple):
                values = list(raw_requested)
            elif isinstance(raw_requested, str):
                values = [part.strip() for part in raw_requested.split(",")]
            elif raw_requested is None:
                values = []
            else:
                values = [raw_requested]

            normalized: list[str] = []
            for item in values:
                text = str(item or "").strip().lower()
                if not text:
                    continue

                value = duplex_aliases.get(text)
                if value is None:
                    errors.append(
                        f"invalid duplex target {text!r}; allowed values are simplex, none, off, minus, dup-, duplex-, plus, dup+, duplex+"
                    )
                    continue

                normalized.append(value)

            if not normalized:
                errors.append("duplex proof sequence must contain at least one target")

            if len(normalized) > 6:
                errors.append("duplex proof sequence may contain no more than 6 targets")

            return normalized, errors

        def hex_bytes(data: bytes) -> str:
            return " ".join(f"{b:02X}" for b in data)

        def normalize_duplex_readback(value: Any) -> Optional[str]:
            text = str(value or "").strip().lower()
            return {
                "simplex": "simplex",
                "none": "simplex",
                "10": "simplex",
                "dup-": "minus",
                "minus": "minus",
                "11": "minus",
                "dup+": "plus",
                "plus": "plus",
                "12": "plus",
            }.get(text)

        def frame_for(payload: bytes) -> bytes:
            to_addr = _safe_hex_byte(
                cfg.direct_civ_transceiver_address_hex,
                DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
            )
            from_addr = _safe_hex_byte(
                cfg.direct_civ_controller_address_hex,
                DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
            )
            return bytes([0xFE, 0xFE, to_addr, from_addr]) + bytes(payload) + b"\xFD"

        def parse_frames(raw: bytes) -> list[bytes]:
            frames: list[bytes] = []
            start = 0
            while True:
                try:
                    fe = raw.index(b"\xFE\xFE", start)
                    fd = raw.index(b"\xFD", fe)
                except ValueError:
                    break
                frames.append(raw[fe:fd + 1])
                start = fd + 1
            return frames

        def response_status(raw: bytes) -> tuple[str, str]:
            if not raw:
                return "timeout", "No CI-V response bytes were received before timeout."

            frames = parse_frames(raw)
            for frame in frames:
                if b"\xFB" in frame:
                    return "ok", "CI-V OK response received."
                if b"\xFA" in frame:
                    return "ng", "CI-V NG response received."

            if frames:
                return "error", "CI-V response frame received, but no OK/NG byte was found."

            return "error", "No complete CI-V response frame was parsed."

        def send_control_command(ser: Any, name: str, payload: bytes) -> Dict[str, Any]:
            frame = frame_for(payload)
            command_result: Dict[str, Any] = {
                "name": name,
                "documented_command_code": hex_bytes(payload),
                "payload_hex": hex_bytes(payload),
                "frame_hex": hex_bytes(frame),
                "sent": False,
                "response_hex": "",
                "status": "unknown",
                "ok": False,
                "reason": "",
            }

            try:
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                try:
                    ser.reset_output_buffer()
                except Exception:
                    pass

                ser.write(frame)
                ser.flush()
                command_result["sent"] = True

                deadline = time.monotonic() + float(cfg.direct_civ_timeout_seconds)
                raw = bytearray()

                while time.monotonic() < deadline:
                    chunk = ser.read(1)
                    if chunk:
                        raw.extend(chunk)
                        if raw.endswith(b"\xFD") and len(raw) >= 6:
                            if b"\xFB" in raw or b"\xFA" in raw:
                                break
                    else:
                        time.sleep(0.02)

                command_result["response_hex"] = hex_bytes(bytes(raw))
                status, reason = response_status(bytes(raw))
                command_result["status"] = status
                command_result["ok"] = status == "ok"
                command_result["reason"] = reason
                return command_result

            except Exception as exc:
                command_result["status"] = "error"
                command_result["ok"] = False
                command_result["reason"] = f"CI-V duplex control command failed: {exc}"
                return command_result

        def read_duplex_command(ser: Any) -> Dict[str, Any]:
            controller_addr = _safe_hex_byte(
                cfg.direct_civ_controller_address_hex,
                DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
            )
            transceiver_addr = _safe_hex_byte(
                cfg.direct_civ_transceiver_address_hex,
                DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
            )

            return self._direct_civ_send_readonly_command(
                port=ser,
                name="duplex",
                controller_addr=controller_addr,
                transceiver_addr=transceiver_addr,
                timeout_seconds=cfg.direct_civ_timeout_seconds,
            )

        def read_rx_tx_command(ser: Any) -> Dict[str, Any]:
            controller_addr = _safe_hex_byte(
                cfg.direct_civ_controller_address_hex,
                DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
            )
            transceiver_addr = _safe_hex_byte(
                cfg.direct_civ_transceiver_address_hex,
                DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
            )

            return self._direct_civ_send_readonly_command(
                port=ser,
                name="rx_tx_status",
                controller_addr=controller_addr,
                transceiver_addr=transceiver_addr,
                timeout_seconds=cfg.direct_civ_timeout_seconds,
            )

        def duplex_from_command(command_result: Dict[str, Any]) -> Optional[str]:
            parsed = command_result.get("parsed") if isinstance(command_result.get("parsed"), dict) else {}
            return normalize_duplex_readback(parsed.get("duplex"))

        def duplex_code_from_command(command_result: Dict[str, Any]) -> Optional[str]:
            parsed = command_result.get("parsed") if isinstance(command_result.get("parsed"), dict) else {}
            code = parsed.get("duplex_code_hex")
            return str(code) if code is not None else None

        result = base_result()

        requested_sequence, validation_errors = normalize_sequence(requested)
        result["requested_sequence"] = requested_sequence

        if validation_errors:
            result.update(
                {
                    "status": "rejected",
                    "ok": False,
                    "reason": "Direct CI-V duplex proof request rejected before serial open: "
                    + "; ".join(validation_errors),
                    "updated_utc": utc_now(),
                }
            )
            return result

        gate_failures: list[str] = []
        if not cfg.direct_civ_enabled:
            gate_failures.append("direct_civ_enabled=false")
        if not cfg.direct_civ_side_a_readiness_probe_enabled:
            gate_failures.append("direct_civ_side_a_readiness_probe_enabled=false")
        if not cfg.direct_civ_side_a_duplex_proof_enabled:
            gate_failures.append("direct_civ_side_a_duplex_proof_enabled=false")

        if gate_failures:
            result.update(
                {
                    "status": "disabled",
                    "ok": False,
                    "reason": "Direct CI-V Side-A duplex proof disabled by config: "
                    + ", ".join(gate_failures),
                    "updated_utc": utc_now(),
                }
            )
            return result

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "aborted",
                    "ok": False,
                    "reason": f"Python pyserial module is not available; no CI-V command was sent: {exc}",
                    "updated_utc": utc_now(),
                }
            )
            return result

        serial_opened = False

        try:
            with serial.Serial(
                port=cfg.direct_civ_serial_port,
                baudrate=cfg.direct_civ_baud,
                timeout=cfg.direct_civ_timeout_seconds,
                write_timeout=cfg.direct_civ_timeout_seconds,
            ) as ser:
                serial_opened = True

                rx_tx_result = read_rx_tx_command(ser)
                before_duplex_result = read_duplex_command(ser)

                before_duplex = duplex_from_command(before_duplex_result)
                before_duplex_code = duplex_code_from_command(before_duplex_result)

                rx_tx_parsed = rx_tx_result.get("parsed") if isinstance(rx_tx_result.get("parsed"), dict) else {}
                rx_not_tx = bool(rx_tx_parsed.get("rx_not_tx") is True)

                result["before"] = {
                    "rx_tx_status": rx_tx_result,
                    "duplex_readback": before_duplex_result,
                    "duplex": before_duplex,
                    "duplex_code_hex": before_duplex_code,
                }

                readback_before_ok = bool(rx_tx_result.get("ok")) and bool(before_duplex_result.get("ok"))
                result["summary"]["readback_before_ok"] = bool(readback_before_ok)
                result["summary"]["rx_not_tx_before"] = bool(rx_not_tx)

                if not readback_before_ok:
                    result.update(
                        {
                            "status": "aborted",
                            "ok": False,
                            "reason": "Aborted before duplex proof write: RX/TX or duplex readback was not OK.",
                            "updated_utc": utc_now(),
                        }
                    )
                    return result

                if not rx_not_tx:
                    result.update(
                        {
                            "status": "aborted",
                            "ok": False,
                            "reason": "Aborted before duplex proof write: radio RX/TX status was not confirmed RX/not-TX.",
                            "updated_utc": utc_now(),
                        }
                    )
                    return result

                current_duplex = before_duplex
                failed = False

                for idx, target_duplex in enumerate(requested_sequence, start=1):
                    step: Dict[str, Any] = {
                        "step": idx,
                        "target_duplex": target_duplex,
                        "before_duplex": current_duplex,
                        "command": {},
                        "after_readback": {},
                        "matched": False,
                        "reason": "",
                    }

                    if current_duplex == target_duplex:
                        after_duplex_result = read_duplex_command(ser)
                        after_duplex = duplex_from_command(after_duplex_result)
                        after_duplex_code = duplex_code_from_command(after_duplex_result)
                        matched = after_duplex == target_duplex

                        step["command"] = {
                            "sent": False,
                            "reason": "Current duplex already matched target.",
                        }
                        step["after_readback"] = {
                            "duplex": after_duplex,
                            "duplex_code_hex": after_duplex_code,
                            "ok": bool(after_duplex_result.get("ok")),
                            "raw": after_duplex_result,
                        }
                        step["matched"] = bool(matched)
                        step["reason"] = (
                            "No write needed; current duplex already matched target and readback confirmed."
                            if matched
                            else "No write was sent, but follow-up duplex readback did not match target."
                        )

                        result["steps"].append(step)

                        if not matched:
                            result["summary"]["failed_step"] = idx
                            failed = True
                            break

                        current_duplex = after_duplex
                        continue

                    spec = command_specs[target_duplex]
                    command_result = send_control_command(
                        ser,
                        str(spec["name"]),
                        bytes(spec["payload"]),
                    )
                    command_result["documented_command_code"] = str(spec["documented_command_code"])
                    step["command"] = command_result

                    result["summary"]["writes_attempted"] = True
                    result["summary"]["write_count"] = int(result["summary"]["write_count"]) + 1

                    if not command_result.get("ok"):
                        after_duplex_result = read_duplex_command(ser)
                        after_duplex = duplex_from_command(after_duplex_result)
                        after_duplex_code = duplex_code_from_command(after_duplex_result)

                        step["after_readback"] = {
                            "duplex": after_duplex,
                            "duplex_code_hex": after_duplex_code,
                            "ok": bool(after_duplex_result.get("ok")),
                            "raw": after_duplex_result,
                        }
                        step["matched"] = False
                        step["reason"] = (
                            f"Duplex command {spec['documented_command_code']} did not return CI-V OK; "
                            "proof aborted without continuing to later targets."
                        )

                        result["steps"].append(step)
                        result["summary"]["failed_step"] = idx
                        failed = True
                        break

                    after_duplex_result = read_duplex_command(ser)
                    after_duplex = duplex_from_command(after_duplex_result)
                    after_duplex_code = duplex_code_from_command(after_duplex_result)
                    matched = after_duplex == target_duplex

                    step["after_readback"] = {
                        "duplex": after_duplex,
                        "duplex_code_hex": after_duplex_code,
                        "ok": bool(after_duplex_result.get("ok")),
                        "raw": after_duplex_result,
                    }
                    step["matched"] = bool(matched)
                    step["reason"] = (
                        f"Duplex command {spec['documented_command_code']} accepted and readback matched."
                        if matched
                        else f"Duplex command {spec['documented_command_code']} returned OK, but readback did not match target."
                    )

                    result["steps"].append(step)

                    if matched:
                        if target_duplex == "simplex":
                            result["summary"]["simplex_proved"] = True
                        elif target_duplex == "minus":
                            result["summary"]["minus_proved"] = True
                        elif target_duplex == "plus":
                            result["summary"]["plus_proved"] = True
                        current_duplex = after_duplex
                    else:
                        result["summary"]["failed_step"] = idx
                        failed = True
                        break

                final_duplex_result = read_duplex_command(ser)
                final_duplex = duplex_from_command(final_duplex_result)
                final_duplex_code = duplex_code_from_command(final_duplex_result)

                result["after"] = {
                    "duplex_readback": final_duplex_result,
                    "duplex": final_duplex,
                    "duplex_code_hex": final_duplex_code,
                }
                result["summary"]["readback_after_ok"] = bool(final_duplex_result.get("ok"))

                all_matched = bool(result["steps"]) and all(
                    bool(step.get("matched")) for step in result["steps"] if isinstance(step, dict)
                )
                result["summary"]["all_requested_steps_matched"] = bool(all_matched)

                if failed:
                    result.update(
                        {
                            "status": "aborted",
                            "ok": False,
                            "reason": "Direct CI-V Side-A duplex proof aborted after failed command or readback mismatch.",
                            "updated_utc": utc_now(),
                        }
                    )
                    return result

                if all_matched:
                    status = "ok" if bool(result["summary"]["writes_attempted"]) else "ok_with_warning"
                    reason = (
                        "Direct CI-V Side-A duplex proof completed; all requested steps matched."
                        if bool(result["summary"]["writes_attempted"])
                        else "Direct CI-V Side-A duplex proof completed with no write needed; requested state already matched."
                    )
                    result.update(
                        {
                            "status": status,
                            "ok": True,
                            "reason": reason,
                            "updated_utc": utc_now(),
                        }
                    )
                    return result

                result.update(
                    {
                        "status": "partial",
                        "ok": False,
                        "reason": "Direct CI-V Side-A duplex proof completed, but not all requested steps matched.",
                        "updated_utc": utc_now(),
                    }
                )
                return result

        except Exception as exc:
            result.update(
                {
                    "status": "partial" if serial_opened else "aborted",
                    "ok": False,
                    "reason": f"Direct CI-V Side-A duplex proof serial/control path failed: {exc}",
                    "updated_utc": utc_now(),
                }
            )
            return result

    def direct_civ_side_a_readiness_probe(self, candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Phase 8C-5 direct CI-V Side-A/Main-band readiness probe.

        This manual CLI-only probe reads the current Main-band state needed for
        a future Side-A candidate tune/check write-plan phase.

        It remains read-only:
        - no frequency write
        - no mode write
        - no duplex write
        - no offset write
        - no tone write
        - no memory write
        - no bank/group write
        - no scan start
        - no Side B programming
        - no PTT/transmit control
        - no Redis write
        - no rt:ui:bus write
        - no rt:system:bus write
        """

        cfg = self.config

        safety_flags = {
            "operation_performed": False,
            "read_only": True,
            "writes_performed": False,
            "frequency_write_performed": False,
            "mode_write_performed": False,
            "duplex_write_performed": False,
            "offset_write_performed": False,
            "tone_write_performed": False,
            "repeater_tone_write_performed": False,
            "tone_squelch_write_performed": False,
            "dtcs_write_performed": False,
            "memory_write_performed": False,
            "bank_write_performed": False,
            "scan_start_performed": False,
            "side_a_main_select_performed": False,
            "side_b_programming_performed": False,
            "ptt_or_transmit_control_added": False,
            "rigctl_used": False,
            "redis_written": False,
            "ui_bus_written": False,
            "system_bus_written": False,
            "serial_opened": False,
            "civ_command_sent": False,
        }

        disabled_result = {
            "action": "direct_civ_side_a_readiness_probe",
            "phase": "8C-5",
            "status": "disabled",
            "ok": False,
            **safety_flags,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "commands": [],
            "candidate": candidate if isinstance(candidate, dict) else None,
            "summary": {
                "rx_tx_status_ok": False,
                "rx_not_tx": False,
                "frequency_ok": False,
                "mode_ok": False,
                "duplex_ok": False,
                "offset_ok": False,
                "tone_setting_ok": False,
                "repeater_tone_frequency_ok": False,
                "tone_squelch_frequency_ok": False,
                "dtcs_code_polarity_ok": False,
                "ready_for_future_write_phase": False,
            },
            "reason": "Direct CI-V Side-A readiness probe disabled by config.",
            "updated_utc": utc_now(),
        }

        if not cfg.direct_civ_enabled or not cfg.direct_civ_side_a_readiness_probe_enabled:
            return disabled_result

        controller_addr = _safe_hex_byte(
            cfg.direct_civ_controller_address_hex,
            DEFAULT_DIRECT_CIV_CONTROLLER_ADDRESS_HEX,
        )
        transceiver_addr = _safe_hex_byte(
            cfg.direct_civ_transceiver_address_hex,
            DEFAULT_DIRECT_CIV_TRANSCEIVER_ADDRESS_HEX,
        )

        result: Dict[str, Any] = {
            "action": "direct_civ_side_a_readiness_probe",
            "phase": "8C-5",
            "status": "starting",
            "ok": False,
            **safety_flags,
            "radio": cfg.radio_name,
            "control_path": "direct_civ",
            "serial_port": cfg.direct_civ_serial_port,
            "baud": cfg.direct_civ_baud,
            "controller_address_hex": f"{controller_addr:02X}",
            "transceiver_address_hex": f"{transceiver_addr:02X}",
            "commands": [],
            "candidate": candidate if isinstance(candidate, dict) else None,
            "summary": {
                "rx_tx_status_ok": False,
                "rx_not_tx": False,
                "frequency_ok": False,
                "mode_ok": False,
                "duplex_ok": False,
                "offset_ok": False,
                "tone_setting_ok": False,
                "repeater_tone_frequency_ok": False,
                "tone_squelch_frequency_ok": False,
                "dtcs_code_polarity_ok": False,
                "ready_for_future_write_phase": False,
            },
            "reason": "",
            "updated_utc": utc_now(),
        }

        allowed_commands = set(DEFAULT_DIRECT_CIV_SIDE_A_READINESS_PROBE_COMMANDS)
        for command_name in cfg.direct_civ_side_a_readiness_probe_commands:
            if command_name not in allowed_commands:
                result.update(
                    {
                        "status": "rejected",
                        "ok": False,
                        "operation_performed": False,
                        "reason": f"Command {command_name!r} is not in the Phase 8C-5 read-only allowlist.",
                        "updated_utc": utc_now(),
                    }
                )
                return result

        try:
            import serial  # type: ignore
        except Exception as exc:
            result.update(
                {
                    "status": "unavailable",
                    "ok": False,
                    "operation_performed": False,
                    "reason": "Python pyserial module is not available; no CI-V readiness command was sent.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        port = None

        try:
            port = serial.Serial(
                port=cfg.direct_civ_serial_port,
                baudrate=cfg.direct_civ_baud,
                timeout=cfg.direct_civ_timeout_seconds,
                write_timeout=cfg.direct_civ_timeout_seconds,
            )

            result["operation_performed"] = True
            result["serial_opened"] = True

            for command_name in cfg.direct_civ_side_a_readiness_probe_commands:
                if command_name in DIRECT_CIV_READONLY_COMMANDS:
                    command_result = self._direct_civ_send_readonly_command(
                        port=port,
                        name=command_name,
                        controller_addr=controller_addr,
                        transceiver_addr=transceiver_addr,
                        timeout_seconds=cfg.direct_civ_timeout_seconds,
                    )
                elif command_name in DIRECT_CIV_READONLY_TONE_COMMANDS:
                    command_result = self._direct_civ_send_readonly_tone_command(
                        port=port,
                        name=command_name,
                        controller_addr=controller_addr,
                        transceiver_addr=transceiver_addr,
                        timeout_seconds=cfg.direct_civ_timeout_seconds,
                    )
                else:
                    command_result = {
                        "name": command_name,
                        "documented": False,
                        "read_only": False,
                        "sent": False,
                        "ok": False,
                        "raw_response_hex": "",
                        "parsed": {},
                        "reason": "Command name is not in the Phase 8C-5 read-only allowlist.",
                    }

                result["commands"].append(command_result)

            result["civ_command_sent"] = any(bool(cmd.get("sent")) for cmd in result["commands"])

            summary = self._direct_civ_side_a_readiness_summary(result["commands"])
            result["summary"] = summary

            required_ok = all(
                [
                    summary.get("rx_tx_status_ok"),
                    summary.get("frequency_ok"),
                    summary.get("mode_ok"),
                    summary.get("duplex_ok"),
                    summary.get("offset_ok"),
                    summary.get("tone_setting_ok"),
                    summary.get("repeater_tone_frequency_ok"),
                ]
            )

            result["status"] = "ok" if required_ok else "partial"
            result["ok"] = bool(result["commands"]) and any(bool(cmd.get("ok")) for cmd in result["commands"])
            result["reason"] = "Direct CI-V Side-A readiness probe completed. No write/control commands were included."
            result["updated_utc"] = utc_now()
            return result

        except Exception as exc:
            result.update(
                {
                    "status": "error",
                    "ok": False,
                    "reason": "Direct CI-V Side-A readiness probe failed.",
                    "detail": str(exc),
                    "updated_utc": utc_now(),
                }
            )
            return result

        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass

    def _direct_civ_send_readonly_command(
        self,
        *,
        port: Any,
        name: str,
        controller_addr: int,
        transceiver_addr: int,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        spec = DIRECT_CIV_READONLY_COMMANDS.get(name)

        if not spec:
            return {
                "name": name,
                "documented": False,
                "read_only": False,
                "sent": False,
                "ok": False,
                "raw_response_hex": "",
                "parsed": {},
                "reason": "Command name is not in the Phase 8C-3 read-only command table.",
            }

        command = spec["command"]
        frame = bytes([0xFE, 0xFE, transceiver_addr, controller_addr]) + command + bytes([0xFD])

        command_result: Dict[str, Any] = {
            "name": name,
            "documented": True,
            "read_only": True,
            "documented_command_code": str(spec["documented_command_code"]),
            "sent": False,
            "ok": False,
            "raw_response_hex": "",
            "parsed": {},
            "reason": "",
        }

        try:
            try:
                port.reset_input_buffer()
            except Exception:
                pass

            port.write(frame)
            port.flush()
            command_result["sent"] = True

            raw = self._direct_civ_read_raw(port=port, timeout_seconds=timeout_seconds)
            command_result["raw_response_hex"] = raw.hex(" ").upper()

            frames = self._direct_civ_split_frames(raw)
            response_payload = self._direct_civ_find_response_payload(
                frames=frames,
                controller_addr=controller_addr,
                transceiver_addr=transceiver_addr,
                expected_prefix=spec["response_prefix"],
            )

            if response_payload is None:
                command_result["reason"] = "No matching CI-V response frame was parsed."
                return command_result

            parsed = self._direct_civ_parse_payload(name, response_payload)
            command_result["parsed"] = parsed
            command_result["ok"] = bool(parsed.get("ok"))
            command_result["reason"] = parsed.get("reason", "CI-V read-only response parsed.")
            return command_result

        except Exception as exc:
            command_result["reason"] = f"CI-V read-only command failed: {exc}"
            return command_result

    def _direct_civ_send_readonly_tone_command(
        self,
        *,
        port: Any,
        name: str,
        controller_addr: int,
        transceiver_addr: int,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        spec = DIRECT_CIV_READONLY_TONE_COMMANDS.get(name)

        if not spec:
            return {
                "name": name,
                "documented": False,
                "read_only": False,
                "dual_purpose_family": False,
                "sent": False,
                "ok": False,
                "raw_response_hex": "",
                "parsed": {},
                "reason": "Command name is not in the Phase 8C-3A tone readback command table.",
            }

        command = spec["command"]

        # Important safety rule:
        # This frame includes only the documented command family/subcommand bytes.
        # It intentionally does not include tone frequency, tone setting, DTCS code,
        # polarity, memory, bank, scan, PTT, transmit, frequency, mode, duplex, offset,
        # Side B, or Main-band-selection payload bytes.
        frame = bytes([0xFE, 0xFE, transceiver_addr, controller_addr]) + command + bytes([0xFD])

        command_result: Dict[str, Any] = {
            "name": name,
            "documented": True,
            "read_only": True,
            "dual_purpose_family": bool(spec.get("dual_purpose_family")),
            "documented_command_code": str(spec["documented_command_code"]),
            "sent": False,
            "ok": False,
            "raw_response_hex": "",
            "parsed": {},
            "reason": "",
        }

        try:
            try:
                port.reset_input_buffer()
            except Exception:
                pass

            port.write(frame)
            port.flush()
            command_result["sent"] = True

            raw = self._direct_civ_read_raw(port=port, timeout_seconds=timeout_seconds)
            command_result["raw_response_hex"] = raw.hex(" ").upper()

            frames = self._direct_civ_split_frames(raw)
            response_payload = self._direct_civ_find_response_payload(
                frames=frames,
                controller_addr=controller_addr,
                transceiver_addr=transceiver_addr,
                expected_prefix=spec["response_prefix"],
            )

            if response_payload is None:
                command_result["reason"] = (
                    "No matching CI-V tone readback response frame was parsed. "
                    "No alternate or guessed command was attempted."
                )
                return command_result

            parsed = self._direct_civ_parse_tone_payload(name, response_payload)
            command_result["parsed"] = parsed
            command_result["ok"] = bool(parsed.get("ok"))
            command_result["reason"] = parsed.get("reason", "CI-V tone readback response parsed conservatively.")
            return command_result

        except Exception as exc:
            command_result["reason"] = f"CI-V tone readback command failed: {exc}"
            return command_result

    @staticmethod
    def _direct_civ_parse_tone_payload(name: str, payload: bytes) -> Dict[str, Any]:
        spec = DIRECT_CIV_READONLY_TONE_COMMANDS.get(name)
        if not spec:
            return {
                "ok": False,
                "reason": f"No tone parser implemented for unknown command {name}.",
            }

        prefix = spec["response_prefix"]
        data = payload[len(prefix):] if payload.startswith(prefix) else b""

        base: Dict[str, Any] = {
            "ok": bool(data),
            "raw_data_hex": data.hex(" ").upper(),
            "parsing_confidence": "conservative",
        }

        if name == "tone_setting":
            code_hex = data.hex(" ").upper() if data else None
            code_compact = code_hex.replace(" ", "") if isinstance(code_hex, str) else None
            tone_mode = {
                "00": "none",
                "01": "tone",
                "02": "tsql",
                "03": "dtcs",
            }.get(code_compact)

            base.update(
                {
                    "tone_setting_code_hex": code_hex,
                    "tone_mode": tone_mode,
                    "tone_mode_parse_confidence": "sample_program_mapping" if tone_mode is not None else "raw_only",
                    "reason": (
                        "Tone setting readback response parsed using supplied IC-2730A sample-program tone-mode values."
                        if tone_mode is not None
                        else "Tone setting readback response received, but tone-mode value is not recognized."
                        if data
                        else "Tone setting readback response had no data."
                    ),
                }
            )
            return base

        if name in {"repeater_tone_frequency", "tone_squelch_frequency"}:
            parsed = IC2730AAdapter._direct_civ_parse_tone_bcd_hz(data)
            base.update(
                {
                    "raw_bcd_hex": data.hex(" ").upper(),
                    "tone_hz": parsed.get("tone_hz"),
                    "tone_hz_parse_confidence": parsed.get("confidence"),
                    "tone_raw_bcd_integer": parsed.get("raw_bcd_integer"),
                    "reason": parsed.get("reason"),
                }
            )
            return base

        if name == "dtcs_code_polarity":
            parsed = IC2730AAdapter._direct_civ_parse_dtcs_raw(data)
            base.update(parsed)
            return base

        return {
            "ok": False,
            "raw_data_hex": data.hex(" ").upper(),
            "reason": f"No parser implemented for tone readback command {name}.",
        }

    @staticmethod
    def _direct_civ_parse_tone_bcd_hz(data: bytes) -> Dict[str, Any]:
        if not data:
            return {
                "tone_hz": None,
                "raw_bcd_integer": None,
                "confidence": "none",
                "reason": "Tone frequency response had no data.",
            }

        raw_little = IC2730AAdapter._direct_civ_parse_bcd_little_endian(data)
        raw_big = IC2730AAdapter._direct_civ_parse_bcd_big_endian(data)

        candidates: list[Dict[str, Any]] = []

        if raw_big is not None:
            candidates.append(
                {
                    "tone_hz": raw_big / 10.0,
                    "raw_bcd_integer": raw_big,
                    "confidence": "tentative_big_endian_one_decimal_bcd",
                    "byte_order": "big_endian",
                    "scale": "one_decimal",
                }
            )
            candidates.append(
                {
                    "tone_hz": float(raw_big),
                    "raw_bcd_integer": raw_big,
                    "confidence": "tentative_big_endian_integer_bcd",
                    "byte_order": "big_endian",
                    "scale": "integer",
                }
            )

        if raw_little is not None:
            candidates.append(
                {
                    "tone_hz": raw_little / 10.0,
                    "raw_bcd_integer": raw_little,
                    "confidence": "tentative_little_endian_one_decimal_bcd",
                    "byte_order": "little_endian",
                    "scale": "one_decimal",
                }
            )
            candidates.append(
                {
                    "tone_hz": float(raw_little),
                    "raw_bcd_integer": raw_little,
                    "confidence": "tentative_little_endian_integer_bcd",
                    "byte_order": "little_endian",
                    "scale": "integer",
                }
            )

        for candidate in candidates:
            tone_hz = float(candidate["tone_hz"])
            if 50.0 <= tone_hz <= 300.0:
                candidate["tone_hz"] = round(tone_hz, 1)
                candidate["reason"] = (
                    "Tone frequency response parsed as BCD into a plausible CTCSS tone. "
                    "Interpretation remains tentative until confirmed against the IC-2730A/IC-2730E reference."
                )
                return candidate

        return {
            "tone_hz": None,
            "raw_bcd_integer": raw_big if raw_big is not None else raw_little,
            "raw_bcd_big_endian_integer": raw_big,
            "raw_bcd_little_endian_integer": raw_little,
            "confidence": "raw_only",
            "reason": (
                "Tone frequency response was valid BCD, but no tested interpretation produced a plausible CTCSS range. "
                "Raw BCD recorded only."
            ),
        }

    @staticmethod
    def _direct_civ_parse_bcd_big_endian(data: bytes) -> Optional[int]:
        if not data:
            return None

        digits: list[str] = []

        for byte in data:
            high = (byte >> 4) & 0x0F
            low = byte & 0x0F

            if high > 9 or low > 9:
                return None

            digits.append(str(high))
            digits.append(str(low))

        text = "".join(digits).lstrip("0")
        if not text:
            return 0

        try:
            return int(text)
        except Exception:
            return None

    @staticmethod
    def _direct_civ_parse_dtcs_raw(data: bytes) -> Dict[str, Any]:
        if not data:
            return {
                "ok": False,
                "raw_dtcs_data_hex": "",
                "dtcs_code": None,
                "dtcs_code_display": None,
                "polarity": None,
                "parsing_confidence": "none",
                "reason": "DTCS code/polarity readback response had no data.",
            }

        raw_big = IC2730AAdapter._direct_civ_parse_bcd_big_endian(data)
        raw_little = IC2730AAdapter._direct_civ_parse_bcd_little_endian(data)

        display = None
        confidence = "raw_only"

        if raw_big is not None and 0 <= raw_big <= 999:
            display = f"D{raw_big:03d}"
            confidence = "tentative_big_endian_bcd"

        return {
            "ok": True,
            "raw_dtcs_data_hex": data.hex(" ").upper(),
            "dtcs_code": raw_big,
            "dtcs_code_display": display,
            "raw_dtcs_big_endian_integer": raw_big,
            "raw_dtcs_little_endian_integer": raw_little,
            "polarity": None,
            "parsing_confidence": confidence,
            "reason": (
                "DTCS code/polarity response received. DTCS code parsed tentatively from big-endian BCD; polarity layout is not interpreted in this phase."
                if display
                else "DTCS code/polarity response received. Raw value recorded; DTCS code/polarity byte layout is not interpreted in this phase."
            ),
        }

    @staticmethod
    def _direct_civ_tone_probe_summary(commands: list[Dict[str, Any]]) -> Dict[str, Any]:
        by_name = {str(cmd.get("name")): cmd for cmd in commands}

        return {
            "tone_setting_ok": bool(by_name.get("tone_setting", {}).get("ok")),
            "repeater_tone_frequency_ok": bool(by_name.get("repeater_tone_frequency", {}).get("ok")),
            "tone_squelch_frequency_ok": bool(by_name.get("tone_squelch_frequency", {}).get("ok")),
            "dtcs_code_polarity_ok": bool(by_name.get("dtcs_code_polarity", {}).get("ok")),
        }

    @staticmethod
    def _direct_civ_hex_bytes(data: bytes) -> str:
        return " ".join(f"{b:02X}" for b in bytes(data or b""))

    @staticmethod
    def _direct_civ_build_frame(
        *,
        payload: bytes,
        controller_addr: int,
        transceiver_addr: int,
    ) -> bytes:
        return bytes([0xFE, 0xFE, transceiver_addr, controller_addr]) + bytes(payload) + b"\xFD"

    @staticmethod
    def _direct_civ_decode_memory_channel_bcd_sample(data: bytes) -> Optional[int]:
        """
        Decode IC-2730A sample-program CMD 08 channel response.

        Sample says response payload is:
          08 <ch_hundreds_bcd> <ch_tens_ones_bcd>

        Examples:
          00 00 -> 0
          00 49 -> 49
          01 00 -> 100
          01 50 -> 150
          04 99 -> 499
        """
        if len(data) < 2:
            return None

        digits: list[str] = []
        for byte in data[:2]:
            high = (byte >> 4) & 0x0F
            low = byte & 0x0F
            if high > 9 or low > 9:
                return None
            digits.append(str(high))
            digits.append(str(low))

        try:
            return int("".join(digits))
        except Exception:
            return None
        
    @staticmethod
    def _direct_civ_encode_memory_channel_bcd(memory_channel: int) -> bytes:
        """
        Encode IC-2730A memory channel for the uploaded bank-manager sample command family.

        Conservative layout used for this first proof:
        - 2 BCD bytes
        - least-significant decimal pair first
        - 150 -> 50 01
        - 100 -> 00 01
        - 199 -> 99 01

        This is only used by the explicitly gated CMD 08 memory read/select proof.
        """
        channel = int(memory_channel)
        if channel < 0 or channel > 9999:
            raise ValueError(f"memory_channel out of BCD range: {memory_channel!r}")

        digits = f"{channel:04d}"
        return bytes(
            [
                (int(digits[2]) << 4) | int(digits[3]),
                (int(digits[0]) << 4) | int(digits[1]),
            ]
        )

    @staticmethod
    def _direct_civ_response_status(raw: bytes) -> Dict[str, Any]:
        frames = IC2730AAdapter._direct_civ_split_frames(raw)

        for frame in frames:
            payload = frame[4:-1] if len(frame) >= 6 else b""
            if payload == b"\xFB" or b"\xFB" in payload:
                return {
                    "status": "ok",
                    "ok": True,
                    "reason": "CI-V OK response received.",
                    "frame_hex": IC2730AAdapter._direct_civ_hex_bytes(frame),
                }
            if payload == b"\xFA" or b"\xFA" in payload:
                return {
                    "status": "ng",
                    "ok": False,
                    "reason": "CI-V NG response received.",
                    "frame_hex": IC2730AAdapter._direct_civ_hex_bytes(frame),
                }

        if frames:
            return {
                "status": "frame_without_ok_ng",
                "ok": False,
                "reason": "CI-V frame received, but no OK/NG payload was found.",
                "frame_count": len(frames),
            }

        return {
            "status": "timeout",
            "ok": False,
            "reason": "No complete CI-V response frame was parsed.",
            "frame_count": 0,
        }

    @staticmethod
    def _direct_civ_read_raw_full_window(*, port: Any, timeout_seconds: float) -> bytes:
        """
        Read for the full timeout window.

        This is intentionally different from _direct_civ_read_raw(), which may stop
        after the first frame. For proof commands we want to capture possible echo
        plus a later OK/NG or readback frame.
        """
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        chunks: list[bytes] = []

        while time.monotonic() < deadline:
            chunk = port.read(256)
            if chunk:
                chunks.append(chunk)
            else:
                time.sleep(0.02)

        return b"".join(chunks)

    def _direct_civ_send_payload_full_window(
        self,
        *,
        port: Any,
        name: str,
        payload: bytes,
        controller_addr: int,
        transceiver_addr: int,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        frame = self._direct_civ_build_frame(
            payload=payload,
            controller_addr=controller_addr,
            transceiver_addr=transceiver_addr,
        )

        result: Dict[str, Any] = {
            "name": name,
            "documented_command_code": self._direct_civ_hex_bytes(payload),
            "payload_hex": self._direct_civ_hex_bytes(payload),
            "frame_hex": self._direct_civ_hex_bytes(frame),
            "sent": False,
            "raw_response_hex": "",
            "response_status": {
                "status": "not_sent",
                "ok": False,
                "reason": "Command was not sent.",
            },
            "ok": False,
            "reason": "",
        }

        try:
            try:
                port.reset_input_buffer()
            except Exception:
                pass

            try:
                port.reset_output_buffer()
            except Exception:
                pass

            port.write(frame)
            port.flush()
            result["sent"] = True

            raw = self._direct_civ_read_raw_full_window(
                port=port,
                timeout_seconds=timeout_seconds,
            )
            result["raw_response_hex"] = self._direct_civ_hex_bytes(raw)
            response_status = self._direct_civ_response_status(raw)
            result["response_status"] = response_status
            result["ok"] = bool(response_status.get("ok"))
            result["reason"] = str(response_status.get("reason") or "CI-V command completed.")
            return result

        except Exception as exc:
            result["ok"] = False
            result["response_status"] = {
                "status": "error",
                "ok": False,
                "reason": f"CI-V command failed: {exc}",
            }
            result["reason"] = f"CI-V command failed: {exc}"
            return result

    @staticmethod
    def _direct_civ_read_raw(*, port: Any, timeout_seconds: float) -> bytes:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        chunks: list[bytes] = []

        while time.monotonic() < deadline:
            chunk = port.read(256)
            if chunk:
                chunks.append(chunk)
                if b"\xFD" in chunk:
                    # A read command normally returns one frame. Keep this short and conservative.
                    break

        return b"".join(chunks)

    @staticmethod
    def _direct_civ_split_frames(raw: bytes) -> list[bytes]:
        frames: list[bytes] = []
        idx = 0

        while idx < len(raw):
            start = raw.find(b"\xFE\xFE", idx)
            if start < 0:
                break

            end = raw.find(b"\xFD", start + 2)
            if end < 0:
                break

            frames.append(raw[start : end + 1])
            idx = end + 1

        return frames

    @staticmethod
    def _direct_civ_find_response_payload(
        *,
        frames: list[bytes],
        controller_addr: int,
        transceiver_addr: int,
        expected_prefix: bytes,
    ) -> Optional[bytes]:
        for frame in frames:
            if len(frame) < 6:
                continue

            if frame[0] != 0xFE or frame[1] != 0xFE or frame[-1] != 0xFD:
                continue

            dest = frame[2]
            src = frame[3]
            payload = frame[4:-1]

            # Ignore echoed request frames.
            if dest == transceiver_addr and src == controller_addr:
                continue

            if dest != controller_addr or src != transceiver_addr:
                continue

            if payload.startswith(expected_prefix):
                return payload

        return None

    @staticmethod
    def _direct_civ_parse_payload(name: str, payload: bytes) -> Dict[str, Any]:
        if name == "transceiver_id":
            data = payload[2:]
            return {
                "ok": bool(data),
                "transceiver_id_hex": data.hex(" ").upper(),
                "reason": "Transceiver ID read response parsed." if data else "Transceiver ID response had no data.",
            }

        if name == "operating_frequency":
            data = payload[1:]
            parsed_hz = IC2730AAdapter._direct_civ_parse_bcd_little_endian(data)
            parsed_mhz = round(parsed_hz / 1_000_000.0, 6) if parsed_hz is not None else None
            return {
                "ok": parsed_hz is not None,
                "frequency_hz": parsed_hz,
                "frequency_mhz": parsed_mhz,
                "raw_bcd_hex": data.hex(" ").upper(),
                "reason": "Operating frequency read response parsed." if parsed_hz is not None else "Operating frequency could not be parsed.",
            }

        if name == "operating_mode":
            data = payload[1:]
            mode_code = data[0] if data else None
            mode_map = {
                0x00: "LSB",
                0x01: "USB",
                0x02: "AM",
                0x03: "CW",
                0x04: "RTTY",
                0x05: "FM",
                0x07: "CW-R",
                0x08: "RTTY-R",
                0x17: "DV",
            }
            return {
                "ok": mode_code is not None,
                "mode_code_hex": f"{mode_code:02X}" if mode_code is not None else None,
                "mode": mode_map.get(mode_code, "unknown") if mode_code is not None else None,
                "raw_mode_data_hex": data.hex(" ").upper(),
                "reason": "Operating mode read response parsed." if mode_code is not None else "Operating mode response had no data.",
            }

        if name == "duplex":
            data = payload[1:]
            code = data[0] if data else None
            duplex_map = {
                0x10: "simplex",
                0x11: "dup-",
                0x12: "dup+",
            }
            return {
                "ok": code is not None,
                "duplex_code_hex": f"{code:02X}" if code is not None else None,
                "duplex": duplex_map.get(code, "unknown") if code is not None else None,
                "raw_duplex_data_hex": data.hex(" ").upper(),
                "reason": "Duplex setting read response parsed." if code is not None else "Duplex response had no data.",
            }

        if name == "offset":
            data = payload[1:]
            parsed = IC2730AAdapter._direct_civ_parse_bcd_little_endian(data)
            return {
                "ok": parsed is not None,
                "offset_raw_bcd_integer": parsed,
                "raw_offset_data_hex": data.hex(" ").upper(),
                "reason": (
                    "Frequency offset read response parsed as raw BCD integer; unit interpretation remains conservative."
                    if parsed is not None
                    else "Frequency offset could not be parsed."
                ),
            }

        if name == "rx_tx_status":
            data = payload[2:]
            code = data[0] if data else None

            # Conservative interpretation for Icom RX/TX status readback.
            # If this cannot be parsed as RX/not-TX, future write/control phases must not proceed.
            rx_not_tx = True if code == 0x00 else False if code == 0x01 else None
            status = "rx" if code == 0x00 else "tx" if code == 0x01 else "unknown"

            return {
                "ok": code is not None,
                "rx_tx_status_code_hex": f"{code:02X}" if code is not None else None,
                "rx_tx_status": status,
                "rx_not_tx": rx_not_tx,
                "raw_rx_tx_status_data_hex": data.hex(" ").upper(),
                "reason": (
                    "RX/TX status parsed as RX/not-TX."
                    if rx_not_tx is True
                    else "RX/TX status parsed as TX; do not proceed to future write/control commands."
                    if rx_not_tx is False
                    else "RX/TX status could not be safely interpreted."
                ),
            }

        return {
            "ok": False,
            "reason": f"No parser implemented for read-only command {name}.",
        }

    @staticmethod
    def _direct_civ_parse_bcd_little_endian(data: bytes) -> Optional[int]:
        if not data:
            return None

        value = 0
        multiplier = 1

        for byte in data:
            low = byte & 0x0F
            high = (byte >> 4) & 0x0F

            if low > 9 or high > 9:
                return None

            value += low * multiplier
            multiplier *= 10
            value += high * multiplier
            multiplier *= 10

        return value

    @staticmethod
    def _direct_civ_probe_summary(commands: list[Dict[str, Any]]) -> Dict[str, Any]:
        by_name = {str(cmd.get("name")): cmd for cmd in commands}

        rx_tx = by_name.get("rx_tx_status", {})
        rx_tx_parsed = rx_tx.get("parsed") if isinstance(rx_tx.get("parsed"), dict) else {}

        return {
            "transceiver_id_ok": bool(by_name.get("transceiver_id", {}).get("ok")),
            "frequency_ok": bool(by_name.get("operating_frequency", {}).get("ok")),
            "mode_ok": bool(by_name.get("operating_mode", {}).get("ok")),
            "duplex_ok": bool(by_name.get("duplex", {}).get("ok")),
            "offset_ok": bool(by_name.get("offset", {}).get("ok")),
            "rx_tx_status_ok": bool(by_name.get("rx_tx_status", {}).get("ok")),
            "rx_not_tx": bool(rx_tx_parsed.get("rx_not_tx") is True),
        }

    @staticmethod
    def _direct_civ_side_a_readiness_summary(commands: list[Dict[str, Any]]) -> Dict[str, Any]:
        by_name = {str(cmd.get("name")): cmd for cmd in commands}

        rx_tx = by_name.get("rx_tx_status", {})
        rx_tx_parsed = rx_tx.get("parsed") if isinstance(rx_tx.get("parsed"), dict) else {}

        return {
            "rx_tx_status_ok": bool(by_name.get("rx_tx_status", {}).get("ok")),
            "rx_not_tx": bool(rx_tx_parsed.get("rx_not_tx") is True),
            "frequency_ok": bool(by_name.get("operating_frequency", {}).get("ok")),
            "mode_ok": bool(by_name.get("operating_mode", {}).get("ok")),
            "duplex_ok": bool(by_name.get("duplex", {}).get("ok")),
            "offset_ok": bool(by_name.get("offset", {}).get("ok")),
            "tone_setting_ok": bool(by_name.get("tone_setting", {}).get("ok")),
            "repeater_tone_frequency_ok": bool(by_name.get("repeater_tone_frequency", {}).get("ok")),
            "tone_squelch_frequency_ok": bool(by_name.get("tone_squelch_frequency", {}).get("ok")),
            "dtcs_code_polarity_ok": bool(by_name.get("dtcs_code_polarity", {}).get("ok")),
            "ready_for_future_write_phase": False,
        }
    
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

    if "--direct-civ-readonly-probe" in sys.argv:
        print(json.dumps(adapter.direct_civ_readonly_probe(), indent=2, sort_keys=True))
        return 0

    if "--direct-civ-readonly-tone-probe" in sys.argv:
        print(json.dumps(adapter.direct_civ_readonly_tone_probe(), indent=2, sort_keys=True))
        return 0

    if "--direct-civ-side-a-readiness-probe" in sys.argv:
        print(json.dumps(adapter.direct_civ_side_a_readiness_probe(), indent=2, sort_keys=True))
        return 0

    if "--direct-civ-side-a-write-plan" in sys.argv:
        candidate_json = None
        if "--candidate-json" in sys.argv:
            idx = sys.argv.index("--candidate-json")
            if idx + 1 < len(sys.argv):
                candidate_json = sys.argv[idx + 1]

        if candidate_json is None:
            candidate = {"__candidate_json_parse_error": "--candidate-json is required"}
        else:
            try:
                parsed = json.loads(candidate_json)
                candidate = parsed if isinstance(parsed, dict) else {
                    "__candidate_json_parse_error": "candidate JSON must be an object"
                }
            except Exception as exc:
                candidate = {"__candidate_json_parse_error": str(exc)}

        print(json.dumps(adapter.direct_civ_side_a_write_plan(candidate), indent=2, sort_keys=True))
        return 0

    if "--direct-civ-side-a-real-tune-test" in sys.argv:
        candidate_json = None
        if "--candidate-json" in sys.argv:
            idx = sys.argv.index("--candidate-json")
            if idx + 1 < len(sys.argv):
                candidate_json = sys.argv[idx + 1]

        if candidate_json is None:
            candidate = {"__candidate_json_parse_error": "--candidate-json is required"}
        else:
            try:
                parsed = json.loads(candidate_json)
                candidate = parsed if isinstance(parsed, dict) else {
                    "__candidate_json_parse_error": "candidate JSON must be an object"
                }
            except Exception as exc:
                candidate = {"__candidate_json_parse_error": str(exc)}

        print(json.dumps(adapter.direct_civ_side_a_real_tune_test(candidate), indent=2, sort_keys=True))
        return 0


    if "--direct-civ-side-a-repeater-tune-test" in sys.argv:
        candidate_json = None
        if "--candidate-json" in sys.argv:
            idx = sys.argv.index("--candidate-json")
            if idx + 1 < len(sys.argv):
                candidate_json = sys.argv[idx + 1]

        if candidate_json is None:
            candidate = {"__candidate_json_parse_error": "--candidate-json is required"}
        else:
            try:
                parsed = json.loads(candidate_json)
                candidate = parsed if isinstance(parsed, dict) else {
                    "__candidate_json_parse_error": "candidate JSON must be an object"
                }
            except Exception as exc:
                candidate = {"__candidate_json_parse_error": str(exc)}

        print(json.dumps(adapter.direct_civ_side_a_repeater_tune_test(candidate), indent=2, sort_keys=True))
        return 0

    if "--direct-civ-side-a-duplex-proof" in sys.argv:
        requested = None

        if "--duplex-sequence" in sys.argv:
            idx = sys.argv.index("--duplex-sequence")
            if idx + 1 < len(sys.argv):
                requested = sys.argv[idx + 1]

        if requested is None and "--target-duplex" in sys.argv:
            idx = sys.argv.index("--target-duplex")
            if idx + 1 < len(sys.argv):
                requested = sys.argv[idx + 1]

        print(json.dumps(adapter.direct_civ_side_a_duplex_proof(requested), indent=2, sort_keys=True))
        return 0

    print(json.dumps(adapter.get_status(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
