from __future__ import annotations

import time

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

    def atas_tune(
        self,
        *,
        band: str | None = None,
        timeout_sec: float = 8.0,
        poll_interval_sec: float = 0.35,
    ) -> dict[str, object]:
        """
        Trigger tuner cycle only when an actual tuner exists.

        `band` is informational; tuning is based on current radio frequency.
        """
        if not self.config.has_tuner:
            raise RuntimeError("no controllable tuner configured for this radio")

        if not self.config.allow_autotune:
            raise RuntimeError("autotune disabled in config")

        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be > 0")
        if poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be > 0")

        initial_state = ""
        try:
            initial_state = str(self.hamlib.get_tuner_state() or "").strip()
        except Exception:
            initial_state = ""

        self.hamlib.start_tuner()

        deadline = time.monotonic() + timeout_sec
        poll_count = 0
        last_state = initial_state
        history: list[str] = []

        time.sleep(min(poll_interval_sec, 0.25))

        while time.monotonic() < deadline:
            poll_count += 1

            try:
                state = str(self.hamlib.get_tuner_state() or "").strip()
            except Exception as exc:
                return {
                    "band": band or "",
                    "tuner_started": True,
                    "completed": False,
                    "timed_out": False,
                    "final_state": last_state,
                    "initial_state": initial_state,
                    "poll_count": poll_count,
                    "history": history[-10:],
                    "msg": f"tuner state polling failed: {type(exc).__name__}: {exc}",
                }

            if state:
                last_state = state
                history.append(state)

            normalized = state.upper()

            if normalized in {"0", "OFF", "ON", "READY", "IDLE", "ENABLED", "DISABLED"}:
                return {
                    "band": band or "",
                    "tuner_started": True,
                    "completed": True,
                    "timed_out": False,
                    "final_state": state,
                    "initial_state": initial_state,
                    "poll_count": poll_count,
                    "history": history[-10:],
                    "msg": "atas_tune_completed",
                }

            time.sleep(poll_interval_sec)

        return {
            "band": band or "",
            "tuner_started": True,
            "completed": False,
            "timed_out": True,
            "final_state": last_state,
            "initial_state": initial_state,
            "poll_count": poll_count,
            "history": history[-10:],
            "msg": "atas_tune_timeout",
        }