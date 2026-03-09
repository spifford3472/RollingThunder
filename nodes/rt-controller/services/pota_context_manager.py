#!/opt/rollingthunder/.venv/bin/python3
"""
RollingThunder v0.3300
pota_context_manager.py

Purpose:
- Maintain current park selection context
- Mirror poller-owned Redis zsets into UI-friendly JSON keys
- Keep UI simple and deterministic

Reads:
- rt:pota:nearby
- rt:pota:ssb:bands                (zset; member=band, score=count)
- rt:pota:ssb:spots:<band>         (zset; member="band:CALL:PARKREF:YYYYMMDD", score=spot_ts_epoch)

Writes:
- rt:pota:context                  (string JSON object)
- rt:pota:ui:ssb:bands             (string JSON array)
- rt:pota:ui:ssb:spots:<band>      (string JSON array)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import redis
from redis.exceptions import RedisError

SERVICE_NAME = "pota_context_manager"
SERVICE_VERSION = "0.3300"
LOOP_INTERVAL_SEC = 1.0

BAND_ORDER = [
    "160m", "80m", "60m", "40m", "30m",
    "20m", "17m", "15m", "12m", "10m",
    "6m",
]


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": utc_now_ms(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "msg": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            payload.update(record.extra_data)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return compact_json(payload)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = configure_logging()


def log_info(message: str, event: str, **extra: Any) -> None:
    LOGGER.info(message, extra={"event": event, "extra_data": extra})


def log_warning(message: str, event: str, **extra: Any) -> None:
    LOGGER.warning(message, extra={"event": event, "extra_data": extra})


def log_error(message: str, event: str, **extra: Any) -> None:
    LOGGER.error(message, extra={"event": event, "extra_data": extra})


@dataclass
class Config:
    redis_host: str = env_str("RT_REDIS_HOST", "127.0.0.1")
    redis_port: int = env_int("RT_REDIS_PORT", 6379)
    redis_password: str = env_str("RT_REDIS_PASSWORD", "")
    redis_db: int = env_int("RT_REDIS_DB", 0)

    pota_context_key: str = env_str("RT_POTA_CONTEXT_KEY", "rt:pota:context")
    pota_nearby_key: str = env_str("RT_POTA_NEARBY_KEY", "rt:pota:nearby")

    pota_ssb_bands_source_key: str = env_str("RT_POTA_SSB_BANDS_SOURCE_KEY", "rt:pota:ssb:bands")
    pota_ssb_spots_source_prefix: str = env_str("RT_POTA_SSB_SPOTS_SOURCE_PREFIX", "rt:pota:ssb:spots")

    pota_ui_bands_key: str = env_str("RT_POTA_UI_BANDS_KEY", "rt:pota:ui:ssb:bands")
    pota_ui_spots_prefix: str = env_str("RT_POTA_UI_SPOTS_PREFIX", "rt:pota:ui:ssb:spots")


class RedisManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client: Optional[redis.Redis] = None

    def connect(self) -> redis.Redis:
        if self.client is not None:
            try:
                self.client.ping()
                return self.client
            except RedisError:
                self.client = None

        while True:
            try:
                self.client = redis.Redis(
                    host=self.cfg.redis_host,
                    port=self.cfg.redis_port,
                    password=(self.cfg.redis_password or None),
                    db=self.cfg.redis_db,
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    health_check_interval=15,
                )
                self.client.ping()
                log_info(
                    "Connected to Redis",
                    event="redis_connected",
                    host=self.cfg.redis_host,
                    port=self.cfg.redis_port,
                    db=self.cfg.redis_db,
                )
                return self.client
            except RedisError as exc:
                log_error(
                    "Redis connection failed; retrying",
                    event="redis_connect_error",
                    error=str(exc),
                )
                time.sleep(2.0)

    def get(self) -> redis.Redis:
        return self.connect()


def default_context() -> Dict[str, Any]:
    return {
        "selected_park_ref": "",
        "selected_park_name": "Not in a park",
        "grid": "",
        "selection_ts": utc_now_ms(),
    }


def load_json_object(r: redis.Redis, key: str) -> Optional[Dict[str, Any]]:
    raw = r.get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        log_warning("Redis key does not contain a JSON object", event="invalid_json_object", key=key)
        return None
    except json.JSONDecodeError:
        log_warning("Failed to parse JSON object from Redis", event="json_decode_error", key=key)
        return None


def normalize_context(existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = default_context()
    if not existing:
        return base

    ctx = {
        "selected_park_ref": str(existing.get("selected_park_ref", "") or ""),
        "selected_park_name": str(existing.get("selected_park_name", "") or ""),
        "grid": str(existing.get("grid", "") or ""),
        "selection_ts": existing.get("selection_ts", base["selection_ts"]),
    }

    if not ctx["selected_park_ref"]:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"

    if not ctx["selected_park_name"]:
        ctx["selected_park_name"] = "Not in a park" if not ctx["selected_park_ref"] else ""

    try:
        ctx["selection_ts"] = int(ctx["selection_ts"])
    except (TypeError, ValueError):
        ctx["selection_ts"] = base["selection_ts"]

    return ctx


def parse_band_spot_member(member: str, score: float) -> Dict[str, Any]:
    parts = member.split(":", 3)
    band = parts[0] if len(parts) > 0 else ""
    call = parts[1] if len(parts) > 1 else ""
    park_ref = parts[2] if len(parts) > 2 else ""
    spot_day_utc = parts[3] if len(parts) > 3 else ""

    try:
        score_int = int(float(score))
    except (TypeError, ValueError):
        score_int = 0

    return {
        "member": member,
        "band": band,
        "call": call,
        "park_ref": park_ref,
        "spot_day_utc": spot_day_utc,
        "score": score_int,
    }


def zset_band_counts(r: redis.Redis, key: str) -> List[Tuple[str, int]]:
    try:
        raw = r.zrange(key, 0, -1, withscores=True)
    except RedisError:
        raise
    except Exception as exc:
        log_warning("Unable to read band counts zset", event="bands_zset_read_error", key=key, error=str(exc))
        return []

    counts: Dict[str, int] = {}
    for member, score in raw:
        band = str(member)
        if band not in BAND_ORDER:
            continue
        try:
            counts[band] = int(float(score))
        except (TypeError, ValueError):
            counts[band] = 0

    return [(band, counts[band]) for band in BAND_ORDER if counts.get(band, 0) > 0]


def zset_band_spots(r: redis.Redis, key: str) -> List[Dict[str, Any]]:
    try:
        raw = r.zrange(key, 0, -1, withscores=True)
    except RedisError:
        raise
    except Exception as exc:
        log_warning("Unable to read band spots zset", event="spots_zset_read_error", key=key, error=str(exc))
        return []

    return [parse_band_spot_member(member, score) for member, score in raw]


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.redis_mgr = RedisManager(cfg)
        self.running = True

    def stop(self, *_args: Any) -> None:
        self.running = False
        log_info("Shutdown requested", event="shutdown_requested")

    def ensure_context_key(self, r: redis.Redis) -> Dict[str, Any]:
        existing = load_json_object(r, self.cfg.pota_context_key)
        normalized = normalize_context(existing)
        if existing != normalized:
            r.set(self.cfg.pota_context_key, compact_json(normalized))
        return normalized

    def build_ui_band_summary(self, r: redis.Redis) -> List[Dict[str, Any]]:
        counts = zset_band_counts(r, self.cfg.pota_ssb_bands_source_key)
        return [{"band": band, "count": count} for band, count in counts]

    def build_ui_spots(self, r: redis.Redis) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for band in BAND_ORDER:
            source_key = f"{self.cfg.pota_ssb_spots_source_prefix}:{band}"
            result[band] = zset_band_spots(r, source_key)
        return result

    def publish_ui_state(
        self,
        r: redis.Redis,
        band_summary: List[Dict[str, Any]],
        per_band_spots: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        r.set(self.cfg.pota_ui_bands_key, compact_json(band_summary))
        for band in BAND_ORDER:
            key = f"{self.cfg.pota_ui_spots_prefix}:{band}"
            r.set(key, compact_json(per_band_spots.get(band, [])))

    def run_once(self) -> None:
        r = self.redis_mgr.get()
        context = self.ensure_context_key(r)
        band_summary = self.build_ui_band_summary(r)
        per_band_spots = self.build_ui_spots(r)
        self.publish_ui_state(r, band_summary, per_band_spots)

        total_spots = sum(len(v) for v in per_band_spots.values())

        log_info(
            "Published POTA UI context",
            event="cycle_complete",
            active_bands=len(band_summary),
            total_ui_spots=total_spots,
            selected_park_ref=context.get("selected_park_ref", ""),
        )

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        log_info(
            "Starting service",
            event="service_start",
            version=SERVICE_VERSION,
            loop_interval_sec=LOOP_INTERVAL_SEC,
            context_key=self.cfg.pota_context_key,
            nearby_key=self.cfg.pota_nearby_key,
            bands_source_key=self.cfg.pota_ssb_bands_source_key,
            spots_source_prefix=self.cfg.pota_ssb_spots_source_prefix,
            ui_bands_key=self.cfg.pota_ui_bands_key,
            ui_spots_prefix=self.cfg.pota_ui_spots_prefix,
        )

        while self.running:
            cycle_start = time.monotonic()
            try:
                self.run_once()
            except RedisError as exc:
                log_error("Redis operation failed", event="redis_runtime_error", error=str(exc))
                self.redis_mgr.client = None
            except Exception as exc:
                log_error("Unhandled exception in service loop", event="service_loop_error", error=str(exc))

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, LOOP_INTERVAL_SEC - elapsed)
            time.sleep(sleep_for)

        log_info("Service stopped", event="service_stop")


def main() -> int:
    cfg = Config()
    service = Service(cfg)
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())