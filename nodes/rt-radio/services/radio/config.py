from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RadioConfig:
    radio_type: str = "ft891"
    hamlib_host: str = "127.0.0.1"
    hamlib_port: int = 4532
    hamlib_timeout_sec: float = 2.0
    hamlib_readback_delay_ms: int = 120
    default_passband_ssb_hz: int = 2400
    default_passband_digital_hz: int = 3000
    default_passband_cw_hz: int = 500
    allow_autotune: bool = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_radio_config() -> RadioConfig:
    return RadioConfig(
        radio_type=os.environ.get("RT_RADIO_TYPE", "ft891").strip().lower(),
        hamlib_host=os.environ.get("RT_HAMLIB_HOST", "127.0.0.1").strip(),
        hamlib_port=int(os.environ.get("RT_HAMLIB_PORT", "4532")),
        hamlib_timeout_sec=float(os.environ.get("RT_HAMLIB_TIMEOUT_SEC", "2.0")),
        hamlib_readback_delay_ms=int(os.environ.get("RT_HAMLIB_READBACK_DELAY_MS", "120")),
        default_passband_ssb_hz=int(os.environ.get("RT_DEFAULT_PASSBAND_SSB_HZ", "2400")),
        default_passband_digital_hz=int(os.environ.get("RT_DEFAULT_PASSBAND_DIGITAL_HZ", "3000")),
        default_passband_cw_hz=int(os.environ.get("RT_DEFAULT_PASSBAND_CW_HZ", "500")),
        allow_autotune=_env_bool("RT_RADIO_ALLOW_AUTOTUNE", True),
    )