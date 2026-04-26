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

    Conservative extension:
    - optionally attempts explicit FT-891 CAT band select before frequency/mode
    - if raw CAT passthrough is unavailable on the HamlibClient, falls back safely
      to the prior frequency/mode-only behavior
    """

    # FT-891 native CAT band select codes:
    # BS00=160m, BS01=80m, BS03=40m, BS04=30m, BS05=20m, BS06=17m,
    # BS07=15m, BS08=12m, BS09=10m, BS10=6m
    _FT891_BAND_TO_BS = {
        "160m": "BS00;",
        "80m": "BS01;",
        "40m": "BS03;",
        "30m": "BS04;",
        "20m": "BS05;",
        "17m": "BS06;",
        "15m": "BS07;",
        "12m": "BS08;",
        "10m": "BS09;",
        "6m": "BS10;",
    }

    def __init__(self, hamlib: HamlibClient, readback_delay_ms: int = 120):
        self.hamlib = hamlib
        self.readback_delay_ms = readback_delay_ms

    def _validate(
        self,
        freq_hz: int,
        mode: str | None,
        passband_hz: int | None,
        band: str | None = None,
    ) -> None:
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

        if band is not None:
            band_norm = self._normalize_band(band)
            if band_norm not in self._FT891_BAND_TO_BS:
                raise RadioValidationError(f"unsupported band: {band}")

    def _normalize_band(self, band: str | None) -> str | None:
        if band is None:
            return None
        b = str(band).strip().lower()
        if not b:
            return None
        return b

    def _hamlib_raw_command(self, command: str) -> str | None:
        """
        Best-effort raw CAT passthrough.

        We intentionally probe a few likely method names so this file can remain
        a drop-in replacement even if HamlibClient evolved slightly.
        """
        candidates = (
            "raw_command",
            "raw_cat",
            "command",
            "send_command",
        )

        for name in candidates:
            fn = getattr(self.hamlib, name, None)
            if callable(fn):
                try:
                    # Common shapes:
                    #   fn("w BS05; 500")
                    #   fn("BS05;")
                    result = fn(command)
                    return None if result is None else str(result)
                except TypeError:
                    continue

        return None
    
    def _select_band(self, band: str | None) -> bool:
        band_norm = self._normalize_band(band)
        if not band_norm:
            return False

        cat = self._FT891_BAND_TO_BS.get(band_norm)
        if not cat:
            return False

        try:
            # ✅ CORRECT: use raw_cat directly (NO "w ...")
            self.hamlib.raw_cat(cat, expected_bytes=0)
            return True
        except Exception:
            return False

    def tune(
        self,
        freq_hz: int,
        mode: str | None,
        passband_hz: int | None,
        autotune: bool,
        band: str | None = None,
    ) -> TuneResult:
        if mode is not None:
            mode = mode.upper()

        band_norm = self._normalize_band(band)
        self._validate(freq_hz, mode, passband_hz, band_norm)

        # Step 0: best-effort explicit band select
        # Safe fallback: if unsupported or raw CAT passthrough is unavailable,
        # we continue with the prior behavior.
        if band_norm is not None:
            try:
                self._select_band(band_norm)
            except Exception:
                # Conservative: never fail the tune solely because explicit band
                # select was unavailable.
                pass

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