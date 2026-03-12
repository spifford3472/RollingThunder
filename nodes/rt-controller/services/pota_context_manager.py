#!/opt/rollingthunder/.venv/bin/python3
"""
RollingThunder v0.3310
pota_context_manager.py

Purpose:
- Maintain current park selection context
- Mirror poller-owned Redis zsets into UI-friendly JSON keys
- Enrich UI spot rows from poller-owned metadata sidecar keys
- Keep UI simple and deterministic

Reads:
- rt:pota:nearby
- rt:pota:ssb:bands                (zset; member=band, score=latest/current spot epoch)
- rt:pota:ssb:spots:<band>         (zset; member="band:CALL:PARKREF:YYYYMMDD", score=spot_ts_epoch)
- rt:pota:ssb:spotmeta:<member>    (string JSON metadata sidecar, optional)

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
from datetime import datetime, timezone

import redis
from redis.exceptions import RedisError

SERVICE_NAME = "pota_context_manager"
SERVICE_VERSION = "0.3310"
LOOP_INTERVAL_SEC = 1.0

BAND_ORDER = [
    "160m", "80m", "60m", "40m", "30m",
    "20m", "17m", "15m", "12m", "10m",
    "6m",
]


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def epoch_to_iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


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
    pota_ssb_spotmeta_prefix: str = env_str("RT_POTA_SSB_SPOTMETA_PREFIX", "rt:pota:ssb:spotmeta")

    pota_ui_bands_key: str = env_str("RT_POTA_UI_BANDS_KEY", "rt:pota:ui:ssb:bands")
    pota_ui_spots_prefix: str = env_str("RT_POTA_UI_SPOTS_PREFIX", "rt:pota:ui:ssb:spots")
    pota_ui_selected_spots_key: str = env_str("RT_POTA_UI_SELECTED_SPOTS_KEY","rt:pota:ui:ssb:spots:selected",)


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
        "selected_band": "",
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
        "selected_band": str(existing.get("selected_band", "") or ""),
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
        "spot_ts_epoch": score_int,
    }


def load_spotmeta_bulk(
    r: redis.Redis,
    spotmeta_prefix: str,
    members: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    if not members:
        return {}, 0

    keys = [f"{spotmeta_prefix}:{member}" for member in members]
    raw_values = r.mget(keys)

    meta_by_member: Dict[str, Dict[str, Any]] = {}
    malformed_count = 0

    for member, raw in zip(members, raw_values):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                meta_by_member[member] = parsed
            else:
                malformed_count += 1
                log_warning(
                    "Spot metadata sidecar is not a JSON object",
                    event="spotmeta_invalid_type",
                    member=member,
                )
        except json.JSONDecodeError:
            malformed_count += 1
            log_warning(
                "Failed to parse spot metadata sidecar JSON",
                event="spotmeta_json_decode_error",
                member=member,
            )

    return meta_by_member, malformed_count


def enrich_spot_row(base: Dict[str, Any], meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = dict(base)

    row.setdefault("park_name", "")
    row.setdefault("freq_hz", None)
    row.setdefault("mode", "SSB")
    row.setdefault("spot_ts_utc", "")

    if not meta:
        if row.get("spot_ts_epoch"):
            row["spot_ts_utc"] = epoch_to_iso_utc(int(row["spot_ts_epoch"]))
        return row

    row["call"] = str(meta.get("call") or row.get("call", ""))
    row["band"] = str(meta.get("band") or row.get("band", ""))
    row["park_ref"] = str(meta.get("park_ref") or row.get("park_ref", ""))
    row["park_name"] = str(meta.get("park_name") or "")

    freq_hz = meta.get("freq_hz")
    try:
        row["freq_hz"] = int(freq_hz) if freq_hz is not None else None
    except (TypeError, ValueError):
        row["freq_hz"] = None

    row["mode"] = str(meta.get("mode") or "SSB")

    meta_ts = meta.get("spot_ts")
    if meta_ts is None:
        meta_ts = meta.get("spot_ts_epoch")  # backward-compat during rollout

    try:
        if meta_ts is not None:
            row["spot_ts_epoch"] = int(meta_ts)
    except (TypeError, ValueError):
        pass

    meta_utc = meta.get("spot_ts_utc")
    if meta_utc:
        row["spot_ts_utc"] = str(meta_utc)
    elif row.get("spot_ts_epoch"):
        row["spot_ts_utc"] = epoch_to_iso_utc(int(row["spot_ts_epoch"]))

    return row


def zset_band_counts(r: redis.Redis, spots_prefix: str) -> List[Tuple[str, int]]:
    counts: List[Tuple[str, int]] = []

    for band in BAND_ORDER:
        key = f"{spots_prefix}:{band}"
        try:
            count = int(r.zcard(key))
        except RedisError:
            raise
        except Exception as exc:
            log_warning(
                "Unable to read band spot count",
                event="band_zcard_error",
                key=key,
                error=str(exc),
            )
            count = 0

        if count > 0:
            counts.append((band, count))

    return counts


def zset_band_spots_with_meta(
    r: redis.Redis,
    spots_key: str,
    spotmeta_prefix: str,
) -> Tuple[List[Dict[str, Any]], int, int]:
    try:
        raw = r.zrange(spots_key, 0, -1, withscores=True)
    except RedisError:
        raise
    except Exception as exc:
        log_warning(
            "Unable to read band spots zset",
            event="spots_zset_read_error",
            key=spots_key,
            error=str(exc),
        )
        return [], 0, 0

    base_rows = [parse_band_spot_member(member, score) for member, score in raw]
    members = [row["member"] for row in base_rows]

    meta_by_member, malformed_count = load_spotmeta_bulk(r, spotmeta_prefix, members)

    rows: List[Dict[str, Any]] = []
    hit_count = 0

    for base in base_rows:
        member = base["member"]
        meta = meta_by_member.get(member)
        if meta:
            hit_count += 1
        rows.append(enrich_spot_row(base, meta))

    return rows, hit_count, malformed_count


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
        counts = zset_band_counts(r, self.cfg.pota_ssb_spots_source_prefix)
        return [{"band": band, "count": count} for band, count in counts]

    def build_ui_spots(self, r: redis.Redis) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        total_meta_hits = 0
        total_meta_malformed = 0

        for band in BAND_ORDER:
            source_key = f"{self.cfg.pota_ssb_spots_source_prefix}:{band}"
            rows, meta_hits, malformed = zset_band_spots_with_meta(
                r,
                source_key,
                self.cfg.pota_ssb_spotmeta_prefix,
            )
            result[band] = rows
            total_meta_hits += meta_hits
            total_meta_malformed += malformed

        return result, total_meta_hits, total_meta_malformed

    def publish_ui_state(
        self,
        r: redis.Redis,
        context: Dict[str, Any],
        band_summary: List[Dict[str, Any]],
        per_band_spots: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        selected_band = str(context.get("selected_band", "") or "")
        selected_spots = per_band_spots.get(selected_band, []) if selected_band else []

        pipe = r.pipeline(transaction=False)
        pipe.set(self.cfg.pota_ui_bands_key, compact_json(band_summary))
        for band in BAND_ORDER:
            key = f"{self.cfg.pota_ui_spots_prefix}:{band}"
            pipe.set(key, compact_json(per_band_spots.get(band, [])))
        pipe.set(self.cfg.pota_ui_selected_spots_key, compact_json(selected_spots))
        pipe.execute()

    def run_once(self) -> None:
        r = self.redis_mgr.get()
        context = self.ensure_context_key(r)
        band_summary = self.build_ui_band_summary(r)
        per_band_spots, total_meta_hits, total_meta_malformed = self.build_ui_spots(r)
        self.publish_ui_state(r, context, band_summary, per_band_spots)

        total_spots = sum(len(v) for v in per_band_spots.values())

        log_info(
            "Published POTA UI context",
            event="cycle_complete",
            active_bands=len(band_summary),
            total_ui_spots=total_spots,
            selected_park_ref=context.get("selected_park_ref", ""),
            spotmeta_hits=total_meta_hits,
            spotmeta_malformed=total_meta_malformed,
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
            spotmeta_prefix=self.cfg.pota_ssb_spotmeta_prefix,
            ui_bands_key=self.cfg.pota_ui_bands_key,
            ui_spots_prefix=self.cfg.pota_ui_spots_prefix,
            ui_selected_spots_key=self.cfg.pota_ui_selected_spots_key,
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