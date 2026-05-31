#!/usr/bin/env python3
"""
RollingThunder VHF page/map view model service.

Phase 8.3D scope:
- Controller-owned display-ready VHF page model.
- Controller-owned display-ready VHF map model.
- Reads only Redis state.
- Writes:
    rt:vhf:page
    rt:vhf:map
- Publishes state.changed to rt:system:bus when either model changes.

Safety:
- Does not write rt:ui:bus.
- Does not issue adapter requests.
- Does not open serial ports.
- Does not command radios.
- Does not use rigctl.
- Does not write/clear/load memories.
- Does not start IC-2730A built-in scan.
- Does not program Side B.
- Does not add PTT/transmit controls.
- Does not read SQLite directly.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SOURCE = "vhf_page_model"

APP_CONFIG_PATH = Path("/opt/rollingthunder/config/app.json")

KEY_VHF_RADIO = "rt:vhf:radio"
KEY_VHF_SCAN = "rt:vhf:scan"
KEY_NEARBY = "rt:vhf:repeaters:nearby"
KEY_GPS_POS = "rt:gps:pos"

KEY_VHF_PAGE = "rt:vhf:page"
KEY_VHF_MAP = "rt:vhf:map"

SYSTEM_BUS = "rt:system:bus"

DEFAULT_MODE = "repeaters"
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_MAP_RADIUS_MILES = 30.0
DEFAULT_REPEATER_RADIUS_MILES = 25.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"{utc_now()} {SOURCE}: {message}", flush=True)


def warn(message: str) -> None:
    print(f"{utc_now()} {SOURCE}: WARN: {message}", file=sys.stderr, flush=True)


def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        warn(f"could not read config {path}: {exc}")
        return {}


def deep_get(config: Dict[str, Any], dotted: str, default: Any) -> Any:
    if dotted in config:
        return config.get(dotted, default)

    cur: Any = config
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def cfg_float(
    config: Dict[str, Any],
    dotted: str,
    env_name: str,
    default: float,
    minimum: Optional[float] = None,
) -> float:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = deep_get(config, dotted, default)

    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("not finite")
        if minimum is not None and value < minimum:
            raise ValueError(f"value below minimum {minimum}")
        return value
    except Exception:
        warn(f"invalid numeric config {dotted}={raw!r}; using {default!r}")
        return float(default)


def boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "ok", "available", "ready"}:
            return True
        if text in {"0", "false", "no", "n", "off", "bad", "unavailable", "disabled"}:
            return False
    return default


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


def first_present_float(data: Dict[str, Any], names: Iterable[str]) -> Optional[float]:
    lower = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            val = parse_float(data.get(name))
            if val is not None:
                return val
        lname = name.lower()
        if lname in lower:
            val = parse_float(lower.get(lname))
            if val is not None:
                return val
    return None


def cardinal_from_degrees(value: Any) -> str:
    deg = parse_float(value)
    if deg is None:
        return ""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((deg % 360.0) + 22.5) / 45.0) % 8
    return dirs[idx]


def normalize_frequency(value: Any) -> Optional[float]:
    freq = parse_float(value)
    if freq is None:
        return None
    if 100.0 <= freq <= 999.999999:
        return round(freq, 6)
    return None


def frequency_label(freq: Optional[float]) -> str:
    if freq is None:
        return ""
    return f"{freq:.3f}".rstrip("0").rstrip(".")


def tone_label(item: Dict[str, Any]) -> str:
    tone = parse_float(
        item.get("tone_hz")
        or item.get("tx_tone")
        or item.get("rx_tone")
        or item.get("repeater_tone_hz")
    )
    if tone is None or tone <= 0:
        return "No tone"
    return f"Tone {tone:.1f}"


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


class RedisCli:
    def __init__(self) -> None:
        self.redis_cli = os.environ.get("REDIS_CLI", "redis-cli")
        self.base_args: List[str] = [self.redis_cli]

        host = os.environ.get("RT_REDIS_HOST") or os.environ.get("REDIS_HOST")
        port = os.environ.get("RT_REDIS_PORT") or os.environ.get("REDIS_PORT")
        db = os.environ.get("RT_REDIS_DB") or os.environ.get("REDIS_DB")

        if host:
            self.base_args += ["-h", str(host)]
        if port:
            self.base_args += ["-p", str(port)]
        if db:
            self.base_args += ["-n", str(db)]

        auth_args = os.environ.get("REDIS_AUTH_ARGS", "").strip()
        if auth_args:
            self.base_args += shlex.split(auth_args)

    def _run(self, args: Sequence[str], input_text: Optional[str] = None) -> str:
        proc = subprocess.run(
            self.base_args + list(args),
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"redis-cli failed rc={proc.returncode}: {proc.stderr.strip()}")
        return proc.stdout

    def get(self, key: str) -> Optional[str]:
        out = self._run(["--raw", "GET", key])
        if out == "":
            return None
        return out.rstrip("\n")

    def hgetall(self, key: str) -> Dict[str, str]:
        out = self._run(["--raw", "HGETALL", key])
        lines = out.splitlines()
        result: Dict[str, str] = {}
        for i in range(0, len(lines) - 1, 2):
            result[lines[i]] = lines[i + 1]
        return result

    def key_type(self, key: str) -> str:
        out = self._run(["--raw", "TYPE", key])
        return out.strip().lower()

    def read_any_model(self, key: str) -> Dict[str, Any]:
        try:
            key_type = self.key_type(key)
            if key_type == "hash":
                return self.hgetall(key)
            if key_type == "string":
                raw = self.get(key)
                if not raw:
                    return {}
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            warn(f"could not read {key}: {exc}")
        return {}

    def set(self, key: str, value: str) -> None:
        self._run(["SET", key, value])

    def publish_json(self, channel: str, payload: Dict[str, Any]) -> None:
        self._run(["PUBLISH", channel, compact_json(payload)])


def stable_model(model: Dict[str, Any]) -> Dict[str, Any]:
    clone = dict(model)
    clone.pop("updated_utc", None)
    return clone


def load_json_model(redis: RedisCli, key: str) -> Dict[str, Any]:
    try:
        raw = redis.get(key)
        if not raw:
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def publish_state_changed(redis: RedisCli, keys: List[str], reason: str) -> None:
    event = {
        "topic": "state.changed",
        "type": "state.changed",
        "source": SOURCE,
        "keys": keys,
        "changed_keys": keys,
        "deleted_keys": [],
        "reason": reason,
        "timestamp_utc": utc_now(),
        "host": socket.gethostname(),
        # Include payload for the current projector event parser.
        "payload": {
            "keys": keys,
            "changed_keys": keys,
            "deleted_keys": [],
            "reason": reason,
        },
    }
    redis.publish_json(SYSTEM_BUS, event)


def publish_model_if_changed(
    redis: RedisCli,
    key: str,
    model: Dict[str, Any],
    changed_keys: List[str],
) -> bool:
    previous = load_json_model(redis, key)
    if stable_model(previous) == stable_model(model):
        return False

    redis.set(key, compact_json(model))
    changed_keys.append(key)
    return True


def radio_available(radio: Dict[str, Any]) -> bool:
    status = str(radio.get("status") or "").strip().lower()
    return boolish(radio.get("available"), False) and status in {"available", "ready"}


def radio_status(radio: Dict[str, Any]) -> str:
    return str(radio.get("status") or "unknown").strip().lower() or "unknown"


def nearby_items(nearby: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("repeaters", "items", "rows", "nearby"):
        value = nearby.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def item_id(item: Dict[str, Any]) -> str:
    for key in ("id", "source_id", "repeater_id", "station_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    callsign = str(item.get("callsign") or item.get("call_sign") or "").strip()
    name = str(item.get("name") or item.get("channel_name") or "").strip()
    freq = frequency_label(normalize_frequency(item.get("frequency_mhz")))
    return ":".join(part for part in (callsign or name, freq) if part) or "repeater"


def item_name(item: Dict[str, Any]) -> str:
    for key in ("name", "channel_name", "repeater_name", "callsign", "call_sign"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:64]
    return "Repeater"


def item_callsign(item: Dict[str, Any]) -> str:
    for key in ("callsign", "call_sign", "station", "owner_call", "name"):
        value = str(item.get(key) or "").strip().upper()
        if value:
            return value[:24]
    return ""


def valid_lat_lon(lat: Any, lon: Any) -> Tuple[Optional[float], Optional[float]]:
    parsed_lat = parse_float(lat)
    parsed_lon = parse_float(lon)
    if parsed_lat is None or parsed_lon is None:
        return None, None
    if parsed_lat < -90 or parsed_lat > 90 or parsed_lon < -180 or parsed_lon > 180:
        return None, None
    return round(parsed_lat, 6), round(parsed_lon, 6)


def repeater_lat_lon(item: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    lat = item.get("latitude", item.get("lat"))
    lon = item.get("longitude", item.get("lon", item.get("lng")))
    return valid_lat_lon(lat, lon)


def normalize_repeater_item(
    item: Dict[str, Any],
    *,
    active_id: Optional[str],
    active_index: Optional[int],
    index: int,
) -> Optional[Dict[str, Any]]:
    freq = normalize_frequency(
        item.get("frequency_mhz")
        or item.get("rx_frequency_mhz")
        or item.get("output_mhz")
    )
    if freq is None:
        return None

    rid = item_id(item)
    name = item_name(item)
    callsign = item_callsign(item)

    distance = parse_float(item.get("distance_miles"))
    if distance is not None:
        distance = round(distance, 1)

    bearing = parse_float(item.get("bearing_degrees"))
    if bearing is not None:
        bearing = int(round(bearing % 360.0))

    active = False
    if active_id and rid == active_id:
        active = True
    elif active_index is not None and index == active_index:
        active = True

    label_name = callsign or name
    label = f"{label_name} {frequency_label(freq)}".strip()

    distance_text = ""
    if distance is not None:
        direction = cardinal_from_degrees(bearing)
        distance_text = f"{distance:.1f} mi {direction}".strip()

    subline_parts = [part for part in (distance_text, tone_label(item)) if part]
    subline = " ".join(subline_parts)

    normalized = {
        "id": rid,
        "label": label,
        "name": name,
        "callsign": callsign,
        "frequency_mhz": freq,
        "subline": subline,
        "distance_miles": distance,
        "bearing_degrees": bearing,
        "skywarn": boolish(item.get("skywarn"), False),
        "ares": boolish(item.get("ares"), False),
        "selected": active,
        "highlighted": active,
        "active": active,
    }

    lat, lon = repeater_lat_lon(item)
    if lat is not None and lon is not None:
        normalized["lat"] = lat
        normalized["lon"] = lon

    return normalized


def read_gps_position(redis: RedisCli) -> Tuple[Optional[float], Optional[float], str]:
    gps = redis.read_any_model(KEY_GPS_POS)
    if not gps:
        return None, None, "missing_gps"

    valid_raw = gps.get("valid")
    if valid_raw is not None and not boolish(valid_raw, False):
        return None, None, "invalid_gps"

    status = str(
        gps.get("status")
        or gps.get("gps_status")
        or gps.get("fix_status")
        or ""
    ).strip().lower()

    if status and status not in {"ok", "valid", "ready", "fix", "fixed", "3d", "2d"}:
        return None, None, "invalid_gps"

    lat = first_present_float(
        gps,
        ("lat", "latitude", "gps_lat", "gps_latitude", "fix_lat", "position_lat"),
    )
    lon = first_present_float(
        gps,
        ("lon", "lng", "longitude", "gps_lon", "gps_lng", "gps_longitude", "fix_lon", "position_lon"),
    )

    lat, lon = valid_lat_lon(lat, lon)
    if lat is None or lon is None:
        return None, None, "missing_gps"

    # Round GPS center enough to avoid rapid map churn from normal GPS jitter.
    # 4 decimal places is roughly meter-level display precision.
    return round(lat, 4), round(lon, 4), "ok"
   


def selected_scan_target(scan: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[Dict[str, Any]]]:
    active_id = scan.get("current_repeater_id")
    active_id_text = str(active_id) if active_id not in (None, "") else None

    active_index = None
    try:
        if scan.get("current_index") is not None:
            active_index = int(scan.get("current_index"))
    except Exception:
        active_index = None

    current_repeater = scan.get("current_repeater")
    if not isinstance(current_repeater, dict):
        current_repeater = None

    if active_id_text is None and current_repeater:
        active_id_text = str(
            current_repeater.get("id")
            or current_repeater.get("source_id")
            or current_repeater.get("callsign")
            or current_repeater.get("name")
            or ""
        ).strip() or None

    return active_id_text, active_index, current_repeater


def build_options() -> List[Dict[str, Any]]:
    return [
        {
            "id": "repeaters",
            "label": "REPEATERS",
            "selected": True,
            "enabled": True,
            "placeholder": False,
        },
        {
            "id": "air",
            "label": "AIR",
            "selected": False,
            "enabled": False,
            "placeholder": True,
        },
        {
            "id": "news",
            "label": "NEWS",
            "selected": False,
            "enabled": False,
            "placeholder": True,
        },
    ]


def build_models(config: Dict[str, Any], redis: RedisCli) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    radio = redis.read_any_model(KEY_VHF_RADIO)
    scan = redis.read_any_model(KEY_VHF_SCAN)
    nearby = redis.read_any_model(KEY_NEARBY)
    gps_lat, gps_lon, gps_status = read_gps_position(redis)

    available = radio_available(radio)
    r_status = radio_status(radio)

    repeaters_raw = nearby_items(nearby)
    scan_enabled = boolish(scan.get("enabled"), False)
    scan_requested = boolish(scan.get("requested"), False)
    scanning = boolish(scan.get("scanning"), False) or str(scan.get("actual_scan_state") or "").lower() == "scanning"

    manual_selected = str(scan.get("status") or "").strip().lower() in {
        "manual_select_tuning",
        "manual_selected",
        "manual_select_failed",
    }

    if scan_enabled or scan_requested or scanning or manual_selected:
        active_id, active_index, current_repeater = selected_scan_target(scan)
    else:
        active_id, active_index, current_repeater = None, None, None

    items: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(repeaters_raw):
        normalized = normalize_repeater_item(
            raw_item,
            active_id=active_id,
            active_index=active_index,
            index=index,
        )
        if normalized is not None:
            items.append(normalized)

    selected_index: Optional[int]
    active_items = [idx for idx, item in enumerate(items) if item.get("active")]
    if active_items:
        selected_index = active_items[0]
    elif items:
        selected_index = 0
        items[0]["selected"] = True
    else:
        selected_index = None

    scan_status = str(scan.get("status") or "").strip().lower()
    scanning = boolish(scan.get("scanning"), False) or str(scan.get("actual_scan_state") or "").lower() == "scanning"

    if not radio:
        page_status = "unavailable"
        headline = "VHF radio status unavailable"
        status_state = "unavailable"
        reason = "VHF radio state is not available yet."
    elif not available:
        page_status = "unavailable"
        headline = "VHF radio unavailable"
        status_state = r_status
        reason = str(radio.get("reason") or "VHF radio/control path unavailable.")
    elif scanning:
        page_status = "ok"
        headline = "Scanning..."
        status_state = "scanning"
        reason = str(scan.get("reason") or "Software repeater scan active.")
    elif boolish(scan.get("enabled"), False):
        page_status = "ok"
        headline = "Repeater scan enabled"
        status_state = scan_status or "enabled"
        reason = str(scan.get("reason") or "Software repeater scan enabled.")
    else:
        page_status = "ok"
        headline = "Repeaters"
        status_state = scan_status or "idle"
        reason = str(scan.get("reason") or "Repeater display current.")

    current_item: Optional[Dict[str, Any]] = None
    if selected_index is not None and 0 <= selected_index < len(items):
        current_item = items[selected_index]

    frequency_mhz = None
    if current_item:
        frequency_mhz = current_item.get("frequency_mhz")
    if frequency_mhz is None:
        frequency_mhz = normalize_frequency(scan.get("current_frequency_mhz"))

    radius_miles = parse_float(scan.get("map_radius_miles"))
    if radius_miles is None:
        radius_miles = parse_float(nearby.get("radius_miles"))
    if radius_miles is None:
        radius_miles = cfg_float(
            config,
            "vhf.scan.map_radius_miles",
            "RT_VHF_MAP_RADIUS_MILES",
            DEFAULT_MAP_RADIUS_MILES,
            minimum=0.0,
        )

    page_model = {
        "status": page_status,
        "mode": DEFAULT_MODE,
        "radio_available": available,
        "radio_status": r_status,
        "headline": headline,
        "left_panel": {
            "title": "Repeaters",
            "selectable": True,
            "selected_index": selected_index,
            "items": items,
        },
        "status_panel": {
            "state": status_state,
            "headline": headline,
            "frequency_mhz": frequency_mhz,
            "repeater_id": current_item.get("id") if current_item else None,
            "repeater_name": current_item.get("name") if current_item else None,
            "callsign": current_item.get("callsign") if current_item else None,
            "skywarn": bool(current_item.get("skywarn")) if current_item else False,
            "ares": bool(current_item.get("ares")) if current_item else False,
            "reason": reason,
        },
        "options": build_options(),
        "map_key": KEY_VHF_MAP,
        "source": SOURCE,
        "updated_utc": utc_now(),
    }

    markers: List[Dict[str, Any]] = []
    for item in items:
        lat = item.get("lat")
        lon = item.get("lon")
        valid_lat, valid_lon = valid_lat_lon(lat, lon)
        if valid_lat is None or valid_lon is None:
            continue
        markers.append(
            {
                "id": item.get("id"),
                "type": "repeater_tower",
                "lat": valid_lat,
                "lon": valid_lon,
                "label": item.get("callsign") or item.get("name") or item.get("label"),
                "frequency_mhz": item.get("frequency_mhz"),
                "active": bool(item.get("active")),
                "highlighted": bool(item.get("highlighted")),
                "skywarn": bool(item.get("skywarn")),
                "ares": bool(item.get("ares")),
            }
        )

    highlight = None
    if current_item and bool(current_item.get("active")):
        highlight = current_item.get("id")

    map_status = "ok" if gps_status == "ok" else "unavailable"
    stale = gps_status != "ok"

    map_model = {
        "status": map_status,
        "center_lat": gps_lat,
        "center_lon": gps_lon,
        "radius_miles": int(radius_miles) if float(radius_miles).is_integer() else radius_miles,
        "vehicle_marker": {
            "id": "vehicle",
            "type": "car",
            "lat": gps_lat,
            "lon": gps_lon,
            "label": "RollingThunder",
        } if gps_lat is not None and gps_lon is not None else None,
        "markers": markers,
        "highlight": highlight,
        "refreshing": False,
        "stale": stale,
        "reason": "Map state current." if gps_status == "ok" else gps_status,
        "source": SOURCE,
        "updated_utc": utc_now(),
    }

    return page_model, map_model


def main() -> int:
    config = load_json_file(APP_CONFIG_PATH)

    interval_seconds = cfg_float(
        config,
        "vhf.page_model_interval_seconds",
        "RT_VHF_PAGE_MODEL_INTERVAL_SECONDS",
        DEFAULT_INTERVAL_SECONDS,
        minimum=0.25,
    )

    redis = RedisCli()

    log(
        "starting "
        f"page_key={KEY_VHF_PAGE} map_key={KEY_VHF_MAP} "
        f"interval_seconds={interval_seconds}"
    )

    while True:
        try:
            page_model, map_model = build_models(config, redis)

            changed_keys: List[str] = []
            publish_model_if_changed(redis, KEY_VHF_PAGE, page_model, changed_keys)
            publish_model_if_changed(redis, KEY_VHF_MAP, map_model, changed_keys)

            if changed_keys:
                publish_state_changed(redis, changed_keys, "vhf_page_model_changed")
                log(f"published changed_keys={','.join(changed_keys)}")
        except Exception as exc:
            warn(f"cycle failed: {exc}")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())