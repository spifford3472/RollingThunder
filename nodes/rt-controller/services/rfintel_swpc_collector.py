#!/usr/bin/env python3
"""
RollingThunder - RF Intel NOAA/SWPC Snapshot Collector

Controller-side collector for the RF Intel / propagation page.

Writes only:
  rt:rfintel:solar

Publishes only:
  rt:system:bus state.changed

Does not write to rt:ui:bus.
Does not inspect UI state.
Does not make band/advisor/map recommendations.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping

import redis


DEFAULT_REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
DEFAULT_REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
DEFAULT_REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
DEFAULT_REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

SOLAR_KEY = os.environ.get("RT_KEY_RFINTEL_SOLAR", "rt:rfintel:solar")
SYSTEM_BUS = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")

DEFAULT_INTERVAL_SEC = int(os.environ.get("RT_RFINTEL_SWPC_INTERVAL_SEC", "900"))
HTTP_TIMEOUT_SEC = float(os.environ.get("RT_RFINTEL_SWPC_HTTP_TIMEOUT_SEC", "8"))
STALE_AFTER_SEC = int(os.environ.get("RT_RFINTEL_SWPC_STALE_AFTER_SEC", "1800"))

SOURCE = "NOAA SWPC"
SERVICE_SOURCE = "rfintel_swpc_collector"

URL_KP = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
URL_F107 = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
URL_SCALES = "https://services.swpc.noaa.gov/products/noaa-scales.json"
URL_SUNSPOTS = "https://services.swpc.noaa.gov/json/solar-cycle/sunspots.json"

#SOLAR_IMAGE_URL = "https://www.swpc.noaa.gov/products/goes-solar-ultraviolet-imager-suvi"
SOLAR_IMAGE_URL = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_HMIIF.jpg"

_running = True


def now_ms() -> int:
    return int(time.time() * 1000)


def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_time_tag(value: Any) -> str | None:
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    if s.endswith("Z"):
        return s

    if "T" in s:
        return f"{s}Z"

    return s


def parse_dt(value: Any) -> datetime | None:
    s = normalize_time_tag(value)
    if not s:
        return None

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def latest_utc(values: list[str | None]) -> str:
    candidates = [parse_dt(v) for v in values if v]
    candidates = [v for v in candidates if v is not None]
    if not candidates:
        return now_utc()

    return (
        max(candidates)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def safe_int(value: Any) -> int | None:
    f = safe_float(value)
    if f is None:
        return None
    return int(round(f))


def rounded(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def http_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RollingThunder RFIntel SWPC Collector",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def newest_dict(rows: Any, time_fields: tuple[str, ...] = ("time_tag", "time-tag", "date", "time")) -> dict[str, Any] | None:
    if not isinstance(rows, list) or not rows:
        return None

    dicts = [r for r in rows if isinstance(r, dict)]
    if not dicts:
        return None

    def score(row: dict[str, Any]) -> datetime:
        for field in time_fields:
            dt = parse_dt(row.get(field))
            if dt is not None:
                return dt
        return datetime.min.replace(tzinfo=timezone.utc)

    return max(dicts, key=score)


def first_number_from_keys(row: Mapping[str, Any] | None, keys: tuple[str, ...]) -> float | None:
    if not isinstance(row, Mapping):
        return None

    lower_map = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        value = lower_map.get(key.lower())
        f = safe_float(value)
        if f is not None:
            return f

    return None


def first_time_from_row(row: Mapping[str, Any] | None) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in ("time_tag", "time-tag", "date", "time", "observed_date", "time_stamp", "TimeStamp"):
        value = row.get(key)
        s = normalize_time_tag(value)
        if s:
            return s
    return None


def fetch_kp() -> tuple[float | None, int | None, str | None]:
    rows = http_json(URL_KP)
    row = newest_dict(rows)
    kp = first_number_from_keys(row, ("Kp", "kp"))
    a_running = safe_int(first_number_from_keys(row, ("a_running", "a", "a_index")))
    return kp, a_running, first_time_from_row(row)


def fetch_f107() -> tuple[int | None, str | None]:
    rows = http_json(URL_F107)
    row = newest_dict(rows)
    flux = safe_int(first_number_from_keys(row, ("flux", "f107", "f10.7", "observed_flux")))
    return flux, first_time_from_row(row)


def fetch_noaa_scales() -> tuple[str | None, str | None, str | None]:
    data = http_json(URL_SCALES)
    if not isinstance(data, dict):
        return None, None, None

    current = data.get("0")
    if not isinstance(current, dict):
        return None, None, None

    radio = current.get("R") if isinstance(current.get("R"), dict) else {}
    geomag = current.get("G") if isinstance(current.get("G"), dict) else {}
    solar_rad = current.get("S") if isinstance(current.get("S"), dict) else {}

    r_scale = str(radio.get("Scale") or "0")
    r_text = str(radio.get("Text") or "none")
    g_scale = str(geomag.get("Scale") or "0")
    s_scale = str(solar_rad.get("Scale") or "0")

    xray_status = f"R{r_scale} {r_text}".strip()
    scales_summary = f"G{g_scale} S{s_scale} R{r_scale}"

    date_s = str(current.get("DateStamp") or "").strip()
    time_s = str(current.get("TimeStamp") or "").strip()
    ts = normalize_time_tag(f"{date_s}T{time_s}" if date_s and time_s else None)

    return xray_status, scales_summary, ts


def fetch_sunspots() -> tuple[int | None, str | None]:
    rows = http_json(URL_SUNSPOTS)
    row = newest_dict(rows, time_fields=("time-tag", "time_tag", "date", "time"))
    ssn = first_number_from_keys(
        row,
        (
            "ssn",
            "sunspot_number",
            "sunspots",
            "observed_ssn",
            "monthly_sunspot_number",
            "smoothed_ssn",
        ),
    )
    return safe_int(ssn), first_time_from_row(row)


def condition_from_kp(kp: float | None) -> str:
    if kp is None:
        return "Unknown"
    if kp < 4:
        return "Quiet"
    if kp < 5:
        return "Unsettled"
    if kp < 6:
        return "Minor storm"
    if kp < 7:
        return "Moderate storm"
    if kp < 8:
        return "Strong storm"
    return "Severe storm"


def build_ok_model(verbose: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    timestamps: list[str | None] = []

    kp: float | None = None
    a_index: int | None = None
    sfi: int | None = None
    xray_status: str | None = None
    scales_summary: str | None = None
    sunspot_number: int | None = None

    try:
        kp, a_index, ts = fetch_kp()
        timestamps.append(ts)
    except Exception as e:
        errors.append(f"kp:{type(e).__name__}")

    try:
        sfi, ts = fetch_f107()
        timestamps.append(ts)
    except Exception as e:
        errors.append(f"sfi:{type(e).__name__}")

    try:
        xray_status, scales_summary, ts = fetch_noaa_scales()
        timestamps.append(ts)
    except Exception as e:
        errors.append(f"scales:{type(e).__name__}")

    try:
        sunspot_number, ts = fetch_sunspots()
        timestamps.append(ts)
    except Exception as e:
        errors.append(f"sunspots:{type(e).__name__}")

    if kp is None and sfi is None and xray_status is None and sunspot_number is None:
        raise RuntimeError(";".join(errors) if errors else "no usable SWPC fields")

    condition = condition_from_kp(kp)
    updated_utc = latest_utc(timestamps)

    model: dict[str, Any] = {
        "status": "ok" if not errors else "partial",
        "condition": condition,
        "title": "NOAA SWPC Solar Status",
        "solar_status": condition,
        "k_index": rounded(kp, 1),
        "kp": rounded(kp, 2),
        "sfi": sfi,
        "solar_flux": sfi,
        "a_index": a_index,
        "sunspot_number": sunspot_number,
        "xray_status": xray_status or "—",
        "swpc_scales": scales_summary or "",
        "image_url": SOLAR_IMAGE_URL,
        "updated_utc": updated_utc,
        "generated_ms": now_ms(),
        "source": SOURCE,
        "mock": False,
        "stale_after_sec": STALE_AFTER_SEC,
        "message": "Live NOAA/SWPC snapshot. No band scoring or advisor logic applied.",
    }

    if errors:
        model["warning"] = "partial SWPC data"
        model["errors"] = errors[:6]

    return {k: v for k, v in model.items() if v is not None}


def safe_error_text(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    raw = raw.replace("\n", " ").replace("\r", " ")
    if len(raw) > 180:
        raw = raw[:177] + "..."
    return raw


def build_offline_model(error: str, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    updated = now_utc()

    if (
        isinstance(previous, Mapping)
        and previous.get("status") == "offline"
        and previous.get("error") == error
        and previous.get("updated_utc")
    ):
        updated = str(previous.get("updated_utc"))

    return {
        "status": "offline",
        "condition": "Space weather data unavailable",
        "title": "NOAA SWPC Solar Status",
        "solar_status": "Space weather data unavailable",
        "xray_status": "—",
        "image_url": SOLAR_IMAGE_URL,
        "updated_utc": updated,
        "generated_ms": now_ms(),
        "source": SOURCE,
        "mock": False,
        "stale_after_sec": STALE_AFTER_SEC,
        "error": error,
        "message": "NOAA/SWPC data is currently unavailable. Last good data is kept in service memory only.",
    }


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def comparable_model(value: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != "generated_ms"}


def load_current_model(r: redis.Redis) -> dict[str, Any] | None:
    raw = r.get(SOLAR_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def publish_state_changed(r: redis.Redis, keys: list[str]) -> None:
    ts = now_ms()
    event = {
        "topic": "state.changed",
        "payload": {"keys": keys, "changed_keys": keys, "ts_ms": ts},
        "ts_ms": ts,
        "source": SERVICE_SOURCE,
    }
    r.publish(SYSTEM_BUS, json.dumps(event, sort_keys=True, separators=(",", ":")))


def write_if_changed(r: redis.Redis, model: dict[str, Any], verbose: bool = False) -> bool:
    current = load_current_model(r)

    if isinstance(current, dict):
        if canonical_json(comparable_model(current)) == canonical_json(comparable_model(model)):
            if verbose:
                print("No semantic change; Redis write skipped.")
            return False

    r.set(SOLAR_KEY, canonical_json(model))
    publish_state_changed(r, [SOLAR_KEY])

    if verbose:
        print(f"Wrote {SOLAR_KEY}: {model.get('status')} {model.get('condition')}")
        print(json.dumps(model, indent=2, sort_keys=True))

    return True


def poll_once(r: redis.Redis, verbose: bool = False) -> bool:
    previous = load_current_model(r)

    try:
        model = build_ok_model(verbose=verbose)
    except Exception as e:
        model = build_offline_model(safe_error_text(e), previous=previous)

    return write_if_changed(r, model, verbose=verbose)


def redis_client(args: argparse.Namespace) -> redis.Redis:
    r = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        password=args.redis_password,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )
    r.ping()
    return r


def handle_signal(signum: int, frame: Any) -> None:
    global _running
    _running = False


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RollingThunder NOAA/SWPC RF Intel collector")
    p.add_argument("--interval-sec", type=int, default=DEFAULT_INTERVAL_SEC)
    p.add_argument("--redis-host", default=DEFAULT_REDIS_HOST)
    p.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    p.add_argument("--redis-db", type=int, default=DEFAULT_REDIS_DB)
    p.add_argument("--redis-password", default=DEFAULT_REDIS_PASSWORD)
    p.add_argument("--once", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    r = redis_client(args)

    if args.once:
        poll_once(r, verbose=args.verbose)
        return 0

    if args.verbose:
        print(f"{SERVICE_SOURCE} starting; interval={args.interval_sec}s key={SOLAR_KEY}")

    while _running:
        poll_once(r, verbose=args.verbose)

        slept = 0
        while _running and slept < args.interval_sec:
            time.sleep(1)
            slept += 1

    if args.verbose:
        print(f"{SERVICE_SOURCE} stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))