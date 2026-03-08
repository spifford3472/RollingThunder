#!/usr/bin/env python3
"""
RollingThunder - POTA Nearby Parks (rt-controller)

Purpose:
- Load the POTA park CSV for the United States
- Build / load a tile-based spatial index
- Query nearby parks for a GPS lat/lon efficiently
- Publish controller-owned nearby-park UI state to Redis
- Optionally emit/refresh a nearby-park alert

Design constraints:
- Read-only with respect to POTA spots schema
- Deterministic output ordering
- Controller-owned derived state only
- Practical and small; suitable for continuous operation on a Pi

Authoritative input:
- CSV file:
    /opt/rollingthunder/data/POTA/United States of America (US).csv
  or equivalent dev path

Authoritative GPS source:
- Redis HASH:
    rt:gps:pos

Controller-owned derived state:
- Redis STRING (JSON):
    rt:pota:nearby

Optional alert behavior:
- Writes/refreshes a stable alert in rt:alerts:active with 5 minute TTL

Notes:
- Only the following CSV fields are used:
    reference,name,latitude,longitude,grid,locationDesc
- The following CSV fields are ignored:
    attempts,activations,qsos,my_activations,my_hunted_qsos
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis


# -------------------- Config --------------------

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.5"))

PARKS_CSV_PATH = os.environ.get(
    "RT_POTA_PARKS_CSV",
    "/opt/rollingthunder/data/POTA/United States of America (US).csv",
)
INDEX_JSON_PATH = os.environ.get(
    "RT_POTA_PARKS_INDEX",
    "/opt/rollingthunder/data/POTA/us_parks.tileindex.v1.json",
)

GPS_POS_KEY = os.environ.get("RT_KEY_GPS_POS", "rt:gps:pos")
POTA_NEARBY_KEY = os.environ.get("RT_KEY_POTA_NEARBY", "rt:pota:nearby")
ALERTS_ACTIVE_KEY = os.environ.get("RT_KEY_ALERTS_ACTIVE", "rt:alerts:active")

POLL_MS = max(250, int(os.environ.get("RT_POTA_NEARBY_POLL_MS", "1000")))
THRESHOLD_MILES = max(0.1, float(os.environ.get("RT_POTA_NEARBY_RADIUS_MILES", "5.0")))
TILE_SCALE = max(1, int(os.environ.get("RT_POTA_TILE_SCALE", "10")))

ALERT_ENABLED = os.environ.get("RT_POTA_NEARBY_ALERT_ENABLED", "true").strip().lower() == "true"
ALERT_TTL_SEC = max(60, int(os.environ.get("RT_POTA_NEARBY_ALERT_TTL_SEC", "300")))
ALERT_ID = os.environ.get("RT_POTA_NEARBY_ALERT_ID", "rt:pota:nearby")
ALERT_SOURCE = os.environ.get("RT_POTA_NEARBY_ALERT_SOURCE", "rt-controller")
ALERT_SERVICE = os.environ.get("RT_POTA_NEARBY_ALERT_SERVICE", "pota_nearby_parks")

MAX_ALERT_ITEMS = max(1, int(os.environ.get("RT_ALERTS_MAX_ITEMS", "20")))


# -------------------- Helpers --------------------

def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except Exception:
        return None


def safe_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def floor_tile(lat: float, lon: float, tile_scale: int) -> Tuple[int, int]:
    return (math.floor(lat * tile_scale), math.floor(lon * tile_scale))


def tile_key(tile: Tuple[int, int]) -> str:
    return f"{tile[0]},{tile[1]}"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in statute miles.
    """
    r_miles = 3958.7613

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r_miles * c


def miles_per_degree_lon(lat: float) -> float:
    """
    Approximate miles per degree longitude at latitude.
    Guard against collapse near poles.
    """
    v = 69.172 * math.cos(math.radians(lat))
    return max(1.0, abs(v))


def lat_tile_radius(radius_miles: float, tile_scale: int) -> int:
    tile_height_miles = 69.0 / float(tile_scale)
    return max(1, int(math.ceil(radius_miles / tile_height_miles)))


def lon_tile_radius(radius_miles: float, lat: float, tile_scale: int) -> int:
    tile_width_miles = miles_per_degree_lon(lat) / float(tile_scale)
    return max(1, int(math.ceil(radius_miles / tile_width_miles)))


def json_dumps_compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=False)


# -------------------- Alert helpers --------------------

def remove_alert_by_id(r: redis.Redis, alert_id: str) -> None:
    existing_raw = r.get(ALERTS_ACTIVE_KEY)
    existing_obj = _safe_json_load(existing_raw)
    items = _normalize_alert_items(existing_obj)

    new_items = [
        it for it in items
        if str(it.get("id", "")).strip() != str(alert_id).strip()
    ]

    if len(new_items) == len(items):
        return

    payload = {
        "items": new_items[:MAX_ALERT_ITEMS],
        "last_update_ms": now_ms(),
    }
    r.set(ALERTS_ACTIVE_KEY, json_dumps_compact(payload))

def _safe_json_load(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _normalize_alert_items(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        if isinstance(obj.get("items"), list):
            return [x for x in obj["items"] if isinstance(x, dict)]
        if isinstance(obj.get("alerts"), list):
            return [x for x in obj["alerts"] if isinstance(x, dict)]
    return []


def upsert_alert(
    r: redis.Redis,
    *,
    alert_id: str,
    title: str,
    message: str,
    severity: str = "warn",
    kind: str = "alert",
    source: str = ALERT_SOURCE,
    service: Optional[str] = ALERT_SERVICE,
    ttl_sec: Optional[int] = ALERT_TTL_SEC,
) -> None:
    """
    Small in-process equivalent of rt-emit_alert.py refresh-existing behavior.
    Keeps one stable alert refreshed instead of creating duplicates.
    """
    created = now_ms()
    item: Dict[str, Any] = {
        "id": alert_id,
        "title": title,
        "message": message,
        "severity": severity,
        "kind": kind,
        "when": now_iso_utc(),
        "source": source,
        "created_ms": created,
    }
    if service:
        item["service"] = service
    if ttl_sec is not None and ttl_sec > 0:
        item["ttl_sec"] = int(ttl_sec)
        item["expires_ms"] = int(created + (ttl_sec * 1000))

    existing_raw = r.get(ALERTS_ACTIVE_KEY)
    existing_obj = _safe_json_load(existing_raw)
    items = _normalize_alert_items(existing_obj)

    out: List[Dict[str, Any]] = []
    replaced = False
    for it in items:
        if str(it.get("id", "")).strip() == alert_id:
            out.append(item)
            replaced = True
        else:
            out.append(it)

    if not replaced:
        out = [item] + out

    payload = {
        "items": out[:MAX_ALERT_ITEMS],
        "last_update_ms": now_ms(),
    }
    r.set(ALERTS_ACTIVE_KEY, json_dumps_compact(payload))


# -------------------- Data model --------------------

@dataclass(frozen=True, slots=True)
class ParkRecord:
    reference: str
    name: str
    latitude: float
    longitude: float
    grid: str
    locationDesc: str

    def to_wire(self) -> Dict[str, Any]:
        return {
            "reference": self.reference,
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "grid": self.grid,
            "locationDesc": self.locationDesc,
        }


@dataclass(frozen=True, slots=True)
class NearbyPark:
    reference: str
    name: str
    grid: str
    locationDesc: str
    latitude: float
    longitude: float
    distance_miles: float

    def to_wire(self) -> Dict[str, Any]:
        return {
            "reference": self.reference,
            "name": self.name,
            "grid": self.grid,
            "locationDesc": self.locationDesc,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_miles": round(self.distance_miles, 3),
        }


# -------------------- CSV + tile index --------------------

class PotaParkIndex:
    def __init__(self, csv_path: str, index_path: str, tile_scale: int = TILE_SCALE) -> None:
        self.csv_path = Path(csv_path)
        self.index_path = Path(index_path)
        self.tile_scale = int(tile_scale)

        self.parks: List[ParkRecord] = []
        self.tiles: Dict[str, List[int]] = {}
        self.csv_size: int = 0
        self.csv_mtime_ns: int = 0
        self.loaded_at_ms: int = 0

    def _csv_signature(self) -> Tuple[int, int]:
        st = self.csv_path.stat()
        return (int(st.st_size), int(st.st_mtime_ns))

    def _load_csv_parks(self) -> List[ParkRecord]:
        parks: List[ParkRecord] = []

        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ref = str(row.get("reference", "")).strip()
                name = str(row.get("name", "")).strip()
                lat = safe_float(row.get("latitude"))
                lon = safe_float(row.get("longitude"))
                grid = str(row.get("grid", "")).strip()
                location_desc = str(row.get("locationDesc", "")).strip()

                if not ref or not name or lat is None or lon is None:
                    continue

                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    continue

                parks.append(
                    ParkRecord(
                        reference=ref,
                        name=name,
                        latitude=lat,
                        longitude=lon,
                        grid=grid,
                        locationDesc=location_desc,
                    )
                )

        parks.sort(key=lambda p: (p.reference, p.name, p.latitude, p.longitude))
        return parks

    def _build_tiles(self, parks: List[ParkRecord]) -> Dict[str, List[int]]:
        tiles: Dict[str, List[int]] = {}

        for idx, park in enumerate(parks):
            tk = tile_key(floor_tile(park.latitude, park.longitude, self.tile_scale))
            tiles.setdefault(tk, []).append(idx)

        for tk in tiles:
            tiles[tk].sort()
        return tiles

    def _write_index_file(self, parks: List[ParkRecord], tiles: Dict[str, List[int]]) -> None:
        payload = {
            "version": 1,
            "tile_scale": self.tile_scale,
            "csv_path": str(self.csv_path),
            "csv_size": self.csv_size,
            "csv_mtime_ns": self.csv_mtime_ns,
            "generated_ms": now_ms(),
            "parks": [p.to_wire() for p in parks],
            "tiles": tiles,
        }

        tmp = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json_dumps_compact(payload), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def _load_index_file(self) -> bool:
        if not self.index_path.exists():
            return False

        try:
            obj = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        if not isinstance(obj, dict):
            return False
        if int(obj.get("version", 0)) != 1:
            return False
        if int(obj.get("tile_scale", -1)) != self.tile_scale:
            return False
        if int(obj.get("csv_size", -1)) != self.csv_size:
            return False
        if int(obj.get("csv_mtime_ns", -1)) != self.csv_mtime_ns:
            return False

        parks_raw = obj.get("parks")
        tiles_raw = obj.get("tiles")
        if not isinstance(parks_raw, list) or not isinstance(tiles_raw, dict):
            return False

        parks: List[ParkRecord] = []
        for row in parks_raw:
            if not isinstance(row, dict):
                return False
            ref = str(row.get("reference", "")).strip()
            name = str(row.get("name", "")).strip()
            lat = safe_float(row.get("latitude"))
            lon = safe_float(row.get("longitude"))
            grid = str(row.get("grid", "")).strip()
            location_desc = str(row.get("locationDesc", "")).strip()
            if not ref or not name or lat is None or lon is None:
                return False
            parks.append(
                ParkRecord(
                    reference=ref,
                    name=name,
                    latitude=lat,
                    longitude=lon,
                    grid=grid,
                    locationDesc=location_desc,
                )
            )

        tiles: Dict[str, List[int]] = {}
        for k, v in tiles_raw.items():
            if not isinstance(k, str) or not isinstance(v, list):
                return False
            ints: List[int] = []
            for item in v:
                try:
                    ints.append(int(item))
                except Exception:
                    return False
            ints.sort()
            tiles[k] = ints

        self.parks = parks
        self.tiles = tiles
        self.loaded_at_ms = now_ms()
        return True

    def reload_if_needed(self, force: bool = False) -> bool:
        """
        Returns True if a rebuild/reload occurred.
        """
        size, mtime_ns = self._csv_signature()

        unchanged = (
            not force
            and self.parks
            and self.csv_size == size
            and self.csv_mtime_ns == mtime_ns
        )
        if unchanged:
            return False

        self.csv_size = size
        self.csv_mtime_ns = mtime_ns

        if self._load_index_file():
            return True

        parks = self._load_csv_parks()
        tiles = self._build_tiles(parks)

        self.parks = parks
        self.tiles = tiles
        self.loaded_at_ms = now_ms()

        self._write_index_file(parks, tiles)
        return True

    def nearby(self, lat: float, lon: float, radius_miles: float) -> List[NearbyPark]:
        if not self.parks:
            return []

        center = floor_tile(lat, lon, self.tile_scale)
        lat_ring = lat_tile_radius(radius_miles, self.tile_scale)
        lon_ring = lon_tile_radius(radius_miles, lat, self.tile_scale)

        candidate_ids: List[int] = []
        seen: set[int] = set()

        for dy in range(-lat_ring, lat_ring + 1):
            for dx in range(-lon_ring, lon_ring + 1):
                tk = tile_key((center[0] + dy, center[1] + dx))
                for idx in self.tiles.get(tk, []):
                    if idx not in seen:
                        seen.add(idx)
                        candidate_ids.append(idx)

        out: List[NearbyPark] = []
        for idx in candidate_ids:
            park = self.parks[idx]
            dist = haversine_miles(lat, lon, park.latitude, park.longitude)
            if dist <= radius_miles:
                out.append(
                    NearbyPark(
                        reference=park.reference,
                        name=park.name,
                        grid=park.grid,
                        locationDesc=park.locationDesc,
                        latitude=park.latitude,
                        longitude=park.longitude,
                        distance_miles=dist,
                    )
                )

        out.sort(key=lambda p: (round(p.distance_miles, 6), p.reference, p.name))
        return out


# -------------------- Redis state publishing --------------------

def write_nearby_state(
    r: redis.Redis,
    *,
    gps_valid: bool,
    threshold_miles: float,
    current_lat: Optional[float],
    current_lon: Optional[float],
    nearby: List[NearbyPark],
    source_csv: str,
    csv_mtime_ns: int,
    tile_scale: int,
) -> None:
    choices: List[Dict[str, Any]] = [
        {
            "reference": "",
            "name": "Not in a park",
            "synthetic": True,
            "distance_miles": None,
        }
    ]
    choices.extend([p.to_wire() for p in nearby])

    payload = {
        "gps_valid": bool(gps_valid),
        "last_update_ms": now_ms(),
        "threshold_miles": float(threshold_miles),
        "within_threshold": bool(gps_valid and len(nearby) > 0),
        "park_count": int(len(nearby)),
        "current": {
            "lat": current_lat,
            "lon": current_lon,
        },
        "source": {
            "csv_path": source_csv,
            "csv_mtime_ns": int(csv_mtime_ns),
            "tile_scale": int(tile_scale),
        },
        "choices": choices,
    }
    r.set(POTA_NEARBY_KEY, json_dumps_compact(payload))


def read_current_gps_pos(r: redis.Redis) -> Tuple[bool, Optional[float], Optional[float]]:
    obj = r.hgetall(GPS_POS_KEY)
    if not obj:
        return (False, None, None)

    valid = safe_bool(obj.get("valid"))
    lat = safe_float(obj.get("lat"))
    lon = safe_float(obj.get("lon"))

    if not valid or lat is None or lon is None:
        return (False, None, None)

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return (False, None, None)

    return (True, lat, lon)


# -------------------- Main loop --------------------

def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
        retry_on_timeout=True,
    )
    r.ping()

    index = PotaParkIndex(
        csv_path=PARKS_CSV_PATH,
        index_path=INDEX_JSON_PATH,
        tile_scale=TILE_SCALE,
    )
    index.reload_if_needed(force=True)

    last_alert_message = ""

    while True:
        loop_start = time.time()
        try:
            index.reload_if_needed(force=False)

            gps_valid, lat, lon = read_current_gps_pos(r)

            nearby: List[NearbyPark] = []
            if gps_valid and lat is not None and lon is not None:
                nearby = index.nearby(lat, lon, THRESHOLD_MILES)

            write_nearby_state(
                r,
                gps_valid=gps_valid,
                threshold_miles=THRESHOLD_MILES,
                current_lat=lat,
                current_lon=lon,
                nearby=nearby,
                source_csv=str(index.csv_path),
                csv_mtime_ns=index.csv_mtime_ns,
                tile_scale=index.tile_scale,
            )

            if ALERT_ENABLED:
                if gps_valid and nearby:
                    top = nearby[0]
                    if len(nearby) == 1:
                        msg = f"Within {THRESHOLD_MILES:g} miles of {top.reference} {top.name} ({top.distance_miles:.1f} mi)."
                    else:
                        msg = (
                            f"{len(nearby)} parks within {THRESHOLD_MILES:g} miles. "
                            f"Nearest: {top.reference} {top.name} ({top.distance_miles:.1f} mi)."
                        )

                    if msg != last_alert_message:
                        last_alert_message = msg

                    upsert_alert(
                        r,
                        alert_id=ALERT_ID,
                        title="Nearby POTA park",
                        message=msg,
                        severity="info",
                        kind="pota_nearby",
                        source=ALERT_SOURCE,
                        service=ALERT_SERVICE,
                        ttl_sec=ALERT_TTL_SEC,
                    )
                else:
                    last_alert_message = ""
                    remove_alert_by_id(r, ALERT_ID)

        except Exception as e:
            print(f"[pota_nearby_parks] ERROR: {type(e).__name__}: {e}", flush=True)

        elapsed = time.time() - loop_start
        sleep_s = (POLL_MS / 1000.0) - elapsed
        if sleep_s > 0:
            time.sleep(sleep_s)


if __name__ == "__main__":
    main()
