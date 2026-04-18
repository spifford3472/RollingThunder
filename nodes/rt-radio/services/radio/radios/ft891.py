from __future__ import annotations

import time
from dataclasses import dataclass

from ..hamlib_client import HamlibClient


class RadioValidationError(Exception):
    pass


@dataclass
class TuneResult:
    freq_hz: int
    mode: str
    passband_hz: int
    autotune_requested: bool
    autotune_attempted: bool
    autotune_error: str | None = None


class FT891RadioBackend:
    """
    Minimal FT-891 radio backend using Hamlib rigctld.
    """

    def __init__(self, hamlib: HamlibClient, readback_delay_ms: int = 120):
        self.hamlib = hamlib
        self.readback_delay_ms = readback_delay_ms

    def _validate(self, freq_hz: int, mode: str | None, passband_hz: int | None) -> None:
        if freq_hz <= 0:
            raise RadioValidationError("invalid frequency")
        
        if mode is not None:
            mode = mode.upper()
            valid_modes = {
                "USB",
                "LSB",
                "AM",
                "FM",
                "CW",
                "CWR",
                "DIGU",
                "DIGL",
                "PKTUSB",
                "PKTLSB",
            }
            if mode not in valid_modes:
                raise RadioValidationError(f"unsupported mode: {mode}")

        if passband_hz is not None and passband_hz <= 0:
            raise RadioValidationError("invalid passband")

    def tune(
        self,
        freq_hz: int,
        mode: str | None,
        passband_hz: int | None,
        autotune: bool,
    ) -> TuneResult:
        if mode is not None:
            mode = mode.upper()

        self._validate(freq_hz, mode, passband_hz)

        # Step 1: set frequency
        self.hamlib.set_freq(freq_hz)

        # Step 2: set mode if supplied
        if mode is not None:
            if passband_hz is None:
                current = self.hamlib.get_mode()
                passband_hz = current.passband_hz
            self.hamlib.set_mode(mode, passband_hz)

        autotune_attempted = False
        autotune_error: str | None = None

        # Step 3: optional tuner
        if autotune:
            autotune_attempted = True
            try:
                self.hamlib.start_tuner()
            except Exception as exc:
                autotune_error = str(exc)

        # allow radio state to settle
        time.sleep(self.readback_delay_ms / 1000.0)

        # Step 4: readback
        rb_freq = self.hamlib.get_freq()
        rb_mode = self.hamlib.get_mode()

        return TuneResult(
            freq_hz=rb_freq,
            mode=rb_mode.mode,
            passband_hz=rb_mode.passband_hz,
            autotune_requested=bool(autotune),
            autotune_attempted=autotune_attempted,
            autotune_error=autotune_error,
        )