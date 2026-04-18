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
- rt:pota:ui:ssb:spots:selected    (string JSON array)
"""

from __future__ import annotations

import hashlib
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
FULL_REFRESH_INTERVAL_SEC = env_int("RT_POTA_FULL_REFRESH_INTERVAL_SEC", 120)
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


def stable_compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def payload_fingerprint(obj: Any) -> str:
    return hashlib.sha1(stable_compact_json(obj).encode("utf-8")).hexdigest()


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

    full_refresh_interval_sec: int = env_int("RT_POTA_FULL_REFRESH_INTERVAL_SEC", 120)
    pota_ui_bands_key: str = env_str("RT_POTA_UI_BANDS_KEY", "rt:pota:ui:ssb:bands")
    pota_ui_spots_prefix: str = env_str("RT_POTA_UI_SPOTS_PREFIX", "rt:pota:ui:ssb:spots")
    pota_ui_selected_spots_key: str = env_str(
        "RT_POTA_UI_SELECTED_SPOTS_KEY",
        "rt:pota:ui:ssb:spots:selected",
    )


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
        "selected_park_refs": [],
        "selected_park_names": [],
        "left_selected_park_refs": [],
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


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def normalize_context(existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = default_context()
    if not existing:
        return base

    ctx = {
        "selected_park_ref": str(existing.get("selected_park_ref", "") or ""),
        "selected_park_name": str(existing.get("selected_park_name", "") or ""),
        "selected_park_refs": _normalize_string_list(existing.get("selected_park_refs", [])),
        "selected_park_names": _normalize_string_list(existing.get("selected_park_names", [])),
        "left_selected_park_refs": _normalize_string_list(existing.get("left_selected_park_refs", [])),
        "selected_band": str(existing.get("selected_band", "") or ""),
        "grid": str(existing.get("grid", "") or ""),
        "selection_ts": existing.get("selection_ts", base["selection_ts"]),
    }

    if not ctx["selected_park_ref"]:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"

    if not ctx["selected_park_name"]:
        ctx["selected_park_name"] = "Not in a park" if not ctx["selected_park_ref"] else ""

    if ctx["selected_band"] and ctx["selected_band"] not in BAND_ORDER:
        ctx["selected_band"] = ""

    if ctx["selected_park_refs"]:
        ctx["selected_park_ref"] = ctx["selected_park_refs"][0]
        if ctx["selected_park_names"]:
            ctx["selected_park_name"] = ctx["selected_park_names"][0]
        elif not ctx["selected_park_name"]:
            ctx["selected_park_name"] = ""
    else:
        if ctx["selected_park_ref"]:
            ctx["selected_park_refs"] = [ctx["selected_park_ref"]]
            if ctx["selected_park_name"] and ctx["selected_park_name"] != "Not in a park":
                ctx["selected_park_names"] = [ctx["selected_park_name"]]
        else:
            ctx["selected_park_ref"] = ""
            ctx["selected_park_name"] = "Not in a park"
            ctx["selected_park_refs"] = []
            ctx["selected_park_names"] = []

    try:
        ctx["selection_ts"] = int(ctx["selection_ts"])
    except (TypeError, ValueError):
        ctx["selection_ts"] = base["selection_ts"]

    return ctx


def nearby_reference_name_map(nearby: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(nearby, dict):
        return {}

    choices = nearby.get("choices")
    if not isinstance(choices, list):
        return {}

    out: Dict[str, str] = {}
    for item in choices:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("reference", "") or "").strip()
        if not ref:
            continue
        name = str(item.get("name", "") or "").strip()
        out[ref] = name
    return out


def derive_context_from_nearby(
    ctx: Dict[str, Any],
    nearby: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    result = dict(ctx)

    ref_to_name = nearby_reference_name_map(nearby)
    nearby_refs = set(ref_to_name.keys())

    selected_refs = _normalize_string_list(result.get("selected_park_refs", []))
    selected_names = _normalize_string_list(result.get("selected_park_names", []))

    selected_names_by_ref: Dict[str, str] = {}
    for i, ref in enumerate(selected_refs):
        if i < len(selected_names):
            name = str(selected_names[i]).strip()
            if name:
                selected_names_by_ref[ref] = name

    aligned_names: List[str] = []
    for ref in selected_refs:
        name = selected_names_by_ref.get(ref) or ref_to_name.get(ref) or ""
        aligned_names.append(name)

    result["selected_park_refs"] = selected_refs
    result["selected_park_names"] = aligned_names

    if selected_refs:
        result["selected_park_ref"] = selected_refs[0]
        result["selected_park_name"] = aligned_names[0] if aligned_names else ""
    else:
        result["selected_park_ref"] = ""
        result["selected_park_name"] = "Not in a park"

    result["left_selected_park_refs"] = [ref for ref in selected_refs if ref not in nearby_refs]

    return result


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
        meta_ts = meta.get("spot_ts_epoch")

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


def read_source_band_state(
    r: redis.Redis,
    bands_source_key: str,
    spots_prefix: str,
) -> Dict[str, Tuple[int, int]]:
    """
    Returns:
      { band: (count, last_spot_ts) }

    Source-aware signature:
    - band membership comes from rt:pota:ssb:bands
    - last_spot_ts is the zset score on that key
    - count is zcard(rt:pota:ssb:spots:<band>)
    """
    try:
        raw_bands = r.zrange(bands_source_key, 0, -1, withscores=True)
    except RedisError:
        raise
    except Exception as exc:
        log_warning(
            "Unable to read source band zset",
            event="source_bands_read_error",
            key=bands_source_key,
            error=str(exc),
        )
        return {}

    source_scores: Dict[str, int] = {}
    for member, score in raw_bands:
        band = str(member or "").strip()
        if not band:
            continue
        try:
            source_scores[band] = int(float(score))
        except Exception:
            source_scores[band] = 0

    if not source_scores:
        return {}

    pipe = r.pipeline(transaction=False)
    band_keys: List[Tuple[str, str]] = []

    for band in source_scores.keys():
        key = f"{spots_prefix}:{band}"
        band_keys.append((band, key))
        pipe.zcard(key)

    try:
        counts = pipe.execute()
    except RedisError:
        raise
    except Exception as exc:
        log_warning(
            "Unable to pipeline source band counts",
            event="source_band_counts_pipeline_error",
            error=str(exc),
        )
        return {}

    out: Dict[str, Tuple[int, int]] = {}
    for (band, key), raw_count in zip(band_keys, counts):
        try:
            count = int(raw_count or 0)
        except Exception as exc:
            log_warning(
                "Unable to parse source band count",
                event="source_band_count_error",
                key=key,
                error=str(exc),
            )
            count = 0

        # keep only actually active bands
        if count > 0:
            out[band] = (count, source_scores.get(band, 0))

    return out


def load_changed_band_spots_with_meta(
    r: redis.Redis,
    spots_prefix: str,
    spotmeta_prefix: str,
    bands: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int]:
    """
    Only rebuild the specific bands that changed.
    """
    if not bands:
        return {}, 0, 0

    pipe = r.pipeline(transaction=False)
    band_keys: List[Tuple[str, str]] = []

    for band in bands:
        key = f"{spots_prefix}:{band}"
        band_keys.append((band, key))
        pipe.zrange(key, 0, -1, withscores=True)

    try:
        zrange_results = pipe.execute()
    except RedisError:
        raise
    except Exception as exc:
        log_warning(
            "Unable to pipeline changed band spot zset reads",
            event="changed_bands_zrange_pipeline_error",
            error=str(exc),
        )
        return {}, 0, 0

    per_band_rows: Dict[str, List[Dict[str, Any]]] = {}
    total_meta_hits = 0
    total_meta_malformed = 0

    for (band, key), raw in zip(band_keys, zrange_results):
        if not isinstance(raw, list):
            log_warning(
                "Band spots zset returned unexpected type",
                event="spots_zset_invalid_type",
                key=key,
            )
            per_band_rows[band] = []
            continue

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

        per_band_rows[band] = rows
        total_meta_hits += hit_count
        total_meta_malformed += malformed_count

    return per_band_rows, total_meta_hits, total_meta_malformed


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.redis_mgr = RedisManager(cfg)
        self.running = True

        self._last_ui_bands_fp: Optional[str] = None
        self._last_ui_selected_spots_fp: Optional[str] = None
        self._last_ui_spots_fp_by_band: Dict[str, str] = {}
        self._last_full_refresh_monotonic: float = 0.0

        self._last_source_band_state: Dict[str, Tuple[int, int]] = {}
        self._cached_per_band_spots: Dict[str, List[Dict[str, Any]]] = {
            band: [] for band in BAND_ORDER
        }

    def stop(self, *_args: Any) -> None:
        self.running = False
        log_info("Shutdown requested", event="shutdown_requested")

    def ensure_context_key(self, r: redis.Redis, nearby: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        existing = load_json_object(r, self.cfg.pota_context_key)
        normalized = normalize_context(existing)
        derived = derive_context_from_nearby(normalized, nearby)

        if existing != derived:
            r.set(self.cfg.pota_context_key, compact_json(derived))
        return derived

    def build_ui_band_summary_from_source(
        self,
        source_band_state: Dict[str, Tuple[int, int]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for band in BAND_ORDER:
            state = source_band_state.get(band)
            if not state:
                continue
            count, _last_spot_ts = state
            if count > 0:
                out.append({"band": band, "count": count})
        return out

    def refresh_changed_bands(
        self,
        r: redis.Redis,
        source_band_state: Dict[str, Tuple[int, int]],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int, List[str], List[str], bool]:
        previous = self._last_source_band_state
        current = source_band_state

        now_mono = time.monotonic()
        force_full_refresh = False

        if self._last_full_refresh_monotonic <= 0.0:
            force_full_refresh = True
        elif (now_mono - self._last_full_refresh_monotonic) >= self.cfg.full_refresh_interval_sec:
            force_full_refresh = True

        if force_full_refresh:
            changed_bands = sorted(current.keys())
        else:
            changed_bands = sorted(
                band for band in current.keys()
                if previous.get(band) != current.get(band)
            )

        removed_bands = sorted(band for band in previous.keys() if band not in current)

        refreshed_rows, meta_hits, meta_malformed = load_changed_band_spots_with_meta(
            r,
            self.cfg.pota_ssb_spots_source_prefix,
            self.cfg.pota_ssb_spotmeta_prefix,
            changed_bands,
        )

        for band in changed_bands:
            self._cached_per_band_spots[band] = refreshed_rows.get(band, [])

        for band in removed_bands:
            self._cached_per_band_spots[band] = []

        self._last_source_band_state = dict(current)

        if force_full_refresh:
            self._last_full_refresh_monotonic = now_mono

        per_band_spots = {
            band: self._cached_per_band_spots.get(band, [])
            for band in BAND_ORDER
        }
        return per_band_spots, meta_hits, meta_malformed, changed_bands, removed_bands, force_full_refresh
    
    def publish_ui_state(
        self,
        r: redis.Redis,
        context: Dict[str, Any],
        band_summary: List[Dict[str, Any]],
        per_band_spots: Dict[str, List[Dict[str, Any]]],
    ) -> Tuple[int, int]:
        selected_band = str(context.get("selected_band", "") or "")
        selected_spots = per_band_spots.get(selected_band, []) if selected_band else []

        writes = 0
        skipped = 0
        pipe = r.pipeline(transaction=False)

        ui_bands_fp = payload_fingerprint(band_summary)
        if ui_bands_fp != self._last_ui_bands_fp:
            pipe.set(self.cfg.pota_ui_bands_key, compact_json(band_summary))
            self._last_ui_bands_fp = ui_bands_fp
            writes += 1
        else:
            skipped += 1

        for band in BAND_ORDER:
            key = f"{self.cfg.pota_ui_spots_prefix}:{band}"
            payload = per_band_spots.get(band, [])
            band_fp = payload_fingerprint(payload)

            if self._last_ui_spots_fp_by_band.get(band) != band_fp:
                pipe.set(key, compact_json(payload))
                self._last_ui_spots_fp_by_band[band] = band_fp
                writes += 1
            else:
                skipped += 1

        selected_fp = payload_fingerprint(selected_spots)
        if selected_fp != self._last_ui_selected_spots_fp:
            pipe.set(self.cfg.pota_ui_selected_spots_key, compact_json(selected_spots))
            self._last_ui_selected_spots_fp = selected_fp
            writes += 1
        else:
            skipped += 1

        if writes > 0:
            pipe.execute()

        return writes, skipped

    def run_once(self) -> None:
        r = self.redis_mgr.get()
        nearby = load_json_object(r, self.cfg.pota_nearby_key)
        context = self.ensure_context_key(r, nearby)

        source_band_state = read_source_band_state(
            r,
            self.cfg.pota_ssb_bands_source_key,
            self.cfg.pota_ssb_spots_source_prefix,
        )

        band_summary = self.build_ui_band_summary_from_source(source_band_state)

        per_band_spots, total_meta_hits, total_meta_malformed, changed_bands, removed_bands, force_full_refresh = self.refresh_changed_bands(
            r,
            source_band_state,
        )

        writes, skipped = self.publish_ui_state(r, context, band_summary, per_band_spots)

        total_spots = sum(len(v) for v in per_band_spots.values())

        log_info(
            "Published POTA UI context",
            event="cycle_complete",
            active_bands=len(band_summary),
            total_ui_spots=total_spots,
            changed_bands=changed_bands,
            removed_bands=removed_bands,
            selected_park_ref=context.get("selected_park_ref", ""),
            selected_park_refs=context.get("selected_park_refs", []),
            left_selected_park_refs=context.get("left_selected_park_refs", []),
            selected_band=context.get("selected_band", ""),
            spotmeta_hits=total_meta_hits,
            spotmeta_malformed=total_meta_malformed,
            redis_keys_written=writes,
            redis_keys_skipped=skipped,
            forced_full_refresh=force_full_refresh,
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