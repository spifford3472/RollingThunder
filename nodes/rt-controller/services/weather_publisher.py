#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

KEY_GPS_POS = os.environ.get("RT_KEY_GPS_POS", "rt:gps:pos")
KEY_WEATHER = os.environ.get("RT_KEY_WEATHER_CURRENT", "rt:weather:current")
SYSTEM_BUS = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")

INTERVAL_SEC = int(os.environ.get("RT_WEATHER_INTERVAL_SEC", "300"))
HTTP_TIMEOUT_SEC = float(os.environ.get("RT_WEATHER_HTTP_TIMEOUT_SEC", "8"))

def load_app_version() -> str:
    path = os.environ.get("RT_CONFIG_PATH", "/opt/rollingthunder/config/app.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return str(cfg.get("schema", {}).get("version", "unknown"))
    except Exception:
        return "unknown"

APP_VERSION = load_app_version()

USER_AGENT = os.environ.get(
    "RT_WEATHER_USER_AGENT",
    f"RollingThunder/{APP_VERSION} (KI5VNB local dashboard)",
)
    
def now_ms() -> int:
    return int(time.time() * 1000)

def c_from_f(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0

def publish_state_changed(r: redis.Redis, keys: list[str]) -> None:
    ts = now_ms()
    event = {
        "topic": "state.changed",
        "payload": {"keys": keys, "changed_keys": keys, "ts_ms": ts},
        "ts_ms": ts,
        "source": "weather_publisher",
    }
    r.publish(SYSTEM_BUS, json.dumps(event, sort_keys=True, separators=(",", ":")))

def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/geo+json, application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None

def gps_valid(gps: dict[str, str]) -> bool:
    return str(gps.get("valid", "")).lower() in {"1", "true", "yes", "y"}

def mark_stale(r: redis.Redis, reason: str) -> None:
    r.hset(KEY_WEATHER, mapping={
        "stale": "1",
        "reason": reason,
        "source": "weather.gov",
        "last_update_ms": str(now_ms()),
    })
    publish_state_changed(r, [KEY_WEATHER])

def fetch_weather_for(lat: float, lon: float) -> dict[str, str]:
    lat4 = round(lat, 4)
    lon4 = round(lon, 4)

    points = http_json(f"https://api.weather.gov/points/{lat4},{lon4}")
    props = points.get("properties") or {}
    hourly_url = props.get("forecastHourly")
    if not hourly_url:
        raise RuntimeError("missing forecastHourly URL from weather.gov points response")

    hourly = http_json(hourly_url)
    periods = ((hourly.get("properties") or {}).get("periods")) or []
    if not periods:
        raise RuntimeError("missing hourly forecast periods")

    p0 = periods[0]
    temp_f = get_float(p0.get("temperature"))
    if temp_f is None:
        raise RuntimeError("missing hourly temperature")

    temp_c = c_from_f(temp_f)

    return {
        "f": f"{temp_f:.0f}",
        "c": f"{temp_c:.1f}",
        "short_forecast": str(p0.get("shortForecast") or ""),
        "wind_speed": str(p0.get("windSpeed") or ""),
        "wind_direction": str(p0.get("windDirection") or ""),
        "source": "weather.gov",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "grid_id": str(props.get("gridId") or ""),
        "grid_x": str(props.get("gridX") or ""),
        "grid_y": str(props.get("gridY") or ""),
        "stale": "0",
        "reason": "",
        "last_update_ms": str(now_ms()),
    }

def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )
    r.ping()

    while True:
        try:
            gps = r.hgetall(KEY_GPS_POS)
            if not gps_valid(gps):
                mark_stale(r, "gps_invalid")
                time.sleep(INTERVAL_SEC)
                continue

            lat = get_float(gps.get("lat"))
            lon = get_float(gps.get("lon"))
            if lat is None or lon is None:
                mark_stale(r, "gps_missing_lat_lon")
                time.sleep(INTERVAL_SEC)
                continue

            payload = fetch_weather_for(lat, lon)
            r.hset(KEY_WEATHER, mapping=payload)
            publish_state_changed(r, [KEY_WEATHER])

        except Exception as e:
            mark_stale(r, f"error:{type(e).__name__}")

        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    main()