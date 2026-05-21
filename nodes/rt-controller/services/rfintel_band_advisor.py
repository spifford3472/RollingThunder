#!/usr/bin/env python3
"""
RollingThunder RF Intel Band Advisor v1

Controller-side service only.

Reads only:
  - rt:rfintel:solar
  - rt:hf:spots:selected
  - rt:radio:state
  - rt:gps:pos

Writes only:
  - rt:rfintel:bands

Publishes only:
  - state.changed to rt:system:bus

Does NOT:
  - scan Redis
  - read UI state
  - emit UI intents
  - write to rt:ui:bus
  - call browser APIs
  - perform advanced propagation modeling
"""

import argparse
import json
import math
import sys
import time
import os
from datetime import datetime, timezone, timedelta

try:
    import redis
except ImportError:
    print("ERROR: python3 redis module is required. Try: sudo apt install python3-redis", file=sys.stderr)
    raise


KEY_SOLAR = "rt:rfintel:solar"
KEY_SPOTS = "rt:hf:spots:selected"
KEY_RADIO = "rt:radio:state"
KEY_GPS = "rt:gps:pos"
KEY_OUT = "rt:rfintel:bands"

SYSTEM_BUS = "rt:system:bus"
SOURCE = "rfintel_band_advisor"

BANDS = ["10m", "12m", "15m", "17m", "20m", "30m", "40m", "60m", "80m"]


def now_utc():
    return datetime.now(timezone.utc)


def iso_utc(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def clamp_int(value, low=0, high=100):
    return max(low, min(high, int(round(value))))


def bucket_score(value):
    """
    Bucket to 5-point steps to avoid Redis churn from tiny differences.
    """
    return clamp_int(round(value / 5) * 5)


def safe_json_loads(raw):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def input_status(value):
    if value is None:
        return "missing"
    if isinstance(value, dict) and value.get("status") in ("error", "unavailable"):
        return str(value.get("status"))
    return "ok"


def parse_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def extract_lon(gps):
    if not isinstance(gps, dict):
        return None

    for key in ("lon", "lng", "longitude"):
        if key in gps:
            lon = parse_float(gps.get(key))
            if lon is not None and -180.0 <= lon <= 180.0:
                return lon

    return None


def local_hour_from_lon(dt_utc, lon):
    """
    Approximate solar-local clock from longitude when GPS longitude is available.
    15 degrees longitude ~= 1 hour.

    If longitude is missing, use the controller's configured local timezone
    instead of raw UTC. This is better for RollingThunder in normal operation
    when GPS has not populated yet.
    """
    if lon is None:
        local_dt = dt_utc.astimezone()
        return local_dt.hour + local_dt.minute / 60.0

    offset_hours = lon / 15.0
    local_dt = dt_utc + timedelta(hours=offset_hours)
    return local_dt.hour + local_dt.minute / 60.0


def day_phase(dt_utc, gps):
    """
    Return a coarse operating phase for simple v1 band heuristics.

    This is intentionally not a scientific sunrise/sunset model.

    Phases:
      night   = late evening through early morning
      dawn    = early pre-sunrise / sunrise transition
      day     = broad daylight operating window
      evening = sunset / post-sunset transition

    If GPS longitude is available, use approximate solar-local time.
    If GPS is missing, use the controller's local timezone.
    """
    lon = extract_lon(gps)
    hour = local_hour_from_lon(dt_utc, lon)

    if 7.5 <= hour < 17.5:
        return "day"

    if 17.5 <= hour < 20.5:
        return "evening"

    if 5.5 <= hour < 7.5:
        return "dawn"

    return "night"

def extract_sfi(solar):
    if not isinstance(solar, dict):
        return None
    return parse_float(
        solar.get("sfi", solar.get("solar_flux", solar.get("solarFlux")))
    )


def extract_kp(solar):
    if not isinstance(solar, dict):
        return None
    return parse_float(
        solar.get("kp", solar.get("k_index", solar.get("kindex")))
    )


def extract_solar_condition(solar):
    if not isinstance(solar, dict):
        return ""
    return str(
        solar.get("condition")
        or solar.get("solar_status")
        or solar.get("swpc_scales")
        or ""
    ).strip()


def extract_spot_items(spots_model):
    if not isinstance(spots_model, dict):
        return []

    for key in ("items", "spots", "rows"):
        value = spots_model.get(key)
        if isinstance(value, list):
            return value

    return []


def count_spots_by_band(spots_model):
    counts = {band: 0 for band in BANDS}
    items = extract_spot_items(spots_model)

    for item in items:
        if not isinstance(item, dict):
            continue
        band = str(item.get("band") or "").strip()
        if band in counts:
            counts[band] += 1

    selected_band = None
    if isinstance(spots_model, dict):
        selected_band = str(
            spots_model.get("selected_band") or spots_model.get("band") or ""
        ).strip()

    return counts, selected_band, len(items)


def band_from_freq_hz(freq_hz):
    f = parse_float(freq_hz)
    if f is None:
        return None

    # Accept either Hz or MHz-ish values.
    if f < 1000:
        mhz = f
    else:
        mhz = f / 1_000_000.0

    ranges = [
        ("80m", 3.3, 4.1),
        ("60m", 5.0, 5.5),
        ("40m", 6.8, 7.4),
        ("30m", 10.0, 10.2),
        ("20m", 13.8, 14.5),
        ("17m", 17.9, 18.2),
        ("15m", 20.8, 21.6),
        ("12m", 24.7, 25.0),
        ("10m", 27.5, 30.0),
    ]

    for band, low, high in ranges:
        if low <= mhz <= high:
            return band

    return None


def extract_radio_band(radio):
    if not isinstance(radio, dict):
        return None

    for key in ("band", "current_band", "selected_band"):
        value = str(radio.get(key) or "").strip()
        if value in BANDS:
            return value

    for key in ("freq_hz", "frequency_hz", "freq", "frequency"):
        band = band_from_freq_hz(radio.get(key))
        if band:
            return band

    return None


def base_score_for_phase(band, phase):
    if phase == "day":
        return {
            "10m": 42,
            "12m": 45,
            "15m": 55,
            "17m": 62,
            "20m": 72,
            "30m": 55,
            "40m": 48,
            "60m": 35,
            "80m": 28,
        }[band]

    if phase in ("dawn", "evening"):
        return {
            "10m": 28,
            "12m": 32,
            "15m": 42,
            "17m": 55,
            "20m": 68,
            "30m": 62,
            "40m": 65,
            "60m": 52,
            "80m": 45,
        }[band]

    return {
        "10m": 12,
        "12m": 16,
        "15m": 25,
        "17m": 38,
        "20m": 52,
        "30m": 58,
        "40m": 72,
        "60m": 65,
        "80m": 58,
    }[band]


def solar_adjustment(band, sfi):
    if sfi is None:
        return 0

    high_bands = {"10m", "12m", "15m"}
    mid_bands = {"17m", "20m"}

    if band in high_bands:
        if sfi >= 180:
            return 18
        if sfi >= 140:
            return 12
        if sfi >= 110:
            return 6
        if sfi < 80:
            return -10

    if band in mid_bands:
        if sfi >= 140:
            return 8
        if sfi >= 100:
            return 4
        if sfi < 75:
            return -6

    return 0


def storm_adjustment(band, kp, condition):
    text = condition.lower()
    stormy = "storm" in text or "g1" in text or "g2" in text or "g3" in text or "g4" in text or "g5" in text

    if kp is None:
        kp = 0

    penalty = 0
    if kp >= 7 or stormy:
        penalty = 25
    elif kp >= 5:
        penalty = 16
    elif kp >= 4:
        penalty = 8

    if penalty <= 0:
        return 0

    if band in {"10m", "12m", "15m"}:
        return -penalty
    if band in {"17m", "20m"}:
        return -round(penalty * 0.65)
    return -round(penalty * 0.35)


def spot_adjustment(count):
    if count <= 0:
        return 0
    if count == 1:
        return 8
    if count == 2:
        return 14
    if count <= 5:
        return 20
    return 25


def status_for_score(score):
    if score >= 75:
        return "recommended"
    if score >= 55:
        return "usable"
    if score >= 35:
        return "marginal"
    return "poor"


def confidence_for(score, inputs, spot_count, kp):
    ok_count = sum(1 for v in inputs.values() if v == "ok")

    if kp is not None and kp >= 5:
        return "low"

    if ok_count >= 3 and spot_count > 0 and score >= 55:
        return "medium"

    if ok_count >= 3 and score >= 75:
        return "medium"

    # Keep v1 conservative. Do not claim high confidence yet.
    return "low"


def mode_for_band(band):
    # SSB recommendation only for this first version.
    return "SSB"


def reason_for_band(band, phase, score, spot_count, sfi, kp, radio_band, inputs):
    parts = []

    if spot_count > 0:
        parts.append(f"{spot_count} current spot{'s' if spot_count != 1 else ''} seen on {band}")

    if phase == "day":
        if band in {"10m", "12m", "15m", "17m", "20m"}:
            parts.append("daylight favors this band")
        elif band in {"40m", "60m", "80m"}:
            parts.append("daylight makes this more of a fallback band")
    elif phase in ("dawn", "evening"):
        parts.append("transition-period timing may support this band")
    else:
        if band in {"40m", "60m", "80m"}:
            parts.append("nighttime favors lower HF bands")
        elif band == "20m":
            parts.append("20m can remain usable but is less certain at night")
        else:
            parts.append("nighttime reduces confidence on higher bands")

    if sfi is not None:
        if sfi >= 110 and band in {"10m", "12m", "15m", "17m", "20m"}:
            parts.append(f"SFI {int(round(sfi))} supports daylight HF potential")
        elif sfi < 80 and band in {"10m", "12m", "15m", "17m", "20m"}:
            parts.append(f"SFI {int(round(sfi))} limits higher-band expectations")

    if kp is not None and kp >= 4:
        parts.append(f"Kp {kp:g} lowers confidence")

    if radio_band == band:
        parts.append("small continuity boost for the currently tuned band")

    if inputs.get("spots") != "ok":
        parts.append("no current spot model available")
    elif spot_count == 0:
        parts.append("no current spot density boost")

    if not parts:
        parts.append("generic UTC-based band heuristic")

    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def summarize(items):
    if not items:
        return "No band recommendations generated."

    top = items[0]
    return f"{top['band']} is the strongest current recommendation."


def semantic_copy(model):
    """
    Remove generated fields before comparing current and previous model.
    """
    if not isinstance(model, dict):
        return model

    copied = json.loads(json.dumps(model, sort_keys=True))
    copied.pop("generated_ms", None)
    copied.pop("updated_utc", None)
    return copied


def build_model(solar, spots_model, radio, gps):
    dt = now_utc()
    generated_ms = int(time.time() * 1000)

    inputs = {
        "solar": input_status(solar),
        "spots": input_status(spots_model),
        "radio": input_status(radio),
        "gps": input_status(gps),
    }

    phase = day_phase(dt, gps)
    sfi = extract_sfi(solar)
    kp = extract_kp(solar)
    condition = extract_solar_condition(solar)

    spot_counts, selected_spot_band, total_spots = count_spots_by_band(spots_model)
    radio_band = extract_radio_band(radio)

    items = []

    for band in BANDS:
        score = base_score_for_phase(band, phase)
        score += solar_adjustment(band, sfi)
        score += storm_adjustment(band, kp, condition)
        score += spot_adjustment(spot_counts.get(band, 0))

        if selected_spot_band == band:
            score += 4

        if radio_band == band:
            score += 5

        score = bucket_score(score)
        status = status_for_score(score)
        confidence = confidence_for(score, inputs, spot_counts.get(band, 0), kp)

        reason = reason_for_band(
            band=band,
            phase=phase,
            score=score,
            spot_count=spot_counts.get(band, 0),
            sfi=sfi,
            kp=kp,
            radio_band=radio_band,
            inputs=inputs,
        )

        items.append({
            "band": band,
            "score": score,
            "confidence": confidence,
            "mode": mode_for_band(band),
            "reason": reason,
            "recommendation": reason,
            "status": status,
        })

    items.sort(key=lambda x: (-x["score"], BANDS.index(x["band"])))

    model = {
        "status": "ok",
        "source": SOURCE,
        "mock": False,
        "updated_utc": iso_utc(dt),
        "generated_ms": generated_ms,
        "inputs": inputs,
        "phase": phase,
        "summary": summarize(items),
        "items": items,
    }

    if sfi is not None or kp is not None:
        model["solar"] = {}
        if sfi is not None:
            model["solar"]["sfi"] = int(round(sfi))
        if kp is not None:
            model["solar"]["kp"] = round(kp, 2)

    if total_spots:
        model["spot_count"] = total_spots

    if radio_band:
        model["radio_band"] = radio_band

    return model


def read_inputs(r):
    values = r.mget(KEY_SOLAR, KEY_SPOTS, KEY_RADIO, KEY_GPS)
    return tuple(safe_json_loads(v) for v in values)


def publish_state_changed(r):
    event = {
        "type": "state.changed",
        "source": SOURCE,
        "keys": [KEY_OUT],
        "changed_keys": [KEY_OUT],
        "updated_utc": iso_utc(now_utc()),
        "generated_ms": int(time.time() * 1000),
    }
    r.publish(SYSTEM_BUS, json.dumps(event, separators=(",", ":"), sort_keys=True))


def write_if_changed(r, model, verbose=False):
    raw_prev = r.get(KEY_OUT)
    prev = safe_json_loads(raw_prev)

    if semantic_copy(prev) == semantic_copy(model):
        if verbose:
            print("No semantic change; Redis write skipped.")
        return False

    payload = json.dumps(model, separators=(",", ":"), sort_keys=True)
    r.set(KEY_OUT, payload)
    publish_state_changed(r)

    if verbose:
        print(f"Wrote {KEY_OUT}")
        print(json.dumps(model, indent=2, sort_keys=True))

    return True


def connect_redis(args):
    kwargs = {
        "host": args.redis_host,
        "port": args.redis_port,
        "db": args.redis_db,
        "decode_responses": False,
    }

    if args.redis_password:
        kwargs["password"] = args.redis_password

    return redis.Redis(**kwargs)


def run_once(r, verbose=False):
    solar, spots_model, radio, gps = read_inputs(r)
    model = build_model(solar, spots_model, radio, gps)
    return write_if_changed(r, model, verbose=verbose)


def main():
    parser = argparse.ArgumentParser(description="RollingThunder RF Intel Band Advisor v1")
    parser.add_argument(
        "--interval-sec",
        type=int,
        default=int(os.environ.get("RT_RFI_BAND_ADVISOR_INTERVAL_SEC", "90")),
    )
    parser.add_argument(
        "--redis-host",
        default=os.environ.get("RT_REDIS_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=int(os.environ.get("RT_REDIS_PORT", "6379")),
    )
    parser.add_argument(
        "--redis-password",
        default=os.environ.get("RT_REDIS_PASSWORD"),
    )
    parser.add_argument(
        "--redis-db",
        type=int,
        default=int(os.environ.get("RT_REDIS_DB", "0")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.interval_sec < 15:
        args.interval_sec = 15

    r = connect_redis(args)
    r.ping()

    if args.once:
        run_once(r, verbose=args.verbose)
        return 0

    if args.verbose:
        print(f"{SOURCE} running every {args.interval_sec}s")

    while True:
        try:
            run_once(r, verbose=args.verbose)
        except Exception as e:
            print(f"ERROR: advisor cycle failed: {e}", file=sys.stderr)
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())