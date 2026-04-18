#!/usr/bin/env python3
"""
RollingThunder - POTA SSB Spots Poller (rt-controller, controller-owned state)

Polls the official POTA spots endpoint, filters to SSB/Phone, ages out old spots,
dedupes, and writes Redis keys for downstream consumers.

POTA Spots Endpoint (official):
  https://api.pota.app/spot/activator

Authoritative Redis outputs (controller-owned):
- <ns>:pota:ssb:spot:<spot_id>              HASH detail (TTL)
- <ns>:pota:ssb:spots:<band>                ZSET member=<spot_id>, score=spot_ts_epoch
- <ns>:pota:ssb:dedupe:<utcday>:<band>      SET members=CALL|PARK
- <ns>:pota:ssb:band:<band>                 HASH summary
- <ns>:pota:ssb:bands                       ZSET member=<band>, score=latest/current spot epoch
- <ns>:pota:ssb:spotmeta:<spot_id>          STRING compact JSON metadata sidecar (TTL)

Optional dedupe filtering against "already logged" calls:
- <ns>:pota:context -> JSON or HASH containing selected_ref ("K-xxxxx" or "hunted")
- <ns>:pota:logged:<yyyymmdd>:<context>:<band> -> Redis SET of calls logged

Configuration:
- RT_APP_JSON: path to app.json (default /opt/rollingthunder/config/app.json)
- RT_POTA_URL: override spots endpoint
- RT_POTA_POLL_SEC: poll interval seconds (default 12)
- RT_POTA_HTTP_TIMEOUT_SEC: HTTP timeout (default 6)
- RT_POTA_MAX_AGE_SEC: spot max age seconds (default 1200 = 20 min)
- RT_POTA_MAX_SPOTS_PER_BAND: cap per-band list length (default 250)
- RT_POTA_INCLUDE_AM_FM: "1" to include AM/FM as phone (default "0")
- RT_POTA_SPOTMETA_TTL_SEC: metadata sidecar TTL seconds (default max_age + 900, usually 2700)
- RT_REDIS_URL: override Redis URL (else uses app.json globals.state.redisUrl)
- RT_KEY_PREFIX: override namespace (else uses app.json globals.state.namespace)

Notes:
- app.json is the source of truth for band edges + band ordering.
- Core zset schema is unchanged.
- Sidecar metadata is additive and keyed by the exact zset member string.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
import redis


POTA_URL_DEFAULT = "https://api.pota.app/spot/activator"
POLL_SEC_DEFAULT = 12
MAX_AGE_SEC_DEFAULT = 20 * 60  # 20 minutes
HTTP_TIMEOUT_SEC_DEFAULT = 6
MAX_SPOTS_PER_BAND_DEFAULT = 250
APP_JSON_DEFAULT = "/opt/rollingthunder/config/app.json"

# Treat these as "phone-ish"
PHONE_MODES = {"SSB", "LSB", "USB", "PHONE", "AM", "FM"}
# Output normalization: USB/LSB/PHONE/SSB => "SSB" for first-cut simplicity
STRICT_PHONE_OUTPUT_MODES = {"SSB", "PHONE", "USB", "LSB"}


def make_spot_id(band: str, call: str, park_ref: str, utc_day: str) -> str:
    return f"{band}:{call.upper()}:{park_ref.upper()}:{utc_day}"


def make_worked_member(call: str, park_ref: str) -> str:
    return f"{call.upper()}|{park_ref.upper()}"


def worked_key(prefix: str, utc_day: str, context: str, band: str) -> str:
    return f"{prefix}:pota:worked:{utc_day}:{context}:{band}"


def spot_key(prefix: str, spot_id: str) -> str:
    return f"{prefix}:pota:ssb:spot:{spot_id}"


def spotmeta_key(prefix: str, spot_id: str) -> str:
    return f"{prefix}:pota:ssb:spotmeta:{spot_id}"


def band_spots_key(prefix: str, band: str) -> str:
    return f"{prefix}:pota:ssb:spots:{band}"


def band_summary_key(prefix: str, band: str) -> str:
    return f"{prefix}:pota:ssb:band:{band}"


def band_rank_key(prefix: str) -> str:
    return f"{prefix}:pota:ssb:bands"


def dedupe_key(prefix: str, utc_day: str, band: str) -> str:
    return f"{prefix}:pota:ssb:dedupe:{utc_day}:{band}"


def parse_iso_utc_to_epoch(s: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return None


def epoch_to_iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def stable_band_spots_fingerprint(band_spots: Dict[str, List[Dict[str, Any]]]) -> str:
    """
    Fingerprint only the effective per-band spot state, excluding transient values like
    current poll time. This is used to suppress redundant Redis rewrites.
    """
    canonical: Dict[str, List[Dict[str, Any]]] = {}

    for band in sorted(band_spots.keys()):
        rows: List[Dict[str, Any]] = []
        for s in band_spots[band]:
            raw = s.get("raw") or {}
            rows.append(
                {
                    "call": str(s.get("call") or ""),
                    "freq_hz": int(s.get("freq_hz") or 0),
                    "band": str(s.get("band") or ""),
                    "mode": str(s.get("mode") or ""),
                    "park_ref": str(s.get("park_ref") or ""),
                    "park_name": str(s.get("park_name") or ""),
                    "spot_ts_utc": str(s.get("spot_ts_utc") or ""),
                    "country2": str(s.get("country2") or ""),
                    "state2": str(s.get("state2") or ""),
                    "spotter": str(raw.get("spotter") or ""),
                    "comments": str(raw.get("comments") or ""),
                    "source": str(raw.get("source") or ""),
                    "count": int(raw.get("count") or 0),
                }
            )
        canonical[band] = rows

    payload = compact_json(canonical).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def build_spotmeta_payload(
    *,
    call: str,
    band: str,
    park_ref: str,
    park_name: str,
    freq_hz: Optional[int],
    mode: str,
    spot_ts_epoch: int,
) -> str:
    payload = {
        "call": call,
        "band": band,
        "park_ref": park_ref,
        "park_name": park_name,
        "freq_hz": freq_hz,
        "mode": mode,
        "spot_ts_utc": epoch_to_iso_utc(spot_ts_epoch),
        "spot_ts": spot_ts_epoch,
    }
    return compact_json(payload)


def is_worked(
    r: redis.Redis,
    prefix: str,
    utc_day: str,
    context: str,
    band: str,
    call: str,
    park_ref: str,
) -> bool:
    try:
        return r.sismember(
            worked_key(prefix, utc_day, context, band),
            make_worked_member(call, park_ref),
        )
    except Exception:
        return False


def mark_worked(
    r: redis.Redis,
    prefix: str,
    utc_day: str,
    context: str,
    band: str,
    call: str,
    park_ref: str,
) -> None:
    key = worked_key(prefix, utc_day, context, band)
    member = make_worked_member(call, park_ref)
    pipe = r.pipeline()
    pipe.sadd(key, member)
    pipe.expire(key, 172800)
    pipe.execute()


def load_app_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def dedupe_latest(spots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep the newest spot per (call, park_ref, band).

    Newest is determined by lowest age_sec, which is equivalent to the most
    recent spot inside the current polling window.
    """
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for s in spots:
        key = (
            str(s.get("call") or "").upper(),
            str(s.get("park_ref") or "").upper(),
            str(s.get("band") or ""),
        )
        cur = best.get(key)
        if cur is None or int(s.get("age_sec", 999999)) < int(cur.get("age_sec", 999999)):
            best[key] = s

    out = list(best.values())
    out.sort(key=lambda x: (int(x.get("age_sec", 999999)), str(x.get("call") or "")))
    return out


def get_by_path(obj: Dict[str, Any], path: str, default=None):
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _to_mhz(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except Exception:
        return None

    # Heuristic:
    # MHz values look like 14.0, 14.35
    # kHz values look like 14000, 14350
    # Hz values look like 14000000, 14350000
    if x > 1_000_000:
        return x / 1_000_000.0
    if x > 1_000:
        return x / 1_000.0
    return x


def bands_from_app(app: Dict[str, Any]) -> List[Tuple[str, float, float]]:
    """
    Accepts app band definitions in MHz, kHz, or Hz.
    Expected examples:
      {"20m": {"low_mhz":14.0,"high_mhz":14.35}}
      {"20m": {"low_khz":14000,"high_khz":14350}}
      {"20m": {"low_hz":14000000,"high_hz":14350000}}
    """
    bands = app.get("bands") or {}
    out: List[Tuple[str, float, float]] = []
    if not isinstance(bands, dict):
        return out

    for band, rng in bands.items():
        if not isinstance(rng, dict):
            continue

        lo = None
        hi = None

        for lo_key, hi_key in [
            ("low_mhz", "high_mhz"),
            ("low_khz", "high_khz"),
            ("low_hz", "high_hz"),
        ]:
            if lo_key in rng and hi_key in rng:
                lo = _to_mhz(rng[lo_key])
                hi = _to_mhz(rng[hi_key])
                break

        if lo is None or hi is None:
            if "low" in rng and "high" in rng:
                lo = _to_mhz(rng["low"])
                hi = _to_mhz(rng["high"])

        if lo is None or hi is None:
            continue

        if lo > hi:
            lo, hi = hi, lo

        out.append((str(band), lo, hi))

    return out


def band_order_from_app(app: Dict[str, Any], band_table: List[Tuple[str, float, float]]) -> Dict[str, int]:
    order = app.get("bandOrder")
    if isinstance(order, list) and order:
        return {str(b): i for i, b in enumerate(order)}
    return {b: i for i, (b, _, _) in enumerate(band_table)}


def band_from_mhz(freq_mhz: float, band_table: List[Tuple[str, float, float]]) -> Optional[str]:
    for band, lo, hi in band_table:
        if lo <= freq_mhz <= hi:
            return band
    return None


def parse_spot_time_utc(s: str) -> Optional[datetime]:
    """
    POTA returns spotTime like '2026-03-04T01:18:11' (often no timezone).
    Treat naive timestamps as UTC.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def utc_yyyymmdd(now_utc: datetime) -> str:
    return now_utc.strftime("%Y%m%d")


def safe_float(x: Any) -> Optional[float]:
    try:
        return float(str(x).strip())
    except Exception:
        return None


@dataclass
class Cfg:
    redis_url: str
    pota_url: str
    poll_sec: int
    http_timeout_sec: int
    max_age_sec: int
    max_spots_per_band: int
    include_am_fm: bool
    key_prefix: str
    app_json: str
    spotmeta_ttl_sec: int


class StopFlag:
    stop = False


def _handle_stop(signum, frame):
    StopFlag.stop = True


def connect_redis(redis_url: str) -> redis.Redis:
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    r.ping()
    return r


def read_context_tag(r: redis.Redis, prefix: str) -> str:
    """
    Determines the "context" part of <ns>:pota:logged:<yyyymmdd>:<context>:<band>.
    Priority:
      1) HASH: <ns>:pota:context selected_ref
      2) JSON: <ns>:pota:context {"selected_ref":...}
      3) fallback: "hunted"
    """
    key = f"{prefix}:pota:context"
    try:
        sr = r.hget(key, "selected_ref")
        if sr:
            return str(sr)
    except Exception:
        pass

    try:
        raw = r.get(key)
        if raw:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("selected_ref"):
                return str(obj["selected_ref"])
    except Exception:
        pass

    return "hunted"


def already_logged(r: redis.Redis, prefix: str, yyyymmdd: str, context: str, band: str, call: str) -> bool:
    setkey = f"{prefix}:pota:logged:{yyyymmdd}:{context}:{band}"
    try:
        return r.sismember(setkey, call.upper())
    except Exception:
        return False


def _state2_from_location_desc(location_desc: str) -> Optional[str]:
    """
    locationDesc examples:
      "US-OH"
      "US-FL,US-MS"
    Return a single 2-letter state only if it's unambiguous US-XX.
    """
    if not location_desc:
        return None
    parts = [p.strip() for p in location_desc.split(",") if p.strip()]
    if len(parts) != 1:
        return None
    one = parts[0]
    if not one.startswith("US-"):
        return None
    st = one.split("-", 1)[1].strip()
    return st[:2].upper() if len(st) >= 2 else None


def normalize_spot(
    spot: Dict[str, Any],
    now_utc: datetime,
    include_am_fm: bool,
    band_table: List[Tuple[str, float, float]],
) -> Optional[Dict[str, Any]]:
    """
    Example (fields vary slightly over time):
    {
      "activator":"W6RDG",
      "frequency":"14325",
      "mode":"SSB",
      "reference":"US-3473",
      "name":"Martial Cottle Park...",
      "spotTime":"2026-03-04T01:18:11",
      "locationDesc":"US-CA"
    }
    """
    mode_raw = str(spot.get("mode") or "").strip().upper()
    if mode_raw not in PHONE_MODES:
        return None
    if (not include_am_fm) and mode_raw in {"AM", "FM"}:
        return None

    freq_raw = safe_float(spot.get("frequency"))
    if freq_raw is None:
        return None

    if freq_raw > 1_000_000:
        freq_mhz = freq_raw / 1_000_000.0
    elif freq_raw > 1_000:
        freq_mhz = freq_raw / 1_000.0
    else:
        freq_mhz = freq_raw

    band = band_from_mhz(freq_mhz, band_table)
    if not band:
        return None

    st = parse_spot_time_utc(str(spot.get("spotTime") or ""))
    if not st:
        return None

    age_sec = int((now_utc - st).total_seconds())
    if age_sec < 0:
        age_sec = 0

    call = str(spot.get("activator") or "").strip().upper()
    if not call:
        return None

    location_desc = str(spot.get("locationDesc") or "").strip()
    country2 = ""
    if location_desc:
        country2 = location_desc.split("-", 1)[0][:2].upper()

    park_ref = str(spot.get("reference") or "").strip().upper()
    if not park_ref:
        return None

    return {
        "call": call,
        "freq_hz": int(freq_mhz * 1_000_000),
        "freq_mhz": round(freq_mhz, 4),
        "band": band,
        "mode": "SSB" if mode_raw in STRICT_PHONE_OUTPUT_MODES else mode_raw,
        "park_ref": park_ref,
        "park_name": str(spot.get("name") or spot.get("parkName") or "").strip(),
        "spot_ts_utc": st.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_sec": age_sec,
        "country2": country2,
        "state2": _state2_from_location_desc(location_desc),
        "raw": {
            "spotId": spot.get("spotId"),
            "spotter": spot.get("spotter"),
            "comments": spot.get("comments"),
            "source": spot.get("source"),
            "count": spot.get("count"),
        },
    }


def write_redis(
    r: redis.Redis,
    cfg: Cfg,
    band_spots: Dict[str, List[Dict[str, Any]]],
    band_order: Dict[str, int],
    band_table: List[Tuple[str, float, float]],
    now_utc: datetime,
) -> None:
    """
    v0.3310 Redis model

    Writes:
      - <ns>:pota:ssb:spot:<spot_id>          HASH (TTL ~22 min)
      - <ns>:pota:ssb:spotmeta:<spot_id>      STRING compact JSON metadata sidecar (TTL)
      - <ns>:pota:ssb:spots:<band>            ZSET score=spot_ts member=spot_id
      - <ns>:pota:ssb:dedupe:<utcday>:<band>  SET members=CALL|PARK
      - <ns>:pota:ssb:band:<band>             HASH summary
      - <ns>:pota:ssb:bands                   ZSET score=last_spot_ts member=<band>
    """
    prefix = cfg.key_prefix
    now_ts = int(now_utc.timestamp())
    min_active_ts = now_ts - cfg.max_age_sec
    detail_ttl_sec = cfg.max_age_sec + 120
    dedupe_ttl_sec = 172800
    spotmeta_ttl_sec = cfg.spotmeta_ttl_sec
    bands_zkey = band_rank_key(prefix)

    pipe = r.pipeline()

    # 1) Write per-spot HASHes, sidecars, and per-band ZSET indexes
    for band, spots in band_spots.items():
        zkey = band_spots_key(prefix, band)

        for s in spots:
            call = str(s.get("call") or "").upper()
            park_ref = str(s.get("park_ref") or "").upper()
            if not call or not park_ref:
                continue

            spot_ts = parse_iso_utc_to_epoch(str(s.get("spot_ts_utc") or ""))
            if spot_ts is None:
                continue

            utc_day = datetime.fromtimestamp(spot_ts, tz=timezone.utc).strftime("%Y%m%d")
            spot_id = make_spot_id(band, call, park_ref, utc_day)
            skey = spot_key(prefix, spot_id)
            smkey = spotmeta_key(prefix, spot_id)
            dkey = dedupe_key(prefix, utc_day, band)

            freq_hz = s.get("freq_hz")
            try:
                freq_hz_int = int(freq_hz) if freq_hz is not None else None
            except (TypeError, ValueError):
                freq_hz_int = None

            mapping = {
                "spot_id": spot_id,
                "call": call,
                "band": band,
                "park_ref": park_ref,
                "park_name": str(s.get("park_name") or ""),
                "freq_hz": int(s.get("freq_hz") or 0),
                "freq_mhz": str(s.get("freq_mhz") or ""),
                "mode": str(s.get("mode") or ""),
                "spot_ts_utc": str(s.get("spot_ts_utc") or ""),
                "spot_ts": spot_ts,
                "age_sec": int(s.get("age_sec") or 0),
                "country2": str(s.get("country2") or ""),
                "state2": str(s.get("state2") or ""),
                "spotter": str((s.get("raw") or {}).get("spotter") or ""),
                "comments": str((s.get("raw") or {}).get("comments") or ""),
                "source": str((s.get("raw") or {}).get("source") or ""),
                "count": int((s.get("raw") or {}).get("count") or 0),
            }

            spotmeta_json = build_spotmeta_payload(
                call=call,
                band=band,
                park_ref=park_ref,
                park_name=str(s.get("park_name") or ""),
                freq_hz=freq_hz_int,
                mode=str(s.get("mode") or ""),
                spot_ts_epoch=spot_ts,
            )

            pipe.hset(skey, mapping=mapping)
            pipe.expire(skey, detail_ttl_sec)

            pipe.setex(smkey, spotmeta_ttl_sec, spotmeta_json)

            pipe.zadd(zkey, {spot_id: spot_ts})

            pipe.sadd(dkey, make_worked_member(call, park_ref))
            pipe.expire(dkey, dedupe_ttl_sec)

        pipe.zremrangebyscore(zkey, "-inf", f"({min_active_ts}")

    pipe.execute()

    # 2) Rebuild band summaries and ranking from the active ZSETs
    pipe = r.pipeline()
    pipe.delete(bands_zkey)

    for band, _, _ in band_table:
        zkey = band_spots_key(prefix, band)
        pipe.zremrangebyscore(zkey, "-inf", f"({min_active_ts}")

    pipe.execute()

    active_bands: List[Tuple[str, int, int]] = []
    for band, _, _ in band_table:
        zkey = band_spots_key(prefix, band)
        bkey = band_summary_key(prefix, band)

        count = r.zcard(zkey)
        if count <= 0:
            r.delete(bkey)
            continue

        top = r.zrevrange(zkey, 0, 0, withscores=True)
        last_spot_ts = int(top[0][1]) if top else 0
        active_bands.append((band, count, last_spot_ts))

    pipe = r.pipeline()
    for band, count, last_spot_ts in active_bands:
        bkey = band_summary_key(prefix, band)
        pipe.hset(
            bkey,
            mapping={
                "active_count": count,
                "last_spot_ts": last_spot_ts,
                "last_poll_ts": now_ts,
                "band_order": band_order.get(band, 999),
            },
        )
        pipe.zadd(bands_zkey, {band: last_spot_ts})

    pipe.execute()


def build_cfg_from_env_and_app(app: Dict[str, Any]) -> Cfg:
    app_json = os.getenv("RT_APP_JSON", APP_JSON_DEFAULT)
    key_prefix = os.getenv("RT_KEY_PREFIX") or get_by_path(app, "globals.state.namespace", "rt")

    redis_url = os.getenv("RT_REDIS_URL")
    if not redis_url:
        host = os.getenv("RT_REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("RT_REDIS_PORT", "6379"))
        db = int(os.getenv("RT_REDIS_DB", "0"))

        user = os.getenv("RT_REDIS_USER", "")
        password = os.getenv("RT_REDIS_PASSWORD", "")

        if password:
            if user:
                redis_url = f"redis://{user}:{password}@{host}:{port}/{db}"
            else:
                redis_url = f"redis://:{password}@{host}:{port}/{db}"
        else:
            redis_url = get_by_path(app, "globals.state.redisUrl", f"redis://{host}:{port}/{db}")

    max_age_sec = int(os.getenv("RT_POTA_MAX_AGE_SEC", str(MAX_AGE_SEC_DEFAULT)))
    spotmeta_ttl_sec = int(os.getenv("RT_POTA_SPOTMETA_TTL_SEC", str(max_age_sec + 900)))

    return Cfg(
        redis_url=redis_url,
        pota_url=os.getenv("RT_POTA_URL", POTA_URL_DEFAULT),
        poll_sec=int(os.getenv("RT_POTA_POLL_SEC", str(POLL_SEC_DEFAULT))),
        http_timeout_sec=int(os.getenv("RT_POTA_HTTP_TIMEOUT_SEC", str(HTTP_TIMEOUT_SEC_DEFAULT))),
        max_age_sec=max_age_sec,
        max_spots_per_band=int(os.getenv("RT_POTA_MAX_SPOTS_PER_BAND", str(MAX_SPOTS_PER_BAND_DEFAULT))),
        include_am_fm=os.getenv("RT_POTA_INCLUDE_AM_FM", "0").strip() == "1",
        key_prefix=str(key_prefix),
        app_json=app_json,
        spotmeta_ttl_sec=spotmeta_ttl_sec,
    )


def main() -> int:
    app_path = os.getenv("RT_APP_JSON", APP_JSON_DEFAULT)
    app = load_app_config(app_path)

    cfg = build_cfg_from_env_and_app(app)

    band_table = bands_from_app(app)
    if not band_table:
        band_table = [
            ("160m", 1.8, 2.0),
            ("80m", 3.5, 4.0),
            ("60m", 5.0, 5.5),
            ("40m", 7.0, 7.3),
            ("30m", 10.1, 10.15),
            ("20m", 14.0, 14.35),
            ("17m", 18.068, 18.168),
            ("15m", 21.0, 21.45),
            ("12m", 24.89, 24.99),
            ("10m", 28.0, 29.7),
            ("6m", 50.0, 54.0),
        ]

    band_order = band_order_from_app(app, band_table)

    print(f"[pota_spots_poller] band_table={band_table}", flush=True)
    print(f"[pota_spots_poller] redis_url={cfg.redis_url}", flush=True)
    print(f"[pota_spots_poller] key_prefix={cfg.key_prefix}", flush=True)
    print(f"[pota_spots_poller] spotmeta_ttl_sec={cfg.spotmeta_ttl_sec}", flush=True)

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    r = connect_redis(cfg.redis_url)

    sess = requests.Session()
    sess.headers.update({"User-Agent": "RollingThunder/rt-controller (POTA spots poller)"})

    backoff = 1.0
    last_response_hash: Optional[str] = None
    last_band_spots_fp: Optional[str] = None

    while not StopFlag.stop:
        t0 = time.time()
        now_utc = datetime.now(timezone.utc)

        total_seen = 0
        total_norm = 0
        total_age_dropped = 0

        try:
            resp = sess.get(cfg.pota_url, timeout=cfg.http_timeout_sec)
            resp.raise_for_status()

            response_bytes = resp.content
            response_hash = hashlib.sha1(response_bytes).hexdigest()

            if response_hash == last_response_hash:
                print("[pota_spots_poller] response unchanged; skipping processing", flush=True)
                backoff = 1.0
            else:
                raw_spots = resp.json()
                print(
                    f"[pota_spots_poller] raw_spots={len(raw_spots) if isinstance(raw_spots, list) else 'non-list'}",
                    flush=True,
                )
                if not isinstance(raw_spots, list):
                    raise ValueError("POTA API returned non-list JSON")

                context = read_context_tag(r, cfg.key_prefix)
                yyyymmdd = utc_yyyymmdd(now_utc)

                normalized: List[Dict[str, Any]] = []
                for spot in raw_spots:
                    if not isinstance(spot, dict):
                        continue

                    total_seen += 1
                    s = normalize_spot(spot, now_utc, cfg.include_am_fm, band_table)
                    if not s:
                        continue

                    total_norm += 1

                    if s["age_sec"] > cfg.max_age_sec:
                        total_age_dropped += 1
                        continue

                    # Placeholder for future context-aware filtering; currently preserved
                    _ = context
                    _ = yyyymmdd

                    normalized.append(s)

                pre_dedupe_count = len(normalized)
                normalized = dedupe_latest(normalized)
                post_dedupe_count = len(normalized)

                print(
                    f"[pota_spots_poller] seen={total_seen} normalized={total_norm} "
                    f"age_dropped={total_age_dropped} "
                    f"pre_dedupe={pre_dedupe_count} post_dedupe={post_dedupe_count}",
                    flush=True,
                )

                band_spots: Dict[str, List[Dict[str, Any]]] = {}
                for s in normalized:
                    band_spots.setdefault(s["band"], []).append(s)

                for b in list(band_spots.keys()):
                    band_spots[b] = band_spots[b][: cfg.max_spots_per_band]

                band_spots_fp = stable_band_spots_fingerprint(band_spots)

                if band_spots_fp != last_band_spots_fp:
                    write_redis(r, cfg, band_spots, band_order, band_table, now_utc)
                    last_band_spots_fp = band_spots_fp
                    print("[pota_spots_poller] redis updated", flush=True)
                else:
                    print("[pota_spots_poller] effective state unchanged; skipping redis write", flush=True)

                last_response_hash = response_hash
                backoff = 1.0

        except Exception as e:
            print(f"[pota_spots_poller] error: {e}", file=sys.stderr, flush=True)
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 1.7, 30.0)

        elapsed = time.time() - t0
        sleep_for = max(0.2, cfg.poll_sec - elapsed)

        end = time.time() + sleep_for
        while (time.time() < end) and (not StopFlag.stop):
            time.sleep(0.1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())