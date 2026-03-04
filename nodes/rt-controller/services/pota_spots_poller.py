#!/usr/bin/env python3
"""
RollingThunder - POTA SSB Spots Poller (rt-controller, controller-owned state)

Polls the official POTA spots endpoint, filters to SSB/Phone, ages out old spots
(> 20 minutes), dedupes, and writes Redis keys for the UI to read.

POTA Spots Endpoint (official):
  https://api.pota.app/spot/activator

Redis outputs (authoritative, controller-owned):
- <ns>:pota:ssb:bands -> JSON list: [{band:"20m", count:14}, ...]
- <ns>:pota:ssb:spots:<band> -> JSON list of normalized spots

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
- RT_REDIS_URL: override Redis URL (else uses app.json globals.state.redisUrl)
- RT_KEY_PREFIX: override namespace (else uses app.json globals.state.namespace)

Notes:
- app.json is the source of truth for band edges + band ordering.
"""

from __future__ import annotations

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


def load_app_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def get_by_path(obj: Dict[str, Any], path: str, default=None):
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def bands_from_app(app: Dict[str, Any]) -> List[Tuple[str, float, float]]:
    """
    Reads:
      "bands": { "20m": {"low_mhz":14.0,"high_mhz":14.35}, ... }
    Returns list of (band, low_mhz, high_mhz)
    """
    bands = app.get("bands") or {}
    out: List[Tuple[str, float, float]] = []
    if not isinstance(bands, dict):
        return out
    for band, rng in bands.items():
        if not isinstance(rng, dict):
            continue
        try:
            lo = float(rng["low_mhz"])
            hi = float(rng["high_mhz"])
            out.append((str(band), lo, hi))
        except Exception:
            continue
    return out


def band_order_from_app(app: Dict[str, Any], band_table: List[Tuple[str, float, float]]) -> Dict[str, int]:
    order = app.get("bandOrder")
    if isinstance(order, list) and order:
        return {str(b): i for i, b in enumerate(order)}
    # fallback: the order we got from the table
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
    key_prefix: str  # namespace, e.g. "rt"
    app_json: str


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
        # fail-open (don’t hide spots) if Redis hiccups
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

    freq = safe_float(spot.get("frequency"))
    if freq is None:
        return None

    # POTA API uses kHz (e.g., 7074.0 -> 7.074 MHz). If it ever sends MHz, it will be < 1000.
    freq_mhz = freq / 1000.0 if freq > 1000.0 else freq
    if freq_mhz is None:
        return None

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
        # "US-OH" => "US"
        country2 = location_desc.split("-", 1)[0][:2].upper()

    return {
        "call": call,
        "freq_hz": int(freq_mhz * 1_000_000),
        "freq_mhz": round(freq_mhz, 4),
        "band": band,
        "mode": "SSB" if mode_raw in STRICT_PHONE_OUTPUT_MODES else mode_raw,
        "park_ref": str(spot.get("reference") or "").strip(),
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


def dedupe_latest(spots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep newest spot per (call, park_ref, band). Newest == lowest age_sec.
    """
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for s in spots:
        key = (s["call"], s.get("park_ref", ""), s["band"])
        cur = best.get(key)
        if not cur or s["age_sec"] < cur["age_sec"]:
            best[key] = s
    out = list(best.values())
    out.sort(key=lambda x: (x["age_sec"], x["call"]))
    return out


def write_redis(
    r: redis.Redis,
    cfg: Cfg,
    band_spots: Dict[str, List[Dict[str, Any]]],
    band_order: Dict[str, int],
    band_table: List[Tuple[str, float, float]],
) -> None:
    # bands summary
    bands = [{"band": b, "count": len(lst)} for b, lst in band_spots.items() if lst]
    bands.sort(key=lambda x: band_order.get(x["band"], 999))
    r.set(f"{cfg.key_prefix}:pota:ssb:bands", json.dumps(bands, separators=(",", ":")))

    # per-band lists
    for band, spots in band_spots.items():
        key = f"{cfg.key_prefix}:pota:ssb:spots:{band}"
        r.set(key, json.dumps(spots, separators=(",", ":")))
        # short TTL so stale data evaporates if poller dies
        r.expire(key, cfg.poll_sec * 5)

    # expire band keys that are currently empty/missing so old data doesn’t linger
    for band, _, _ in band_table:
        if band not in band_spots:
            key = f"{cfg.key_prefix}:pota:ssb:spots:{band}"
            r.expire(key, cfg.poll_sec * 2)


def build_cfg_from_env_and_app(app: Dict[str, Any]) -> Cfg:
    app_json = os.getenv("RT_APP_JSON", APP_JSON_DEFAULT)

    key_prefix = os.getenv("RT_KEY_PREFIX") or get_by_path(app, "globals.state.namespace", "rt")

    # Prefer explicit URL if provided
    redis_url = os.getenv("RT_REDIS_URL")
    if not redis_url:
        # Next: assemble from host/port/db + password (common RollingThunder pattern)
        host = os.getenv("RT_REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("RT_REDIS_PORT", "6379"))
        db = int(os.getenv("RT_REDIS_DB", "0"))

        user = os.getenv("RT_REDIS_USER", "")  # optional for ACL
        password = os.getenv("RT_REDIS_PASSWORD", "")

        if password:
            # ACL user optional; if user missing use :password
            if user:
                redis_url = f"redis://{user}:{password}@{host}:{port}/{db}"
            else:
                redis_url = f"redis://:{password}@{host}:{port}/{db}"
        else:
            # Final fallback: app.json value (may be unauthenticated)
            redis_url = get_by_path(app, "globals.state.redisUrl", f"redis://{host}:{port}/{db}")

    return Cfg(
        redis_url=redis_url,
        pota_url=os.getenv("RT_POTA_URL", POTA_URL_DEFAULT),
        poll_sec=int(os.getenv("RT_POTA_POLL_SEC", str(POLL_SEC_DEFAULT))),
        http_timeout_sec=int(os.getenv("RT_POTA_HTTP_TIMEOUT_SEC", str(HTTP_TIMEOUT_SEC_DEFAULT))),
        max_age_sec=int(os.getenv("RT_POTA_MAX_AGE_SEC", str(MAX_AGE_SEC_DEFAULT))),
        max_spots_per_band=int(os.getenv("RT_POTA_MAX_SPOTS_PER_BAND", str(MAX_SPOTS_PER_BAND_DEFAULT))),
        include_am_fm=os.getenv("RT_POTA_INCLUDE_AM_FM", "0").strip() == "1",
        key_prefix=str(key_prefix),
        app_json=app_json,
    )


def main() -> int:
    # Load app.json first (so cfg defaults come from it)
    app_path = os.getenv("RT_APP_JSON", APP_JSON_DEFAULT)
    app = load_app_config(app_path)

    cfg = build_cfg_from_env_and_app(app)

    band_table = bands_from_app(app)
    if not band_table:
        # safety fallback (should not trigger with your current app.json)
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

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    r = connect_redis(cfg.redis_url)

    sess = requests.Session()
    sess.headers.update({"User-Agent": "RollingThunder/rt-controller (POTA spots poller)"})

    backoff = 1.0

    while not StopFlag.stop:
        t0 = time.time()
        now_utc = datetime.now(timezone.utc)

        try:
            resp = sess.get(cfg.pota_url, timeout=cfg.http_timeout_sec)
            resp.raise_for_status()

            raw_spots = resp.json()
            if not isinstance(raw_spots, list):
                raise ValueError("POTA API returned non-list JSON")

            context = read_context_tag(r, cfg.key_prefix)
            yyyymmdd = utc_yyyymmdd(now_utc)

            normalized: List[Dict[str, Any]] = []
            for spot in raw_spots:
                if not isinstance(spot, dict):
                    continue

                s = normalize_spot(spot, now_utc, cfg.include_am_fm, band_table)
                if not s:
                    continue

                # age out (>20 minutes by default)
                if s["age_sec"] > cfg.max_age_sec:
                    continue

                # filter out already-logged calls for this day/context/band
                if already_logged(r, cfg.key_prefix, yyyymmdd, context, s["band"], s["call"]):
                    continue

                normalized.append(s)

            normalized = dedupe_latest(normalized)

            band_spots: Dict[str, List[Dict[str, Any]]] = {}
            for s in normalized:
                band_spots.setdefault(s["band"], []).append(s)

            # cap per band
            for b in list(band_spots.keys()):
                band_spots[b] = band_spots[b][: cfg.max_spots_per_band]

            write_redis(r, cfg, band_spots, band_order, band_table)

            backoff = 1.0

        except Exception as e:
            print(f"[pota_spots_poller] error: {e}", file=sys.stderr)
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