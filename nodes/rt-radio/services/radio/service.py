from __future__ import annotations

from .config import RadioConfig
from .hamlib_client import HamlibClient
from .radios.ft891 import FT891RadioBackend


class RadioService:
    def __init__(self, config: RadioConfig):
        self.config = config
        self.hamlib = HamlibClient(
            host=config.hamlib_host,
            port=config.hamlib_port,
            timeout_sec=config.hamlib_timeout_sec,
        )

        radio_type = (config.radio_type or "").strip().lower()
        if radio_type == "ft891":
            self.backend = FT891RadioBackend(
                hamlib=self.hamlib,
                readback_delay_ms=config.hamlib_readback_delay_ms,
            )
        else:
            raise ValueError(f"unsupported radio_type: {config.radio_type}")

    def close(self) -> None:
        self.hamlib.close()

    def tune(
        self,
        *,
        freq_hz: int,
        mode: str,
        passband_hz: int | None = None,
        autotune: bool = False,
    ):
        return self.backend.tune(
            freq_hz=freq_hz,
            mode=mode,
            passband_hz=passband_hz,
            autotune=autotune,
        )