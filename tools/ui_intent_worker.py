#!/usr/bin/env python3
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timezone

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
SYSTEM_BUS_CH = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")
LAST_RESULT_KEY = os.environ.get("RT_UI_LAST_RESULT_KEY", "rt:controller:ui:last_result")

CONFIG_PATH = Path(os.environ.get("RT_CONFIG_PATH", "/opt/rollingthunder/config/app.json"))
NODE_ID = os.environ.get("RT_NODE_ID", "unknown-node")

# Reboot behavior:
# - "reboot" (default) -> systemctl reboot
# - "poweroff"         -> systemctl poweroff
REBOOT_MODE = os.environ.get("RT_NODE_REBOOT_MODE", "reboot").strip().lower()
SYSTEMCTL_TIMEOUT_SEC = float(os.environ.get("RT_SYSTEMCTL_TIMEOUT_SEC", "8.0"))

# Safety: default off unless explicitly enabled on that node.
ALLOW_NODE_REBOOT = (
    os.environ.get("RT_ALLOW_REBOOT", "0").strip() == "1"
    or os.environ.get("RT_ALLOW_NODE_REBOOT", "0").strip() == "1"
)

# HF UI context keys in Redis, used by both the context manager and the UI intent worker.
HHF_CONTEXT_KEY = os.environ.get("RT_HF_CONTEXT_KEY", "rt:hf:context")
HF_SPOTS_SELECTED_KEY = os.environ.get("RT_HF_SPOTS_SELECTED_KEY", "rt:hf:spots:selected")
HF_SELECTED_DETAIL_KEY = os.environ.get("RT_HF_SELECTED_DETAIL_KEY", "rt:hf:spots:selected_detail")
HF_QSO_HISTORY_SELECTED_KEY = os.environ.get(
    "RT_HF_QSO_HISTORY_SELECTED_KEY",
    "rt:hf:qso_history:selected",
)

HF_QRZ_SELECTED_KEY = os.environ.get(
    "RT_HF_QRZ_SELECTED_KEY",
    "rt:hf:qrz:selected",
)
HF_MAP_SELECTED_KEY = os.environ.get(
    "RT_HF_MAP_SELECTED_KEY",
    "rt:hf:map:selected",
)
HF_QRZ_API_ENABLED = os.environ.get("RT_QRZ_API_ENABLED", "0").strip() == "1"
HF_BANDS_KEY = os.environ.get("RT_HF_BANDS_KEY", "rt:hf:bands")

# POTA UI context key in Redis, used by both the context manager and the UI intent worker.
POTA_CONTEXT_KEY = os.environ.get("RT_POTA_CONTEXT_KEY", "rt:pota:context")


POTA_BAND_ORDER = {
    "160m", "80m", "60m", "40m", "30m",
    "20m", "17m", "15m", "12m", "10m",
    "6m",
}

RT_RADIO_SERVICES_DIR = Path("/opt/rollingthunder/nodes/rt-radio/services")
RT_CONTROLLER_SERVICES_DIR = Path("/opt/rollingthunder/services")

_qso_history_module = None
_qrz_lookup_module = None
_qrz_client_module = None
_hf_country_codes_cache: dict[str, Any] | None = None
_qrz_threads: dict[str, float] = {}
_qrz_threads_lock = threading.Lock()

def _load_qso_history_module():
    """
    Lazy-load controller-only SQLite QSO history support.

    This keeps rt-radio from failing import at startup. Only rt-controller
    should call this from HF selection handlers.
    """
    global _qso_history_module

    if _qso_history_module is not None:
        return _qso_history_module

    if str(RT_CONTROLLER_SERVICES_DIR) not in sys.path:
        sys.path.insert(0, str(RT_CONTROLLER_SERVICES_DIR))

    import qso_history

    _qso_history_module = qso_history
    return _qso_history_module

_radio_runtime: Dict[str, Any] | None = None
_radio_runtime_error: str | None = None

def _load_qrz_lookup_module():
    """
    Lazy-load controller-side QRZ cache support.

    This keeps shared workers on non-controller nodes from depending on QRZ.
    """
    global _qrz_lookup_module

    if _qrz_lookup_module is not None:
        return _qrz_lookup_module

    if str(RT_CONTROLLER_SERVICES_DIR) not in sys.path:
        sys.path.insert(0, str(RT_CONTROLLER_SERVICES_DIR))

    import qrz_lookup

    _qrz_lookup_module = qrz_lookup
    return _qrz_lookup_module


def _load_qrz_client_module():
    """
    Lazy-load controller-side QRZ XML client.
    """
    global _qrz_client_module

    if _qrz_client_module is not None:
        return _qrz_client_module

    if str(RT_CONTROLLER_SERVICES_DIR) not in sys.path:
        sys.path.insert(0, str(RT_CONTROLLER_SERVICES_DIR))

    import qrz_client

    _qrz_client_module = qrz_client
    return _qrz_client_module

def _load_radio_runtime() -> Dict[str, Any]:
    """
    Lazy-load the rt-radio local radio package only when needed.
    This worker is shared across nodes, so we do not want import-time
    failures on nodes that never execute radio control.
    """
    global _radio_runtime, _radio_runtime_error

    if _radio_runtime is not None:
        return _radio_runtime

    if _radio_runtime_error is not None:
        raise RuntimeError(_radio_runtime_error)

    try:
        if str(RT_RADIO_SERVICES_DIR) not in sys.path:
            sys.path.insert(0, str(RT_RADIO_SERVICES_DIR))

        from radio import RadioService, load_radio_config
        from radio.hamlib_client import (
            HamlibError,
            RigctldCommandError,
            RigctldProtocolError,
            RigctldUnreachable,
        )
        from radio.radios.ft891 import RadioValidationError

        service = RadioService(load_radio_config())

        _radio_runtime = {
            "service": service,
            "HamlibError": HamlibError,
            "RigctldCommandError": RigctldCommandError,
            "RigctldProtocolError": RigctldProtocolError,
            "RigctldUnreachable": RigctldUnreachable,
            "RadioValidationError": RadioValidationError,
        }
        return _radio_runtime

    except Exception as e:
        _radio_runtime_error = f"{type(e).__name__}:{e}"
        raise


#=================================================================================
#   HF HELPERS                                                      
#=================================================================================                                                 
def _json_get(r, key, default=None):
    try:
        raw = r.get(key)
        if not raw:
            return default
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        return default


def _json_set(r, key, value):
    r.set(key, json.dumps(value, separators=(",", ":")))


def _hf_selected_spot(spots_model, selected_id=None):
    items = (spots_model or {}).get("items") or []
    if not items:
        return None

    if selected_id:
        for item in items:
            if item.get("id") == selected_id:
                return item

    return items[0]


def _hf_mode_for_freq(freq_hz, band=None):
    try:
        freq_hz = int(freq_hz)
    except Exception:
        return "USB"

    if band in {"160m", "80m", "60m", "40m"}:
        return "LSB"

    if freq_hz < 10000000:
        return "LSB"

    return "USB"


def _hf_publish_state_changed(r: redis.Redis, keys: list[str]) -> None:
    clean_keys = [str(k).strip() for k in keys if str(k).strip()]
    if not clean_keys:
        return

    ts = now_ms()

    r.publish(
        "rt:system:bus",
        json.dumps({
            "topic": "state.changed",
            "payload": {
                "keys": clean_keys,
            },
            "source": "ui_intent_worker:hf",
            "ts_ms": ts,
        })
    )

def _hf_update_bands_selected_id(r: redis.Redis, band: str) -> None:
    band = str(band or "").strip()
    if not band:
        return

    model = _json_get(r, HF_BANDS_KEY, {}) or {}
    if not isinstance(model, dict):
        return

    model["selected_id"] = band
    model["selected_band"] = band
    model["updated_at_ms"] = now_ms()

    items = model.get("items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(
                item.get("id")
                or item.get("band")
                or item.get("label")
                or item.get("name")
                or ""
            ).strip()

            item["selected"] = item_id == band

    _json_set(r, HF_BANDS_KEY, model)

def _hf_update_qso_history_for_callsign(r: redis.Redis, callsign: str) -> None:
    """
    Controller-side SQLite lookup for selected HF callsign.

    Must never run on rt-radio. Must never prevent tuning.
    Must always update rt:hf:qso_history:selected for panel 4, even on failure.
    """
    if NODE_ID != "rt-controller":
        return

    call = str(callsign or "").strip().upper()

    fallback_payload = {
        "source": "sqlite",
        "callsign": call,
        "items": [],
        "limit": 5,
        "updated_at_ms": now_ms(),
    }

    try:
        qso_history = _load_qso_history_module()

        payload = qso_history.history_payload_for_callsign(
            None,
            call,
            limit=5,
            updated_at_ms=now_ms(),
        )

        if not isinstance(payload, dict):
            payload = fallback_payload

        payload["callsign"] = call

        _json_set(r, HF_QSO_HISTORY_SELECTED_KEY, payload)
        _hf_publish_state_changed(r, [HF_QSO_HISTORY_SELECTED_KEY])

    except Exception as exc:
        fallback_payload["status"] = "unavailable"
        fallback_payload["message"] = "QSO HISTORY NOT CURRENTLY AVAILABLE"

        try:
            _json_set(r, HF_QSO_HISTORY_SELECTED_KEY, fallback_payload)
            _hf_publish_state_changed(r, [HF_QSO_HISTORY_SELECTED_KEY])
        except Exception:
            pass

        try:
            publish_last_result(r, {
                "ok": False,
                "intent": "hf.qso_history.lookup",
                "callsign": call,
                "reason": "qso_history_lookup_failed",
                "error": str(exc),
            })
        except Exception:
            pass

def _hf_qrz_status_payload(callsign: str, status: str, message: str = "") -> Dict[str, Any]:
    return {
        "source": "qrz",
        "callsign": str(callsign or "").strip().upper(),
        "status": status,
        "message": message,
        "updated_at_ms": now_ms(),
    }

def _hf_clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _hf_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None

def _hf_world_pin_pct(lat: float, lon: float) -> tuple[float, float]:
    """
    Convert lat/lon to simple equirectangular world-map percentages.

    This is a display hint only. It is not award/geocoding logic.
    """
    lat = max(-90.0, min(90.0, float(lat)))
    lon = max(-180.0, min(180.0, float(lon)))

    x_pct = ((lon + 180.0) / 360.0) * 100.0
    y_pct = ((90.0 - lat) / 180.0) * 100.0

    return round(x_pct, 2), round(y_pct, 2)

def _hf_normalize_country_name(value: Any) -> str:
    text = _hf_clean_text(value).lower()
    for old, new in (
        ("&", " and "),
        (",", " "),
        (";", " "),
        (":", " "),
        ("(", " "),
        (")", " "),
        (".", ""),
        ("'", ""),
    ):
        text = text.replace(old, new)
    return " ".join(text.split())


def _hf_load_country_codes() -> dict[str, Any]:
    global _hf_country_codes_cache

    if _hf_country_codes_cache is not None:
        return _hf_country_codes_cache

    paths = [
        os.environ.get("RT_HF_COUNTRY_CODES_PATH", ""),
        "/opt/rollingthunder/config/hf_country_codes.json",
        str(CONFIG_PATH.parent / "hf_country_codes.json"),
    ]

    for path in paths:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            aliases = loaded.get("aliases") if isinstance(loaded, dict) else {}
            dxcc = loaded.get("dxcc") if isinstance(loaded, dict) else {}

            if not isinstance(aliases, dict):
                aliases = {}
            if not isinstance(dxcc, dict):
                dxcc = {}

            _hf_country_codes_cache = {
                "aliases": {
                    _hf_normalize_country_name(k): _hf_clean_text(v).upper()
                    for k, v in aliases.items()
                    if _hf_clean_text(k) and len(_hf_clean_text(v)) == 2
                },
                "dxcc": {
                    _hf_clean_text(k): _hf_clean_text(v).upper()
                    for k, v in dxcc.items()
                    if _hf_clean_text(k) and len(_hf_clean_text(v)) == 2
                },
            }
            return _hf_country_codes_cache
        except FileNotFoundError:
            continue
        except Exception:
            continue

    _hf_country_codes_cache = {
        "aliases": {},
        "dxcc": {},
    }
    return _hf_country_codes_cache

def _hf_country_code_from_qrz(payload: Dict[str, Any]) -> str:
    """
    Return an ISO alpha-2-ish country code when we can safely derive one.

    Order:
      1. explicit ISO-like fields
      2. QRZ/DXCC numeric fields via local config mapping
      3. QRZ country/land/name aliases via local config mapping
      4. small built-in fallback aliases
    """
    payload = payload or {}

    for key in ("country_code", "iso2", "cc", "ccc"):
        explicit = _hf_clean_text(payload.get(key)).upper()
        if len(explicit) == 2 and explicit.isalpha():
            return explicit

    country_codes = _hf_load_country_codes()
    dxcc_map = country_codes.get("dxcc") or {}
    aliases = country_codes.get("aliases") or {}

    for key in ("dxcc", "ccode"):
        dxcc_value = _hf_clean_text(payload.get(key))
        if dxcc_value:
            # QRZ values may arrive as "291", "291.0", or similar strings.
            try:
                dxcc_value = str(int(float(dxcc_value)))
            except Exception:
                pass

            mapped = _hf_clean_text(dxcc_map.get(dxcc_value)).upper()
            if len(mapped) == 2 and mapped.isalpha():
                return mapped

    for key in ("country", "land", "entity", "dxcc_name"):
        name = _hf_normalize_country_name(payload.get(key))
        if not name:
            continue

        mapped = _hf_clean_text(aliases.get(name)).upper()
        if len(mapped) == 2 and mapped.isalpha():
            return mapped

    built_in = {
        "united states": "US",
        "united states of america": "US",
        "usa": "US",
        "canada": "CA",
        "mexico": "MX",
        "ireland": "IE",
        "england": "GB",
        "scotland": "GB",
        "wales": "GB",
        "northern ireland": "GB",
        "united kingdom": "GB",
        "great britain": "GB",
        "france": "FR",
        "germany": "DE",
        "spain": "ES",
        "italy": "IT",
        "portugal": "PT",
        "netherlands": "NL",
        "belgium": "BE",
        "switzerland": "CH",
        "austria": "AT",
        "sweden": "SE",
        "norway": "NO",
        "finland": "FI",
        "denmark": "DK",
        "poland": "PL",
        "czech republic": "CZ",
        "czechia": "CZ",
        "slovakia": "SK",
        "hungary": "HU",
        "romania": "RO",
        "greece": "GR",
        "ukraine": "UA",
        "japan": "JP",
        "china": "CN",
        "south korea": "KR",
        "republic of korea": "KR",
        "australia": "AU",
        "new zealand": "NZ",
        "brazil": "BR",
        "argentina": "AR",
        "chile": "CL",
        "south africa": "ZA",
    }

    for key in ("country", "land", "entity", "dxcc_name"):
        name = _hf_normalize_country_name(payload.get(key))
        if name in built_in:
            return built_in[name]

    return ""


def _hf_map_zoom_for_country(country_code: str, country: str) -> int:
    """
    Coarse country/region-level zoom hint for a future static map provider.

    The renderer does not use this for decisions. It is projected data only.
    """
    code = _hf_clean_text(country_code).upper()
    name = _hf_clean_text(country).lower()

    large = {
        "US", "CA", "MX", "BR", "AR", "CL", "AU", "CN", "RU", "IN", "ZA"
    }

    small = {
        "IE", "GB", "NL", "BE", "CH", "AT", "DK", "LU"
    }

    if code in large:
        return 4
    if code in small:
        return 6
    if name in ("united states", "canada", "australia", "china", "brazil"):
        return 4
    return 5


def _hf_map_payload_from_qrz(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build rt:hf:map:selected from already-available QRZ selected data.

    Phase 2 intentionally does not call map, flag, geocoder, or tile services.
    It only projects a render-ready/fallback model for the dumb UI renderer.
    """
    payload = payload or {}

    call = _hf_clean_text(
        payload.get("callsign")
        or payload.get("call")
    ).upper()

    qrz_status = _hf_clean_text(payload.get("status")) or "unknown"
    qrz_message = _hf_clean_text(payload.get("message"))

    country = _hf_clean_text(
        payload.get("country")
        or payload.get("land")
    )

    country_code = _hf_country_code_from_qrz(payload)

    lat = _hf_float_or_none(payload.get("lat"))
    lon = _hf_float_or_none(payload.get("lon"))

    out: Dict[str, Any] = {
        "source": "hf_map_enricher",
        "callsign": call,
        "status": "pending",
        "message": "",
        "country": country,
        "country_code": country_code,
        "flag": {
            "status": "unavailable",
            "label": "FLAG UNAVAILABLE",
        },
        "map": {
            "status": "unavailable",
            "label": "MAP UNAVAILABLE",
        },
        "updated_at_ms": now_ms(),
    }

    if country_code:
        out["flag"] = {
            "status": "ok",
            "url": f"/ui/hf/flags/4x3/{country_code.lower()}.svg",
            "label": country or country_code,
        }

    if lat is not None and lon is not None:
        zoom = _hf_map_zoom_for_country(country_code, country)
        pin_x_pct, pin_y_pct = _hf_world_pin_pct(lat, lon)

        out["map"] = {
            "status": "local_world",
            "lat": lat,
            "lon": lon,
            "pin_lat": lat,
            "pin_lon": lon,
            "pin_x_pct": pin_x_pct,
            "pin_y_pct": pin_y_pct,
            "zoom": zoom,
            "provider": "local_world_map",
            "url": "/ui/hf/maps/world_ascii.svg",
            "label": "Approximate location",
        }

    if country or country_code or lat is not None or lon is not None:
        out["status"] = "partial"
        out["message"] = qrz_message
    else:
        out["status"] = "unavailable"
        out["message"] = qrz_message or "LOCATION NOT AVAILABLE"

    if qrz_status in ("not_configured", "not_found", "unavailable", "no_callsign"):
        out["status"] = "unavailable"
        out["message"] = qrz_message or "LOCATION NOT AVAILABLE"

    return out


def _hf_write_map_selected_from_qrz(r: redis.Redis, payload: Dict[str, Any]) -> None:
    try:
        map_payload = _hf_map_payload_from_qrz(payload)
        _json_set(r, HF_MAP_SELECTED_KEY, map_payload)
        _hf_publish_state_changed(r, [HF_MAP_SELECTED_KEY])
    except Exception as exc:
        try:
            fallback = {
                "source": "hf_map_enricher",
                "callsign": _hf_clean_text((payload or {}).get("callsign")).upper(),
                "status": "unavailable",
                "message": "LOCATION NOT AVAILABLE",
                "country": "",
                "country_code": "",
                "flag": {
                    "status": "unavailable",
                    "label": "FLAG UNAVAILABLE",
                },
                "map": {
                    "status": "unavailable",
                    "label": "MAP UNAVAILABLE",
                },
                "updated_at_ms": now_ms(),
            }
            _json_set(r, HF_MAP_SELECTED_KEY, fallback)
            _hf_publish_state_changed(r, [HF_MAP_SELECTED_KEY])
        except Exception:
            pass

        try:
            publish_last_result(r, {
                "ok": False,
                "intent": "hf.map.enrich",
                "reason": "map_enrich_failed",
                "error": str(exc),
            })
        except Exception:
            pass

def _hf_write_qrz_selected(r: redis.Redis, payload: Dict[str, Any]) -> None:
    _json_set(r, HF_QRZ_SELECTED_KEY, payload)
    _hf_write_map_selected_from_qrz(r, payload)
    _hf_publish_state_changed(r, [HF_QRZ_SELECTED_KEY])


def _hf_qrz_lookup_worker(callsign: str) -> None:
    """
    Background QRZ lookup.

    This must not block HF spot selection, tuning, or logging.
    It owns only rt:hf:qrz:selected and QRZ cache keys.
    """
    call = str(callsign or "").strip().upper()
    if not call:
        return

    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
    )

    try:
        # Do not let an old lookup overwrite a newer selected callsign.
        selected_detail = _json_get(r, HF_SELECTED_DETAIL_KEY, {}) or {}
        selected_call = str(
            selected_detail.get("callsign")
            or selected_detail.get("call")
            or ""
        ).strip().upper()
        if selected_call and selected_call != call:
            return

        qrz_lookup = _load_qrz_lookup_module()
        qrz_client = _load_qrz_client_module()

        result = qrz_lookup.lookup_qrz_with_cache(
            r,
            call,
            qrz_client.qrz_fetch_callsign,
        )

        selected_detail = _json_get(r, HF_SELECTED_DETAIL_KEY, {}) or {}
        selected_call = str(
            selected_detail.get("callsign")
            or selected_detail.get("call")
            or ""
        ).strip().upper()
        if selected_call and selected_call != call:
            return

        if result:
            payload = dict(result)
            payload["source"] = "qrz"
            payload["callsign"] = payload.get("callsign") or call
            payload["status"] = payload.get("status") or "ok"
            payload["updated_at_ms"] = now_ms()
            _hf_write_qrz_selected(r, payload)
        else:
            _hf_write_qrz_selected(
                r,
                _hf_qrz_status_payload(call, "not_found", "USER NOT IN QRZ"),
            )

    except RuntimeError as exc:
        text = str(exc)
        if "credentials" in text.lower() or "configured" in text.lower():
            status = "not_configured"
            msg = "QRZ NOT CONFIGURED"
        else:
            status = "unavailable"
            msg = "QRZ NOT CURRENTLY AVAILABLE"

        try:
            _hf_write_qrz_selected(r, _hf_qrz_status_payload(call, status, msg))
        except Exception:
            pass

    except Exception:
        try:
            _hf_write_qrz_selected(
                r,
                _hf_qrz_status_payload(
                    call,
                    "unavailable",
                    "QRZ NOT CURRENTLY AVAILABLE",
                ),
            )
        except Exception:
            pass

    finally:
        with _qrz_threads_lock:
            _qrz_threads.pop(call, None)


def _hf_update_qrz_for_callsign(r: redis.Redis, callsign: str) -> None:
    """
    Start QRZ enrichment for selected HF callsign.

    This writes a fast placeholder immediately, then performs QRZ/cache lookup
    in a background thread so radio.tune is not delayed.
    """
    if NODE_ID != "rt-controller":
        return

    call = str(callsign or "").strip().upper()
    if not call:
        _hf_write_qrz_selected(
            r,
            _hf_qrz_status_payload("", "no_callsign", "No selected callsign"),
        )
        return

    if not HF_QRZ_API_ENABLED:
        _hf_write_qrz_selected(
            r,
            _hf_qrz_status_payload(call, "not_configured", "QRZ NOT CONFIGURED"),
        )
        return

    # If cached, publish immediately and skip the thread.
    try:
        qrz_lookup = _load_qrz_lookup_module()
        cached = qrz_lookup.get_cached_qrz(r, call)
        if cached is not None:
            payload = dict(cached)
            payload["source"] = "qrz"
            payload["callsign"] = payload.get("callsign") or call
            payload["status"] = payload.get("status") or "ok"
            payload["updated_at_ms"] = now_ms()
            _hf_write_qrz_selected(r, payload)
            return
    except Exception:
        pass

    _hf_write_qrz_selected(
        r,
        _hf_qrz_status_payload(call, "loading", "QRZ lookup…"),
    )

    with _qrz_threads_lock:
        if call in _qrz_threads:
            return
        _qrz_threads[call] = time.time()

    t = threading.Thread(
        target=_hf_qrz_lookup_worker,
        args=(call,),
        name=f"hf-qrz-{call}",
        daemon=True,
    )
    t.start()

def _utc_date_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _hf_status_hash_key():
    return f"rt:hf:spot_status:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

def _hf_status_map(r: redis.Redis):
    key = _hf_status_hash_key()
    raw = r.hgetall(key) or {}
    out = {}
    for k, v in raw.items():
        try:
            out[k] = json.loads(v)
        except Exception:
            continue
    return out

def _hf_spot_key(spot, fallback_band=""):
    call = str(spot.get("callsign") or "").strip().upper()
    freq = int(spot.get("freq_hz") or 0)
    band = str(spot.get("band") or fallback_band or "").strip()
    return f"{call}|{freq}|{band}"


def _hf_apply_status(spots_model, status_map):
    items = spots_model.get("items") or []
    fallback_band = str(spots_model.get("band") or "").strip()

    for item in items:
        key = _hf_spot_key(item, fallback_band)
        status_obj = status_map.get(key) or {}
        status = status_obj.get("status")

        item["band"] = item.get("band") or fallback_band

        if status in {"worked", "heard", "cannot_hear"}:
            item["status"] = status
            item["row_style"] = status
        else:
            item["status"] = ""
            item["row_style"] = "default"
#=================================================================================
#   END OF HF PAGE ADDITIONS                                                      
#=================================================================================

def env_truthy(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def handle_ui_browse_delta(r: redis.Redis, params: Dict[str, Any]) -> None:
    delta = int(params.get("delta", 0))
    panel = str(params.get("panel") or "").strip()

    if delta == 0 or not panel:
        return

    key = f"rt:ui:browse:{panel}"
    raw = r.get(key)

    try:
        state = json.loads(raw) if raw else {}
    except Exception:
        state = {}

    # Initialize if needed
    if not state:
        state = {
            "active": True,
            "panel": panel,
            "selected_index": 0,
            "window_start": 0,
            "window_size": 7
        }

    selected = int(state.get("selected_index", 0))
    window_start = int(state.get("window_start", 0))
    window_size = int(state.get("window_size", 7))

    selected += 1 if delta > 0 else -1
    selected = max(0, selected)

    # Window tracking
    if selected < window_start:
        window_start = selected
    elif selected >= window_start + window_size:
        window_start = selected - window_size + 1

    state["selected_index"] = selected

    # --- NEW: auto tune on selection change ---
    state["window_start"] = window_start
    state["window_size"] = window_size
    state["updated_at_ms"] = int(time.time() * 1000)

    r.set(key, json.dumps(state, separators=(",", ":")))

    publish_state_changed(r, [key], source="ui_intent_worker")

def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def default_pota_context() -> Dict[str, Any]:
    return {
        "selected_park_ref": "",
        "selected_park_name": "Not in a park",
        "selected_park_refs": [],
        "selected_park_names": [],
        "left_selected_park_refs": [],
        "selected_band": "",
        "grid": "",
        "selection_ts": now_ms(),
    }


def load_json_object(r: redis.Redis, key: str) -> Dict[str, Any] | None:
    raw = r.get(key)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen = set()
    for item in value:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def normalize_pota_context(existing: Dict[str, Any] | None) -> Dict[str, Any]:
    base = default_pota_context()
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

    if ctx["selected_band"] and ctx["selected_band"] not in POTA_BAND_ORDER:
        ctx["selected_band"] = ""

    # Keep singular/plural park fields compatible
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
    except Exception:
        ctx["selection_ts"] = base["selection_ts"]

    return ctx


def now_ms() -> int:
    return int(time.time() * 1000)


def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    r.ping()
    return r


def publish_state_changed(r: redis.Redis, keys: list[str], source: str = "ui_intent_worker") -> None:
    evt = {
        "topic": "state.changed",
        "payload": {"keys": keys[:50]},
        "ts_ms": now_ms(),
        "source": source,
    }
    r.publish(SYSTEM_BUS_CH, json.dumps(evt, separators=(",", ":"), ensure_ascii=False))


def publish_last_result(r: redis.Redis, payload: Dict[str, Any]) -> None:
    ts = int(time.time() * 1000)

    page = str(
        payload.get("page")
        or payload.get("source")
        or payload.get("source_page")
        or ""
    ).strip()

    if page not in ("home", "pota", "hf"):
        page = None

    focused_panel = payload.get("focused_panel")
    if focused_panel is None:
        focused_panel = payload.get("panel") or payload.get("focusedPanel")

    last_result = {
        "result": "ok" if payload.get("ok") else "error",
        "intent": payload.get("topic") or payload.get("intent") or "unknown",
        "reason": payload.get("msg") or payload.get("reason"),
        "execution_id": str(payload.get("execution_id") or ts),
        "page": page,
        "focused_panel": focused_panel,
        "ts_ms": int(payload.get("ts_ms") or ts),
    }

    r.set(LAST_RESULT_KEY, json.dumps(last_result, separators=(",", ":")), px=5000)

    r.publish(
        SYSTEM_BUS_CH,
        json.dumps({
            "topic": "state.changed",
            "payload": {
                "keys": [LAST_RESULT_KEY],
            },
            "ts_ms": ts,
            "source": "ui_intent_worker",
        })
    )

def _truthy(x: Any) -> bool:
    if x is True:
        return True
    if isinstance(x, str) and x.strip().lower() in ("1", "true", "yes", "y", "on"):
        return True
    if isinstance(x, (int, float)) and x == 1:
        return True
    return False


def _radio_result_base(params: Dict[str, Any]) -> Dict[str, Any]:
    target = str(params.get("nodeId") or params.get("node_id") or "rt-radio").strip()

    page = str(
        params.get("page")
        or params.get("source")
        or params.get("source_page")
        or ""
    ).strip()

    if page not in ("home", "pota", "hf"):
        page = None

    return {
        "topic": "ui.radio.tune.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
        "page": page,
        "source": params.get("source"),
        "focused_panel": params.get("focused_panel") or params.get("panel"),
        "freq_hz": params.get("freq_hz"),
        "band": str(params.get("band") or "").strip(),
        "mode": str(params.get("mode") or "").strip(),
        "passband_hz": params.get("passband_hz"),
        "autotune": _truthy(params.get("autotune")),
    }


def _publish_radio_tune_ok(
    r: redis.Redis,
    base: Dict[str, Any],
    *,
    freq_hz: int,
    mode: str,
    passband_hz: int,
    autotune_requested: bool,
    autotune_attempted: bool,
    autotune_error: str | None = None,
) -> None:
    payload = {
        **base,
        "ok": True,
        "status": "ok",
        "msg": "tuned_successfully",
        "message": "tuned successfully",
        "freq_hz": freq_hz,
        "mode": mode,
        "passband_hz": passband_hz,
        "autotune_requested": autotune_requested,
        "autotune_attempted": autotune_attempted,
    }
    if autotune_error:
        payload["autotune_error"] = autotune_error

    publish_last_result(r, payload)


def _publish_radio_tune_error(
    r: redis.Redis,
    base: Dict[str, Any],
    *,
    error_code: str,
    message: str,
) -> None:
    publish_last_result(
        r,
        {
            **base,
            "ok": False,
            "status": "error",
            "error_code": error_code,
            "msg": message,
            "message": message,
        },
    )


def _publish_radio_atas_tune_result(
    r: redis.Redis,
    *,
    ok: bool,
    target: str,
    band: str,
    msg: str,
    error_code: str | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "topic": "ui.radio.atas_tune.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
        "band": band,
        "ok": ok,
        "msg": msg,
        "message": msg,
    }
    if ok:
        payload["status"] = "ok"
    else:
        payload["status"] = "error"
        if error_code:
            payload["error_code"] = error_code

    publish_last_result(r, payload)


def reboot_this_node() -> tuple[bool, str]:
    cmd = ["systemctl", "--no-wall"]
    if REBOOT_MODE == "poweroff":
        cmd += ["poweroff"]
    else:
        cmd += ["reboot"]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
        if res.returncode == 0:
            return True, f"{REBOOT_MODE}_initiated"
        msg = (res.stderr or res.stdout or "").strip()[:500]
        return False, f"{REBOOT_MODE}_failed rc={res.returncode} {msg}"
    except subprocess.TimeoutExpired:
        return True, f"{REBOOT_MODE}_initiated_timeout"
    except Exception as e:
        return False, f"exception:{type(e).__name__}:{e}"


def handle_node_reboot(r: redis.Redis, params: Dict[str, Any]) -> None:
    target = str(params.get("nodeId") or params.get("node_id") or "").strip()
    confirm = _truthy(params.get("confirm"))

    base = {
        "topic": "ui.node.reboot.result",
        "node": NODE_ID,
        "target": target,
        "ts_ms": now_ms(),
    }

    if not target:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:missing_nodeId"})
        return

    if target != NODE_ID:
        publish_last_result(r, {**base, "ok": False, "msg": "not_for_this_node"})
        return

    if not ALLOW_NODE_REBOOT:
        publish_last_result(r, {**base, "ok": False, "msg": "reboot_disabled"})
        return

    if not confirm:
        publish_last_result(r, {**base, "ok": False, "msg": "not_confirmed"})
        return

    ok, msgtxt = reboot_this_node()
    publish_last_result(r, {**base, "ok": ok, "msg": msgtxt})


# --- ONLY SHOWING THE MODIFIED FUNCTION ---

def handle_radio_tune(r: redis.Redis, params: Dict[str, Any]) -> None:
    base = _radio_result_base(params)
    target = str(base["target"]).strip()

    if target != NODE_ID:
        _publish_radio_tune_error(r, base, error_code="not_for_this_node", message="not_for_this_node")
        return

    if NODE_ID != "rt-radio":
        _publish_radio_tune_error(r, base, error_code="wrong_node_role", message="wrong_node_role")
        return

    freq_raw = params.get("freq_hz")
    try:
        freq_hz = int(freq_raw)
    except Exception:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message="freq_hz is required and must be an integer",
        )
        return

    if freq_hz < 30_000 or freq_hz > 56_000_000:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message="freq_hz out of supported range",
        )
        return

    mode_raw = params.get("mode")
    mode = str(mode_raw).strip() if mode_raw is not None else None
    if mode == "":
        mode = None

    # ✅ NEW: extract band (safe, optional)
    band_raw = params.get("band")
    band = str(band_raw).strip() if band_raw is not None else None
    if band == "":
        band = None

    passband_raw = params.get("passband_hz")
    if passband_raw in (None, ""):
        passband_hz = None
    else:
        try:
            passband_hz = int(passband_raw)
        except Exception:
            _publish_radio_tune_error(
                r,
                base,
                error_code="invalid_payload",
                message="passband_hz must be an integer when provided",
            )
            return

    autotune = _truthy(params.get("autotune"))

    try:
        runtime = _load_radio_runtime()
    except Exception as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="radio_runtime_unavailable",
            message=f"radio runtime unavailable: {type(e).__name__}: {e}",
        )
        return

    service = runtime["service"]
    HamlibError = runtime["HamlibError"]
    RigctldCommandError = runtime["RigctldCommandError"]
    RigctldProtocolError = runtime["RigctldProtocolError"]
    RigctldUnreachable = runtime["RigctldUnreachable"]
    RadioValidationError = runtime["RadioValidationError"]

    try:
        # ✅ NEW: pass band into backend
        result = service.tune(
            freq_hz=freq_hz,
            mode=mode,
            passband_hz=passband_hz,
            autotune=autotune,
            band=band,  # <-- THIS is the key change
        )

        _publish_radio_tune_ok(
            r,
            base,
            freq_hz=int(result.freq_hz),
            mode=str(result.mode),
            passband_hz=int(result.passband_hz),
            autotune_requested=bool(result.autotune_requested),
            autotune_attempted=bool(result.autotune_attempted),
            autotune_error=result.autotune_error,
        )

    except RadioValidationError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="invalid_payload",
            message=str(e),
        )
    except RigctldUnreachable:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_unreachable",
            message="unable to contact rigctld",
        )
    except RigctldProtocolError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_protocol_error",
            message=str(e),
        )
    except RigctldCommandError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="rigctld_command_error",
            message=f"rigctld rejected command: {e.code}",
        )
    except HamlibError as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="radio_error",
            message=str(e),
        )
    except Exception as e:
        _publish_radio_tune_error(
            r,
            base,
            error_code="unexpected_error",
            message=f"{type(e).__name__}:{e}",
        )


def handle_radio_atas_tune(r: redis.Redis, params: Dict[str, Any]) -> None:
    target = str(params.get("nodeId") or params.get("node_id") or "rt-radio").strip()
    band = str(params.get("band") or "").strip()

    if target != NODE_ID:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="not_for_this_node",
            error_code="not_for_this_node",
        )
        return

    if NODE_ID != "rt-radio":
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="wrong_node_role",
            error_code="wrong_node_role",
        )
        return

    if not band:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="band is required",
            error_code="invalid_payload",
        )
        return

    try:
        runtime = _load_radio_runtime()
    except Exception as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"radio runtime unavailable: {type(e).__name__}: {e}",
            error_code="radio_runtime_unavailable",
        )
        return

    service = runtime["service"]
    HamlibError = runtime["HamlibError"]
    RigctldCommandError = runtime["RigctldCommandError"]
    RigctldProtocolError = runtime["RigctldProtocolError"]
    RigctldUnreachable = runtime["RigctldUnreachable"]
    RadioValidationError = runtime["RadioValidationError"]

    atas_fn = getattr(service, "atas_tune", None)
    if not callable(atas_fn):
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="atas_tune not supported by current radio service",
            error_code="not_supported",
        )
        return

    try:
        result = atas_fn(band=band)

        if isinstance(result, dict):
            completed = bool(result.get("completed", False))
            timed_out = bool(result.get("timed_out", False))
            msg = str(result.get("msg") or result.get("message") or "atas_tune_requested")

            payload_msg = msg
            if timed_out:
                _publish_radio_atas_tune_result(
                    r,
                    ok=False,
                    target=target,
                    band=band,
                    msg=payload_msg,
                    error_code="timeout",
                )
                return

            _publish_radio_atas_tune_result(
                r,
                ok=completed or bool(result.get("tuner_started", False)),
                target=target,
                band=band,
                msg=payload_msg,
            )
            return

        _publish_radio_atas_tune_result(
            r,
            ok=True,
            target=target,
            band=band,
            msg="atas_tune_requested",
        )

    except RadioValidationError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="invalid_payload",
        )
    except RigctldUnreachable:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg="unable to contact rigctld",
            error_code="rigctld_unreachable",
        )
    except RigctldProtocolError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="rigctld_protocol_error",
        )
    except RigctldCommandError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"rigctld rejected command: {e.code}",
            error_code="rigctld_command_error",
        )
    except HamlibError as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=str(e),
            error_code="radio_error",
        )
    except Exception as e:
        _publish_radio_atas_tune_result(
            r,
            ok=False,
            target=target,
            band=band,
            msg=f"{type(e).__name__}:{e}",
            error_code="unexpected_error",
        )


def handle_pota_select_band(r: redis.Redis, params: Dict[str, Any]) -> None:
    band = str(params.get("band") or "").strip()

    base = {
        "topic": "ui.pota.select_band.result",
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "band": band,
        "context_key": POTA_CONTEXT_KEY,
    }

    if NODE_ID != "rt-controller":
        publish_last_result(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    if not band:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:missing_band"})
        return

    if band not in POTA_BAND_ORDER:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:invalid_band"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))
    ctx["selected_band"] = band
    ctx["selection_ts"] = now_ms()

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_last_result(r, {**base, "ok": True, "msg": "selected_band_updated"})


POTA_NEARBY_KEY = os.environ.get("RT_POTA_NEARBY_KEY", "rt:pota:nearby")


def nearby_choices_by_ref(r: redis.Redis) -> dict[str, dict[str, Any]]:
    nearby = load_json_object(r, POTA_NEARBY_KEY)
    if not isinstance(nearby, dict):
        return {}

    choices = nearby.get("choices")
    if not isinstance(choices, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for item in choices:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("reference", "") or "").strip()
        if not ref:
            continue
        out[ref] = item
    return out


def handle_pota_select_park(r: redis.Redis, params: Dict[str, Any]) -> None:
    park_ref = str(params.get("park_ref") or params.get("reference") or "").strip()

    base = {
        "topic": "ui.pota.select_park.result",
        "node": NODE_ID,
        "ts_ms": now_ms(),
        "park_ref": park_ref,
        "context_key": POTA_CONTEXT_KEY,
        "nearby_key": POTA_NEARBY_KEY,
    }

    if NODE_ID != "rt-controller":
        publish_last_result(r, {**base, "ok": False, "msg": "wrong_node_role"})
        return

    ctx = normalize_pota_context(load_json_object(r, POTA_CONTEXT_KEY))

    if not park_ref:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"
        ctx["selected_park_refs"] = []
        ctx["selected_park_names"] = []
        ctx["left_selected_park_refs"] = []
        ctx["selection_ts"] = now_ms()

        r.set(POTA_CONTEXT_KEY, compact_json(ctx))
        publish_last_result(r, {**base, "ok": True, "msg": "selected_park_cleared"})
        return

    nearby_map = nearby_choices_by_ref(r)
    choice = nearby_map.get(park_ref)
    if not choice:
        publish_last_result(r, {**base, "ok": False, "msg": "bad_request:park_not_in_nearby_choices"})
        return

    park_name = str(choice.get("name") or "").strip()
    grid = str(choice.get("grid") or ctx.get("grid", "") or "").strip()

    selected_refs = list(ctx.get("selected_park_refs", []))
    selected_names = list(ctx.get("selected_park_names", []))

    name_by_ref: dict[str, str] = {}
    for i, ref in enumerate(selected_refs):
        ref_s = str(ref).strip()
        if not ref_s:
            continue
        if i < len(selected_names):
            nm = str(selected_names[i]).strip()
            if nm:
                name_by_ref[ref_s] = nm

    if park_ref in selected_refs:
        selected_refs = [ref for ref in selected_refs if ref != park_ref]
        name_by_ref.pop(park_ref, None)
        result_msg = "selected_park_removed"
    else:
        selected_refs.append(park_ref)
        if park_name:
            name_by_ref[park_ref] = park_name
        result_msg = "selected_park_added"

    rebuilt_names: list[str] = []
    for ref in selected_refs:
        nm = name_by_ref.get(ref) or str(nearby_map.get(ref, {}).get("name") or "").strip()
        if nm:
            rebuilt_names.append(nm)
        else:
            rebuilt_names.append("")

    prior_left = [str(x).strip() for x in ctx.get("left_selected_park_refs", []) if str(x).strip()]
    left_selected = [ref for ref in prior_left if ref not in selected_refs]

    ctx["selected_park_refs"] = selected_refs
    ctx["selected_park_names"] = rebuilt_names
    ctx["left_selected_park_refs"] = left_selected
    ctx["grid"] = grid
    ctx["selection_ts"] = now_ms()

    if selected_refs:
        ctx["selected_park_ref"] = selected_refs[0]
        first_name = rebuilt_names[0] if rebuilt_names else ""
        ctx["selected_park_name"] = first_name or ""
    else:
        ctx["selected_park_ref"] = ""
        ctx["selected_park_name"] = "Not in a park"

    r.set(POTA_CONTEXT_KEY, compact_json(ctx))

    publish_last_result(r, {
        **base,
        "ok": True,
        "msg": result_msg,
        "park_name": park_name,
        "selected_park_refs": selected_refs,
    })

#=================================================================================
# HF HANDLEERS and PROCEDURES
#=================================================================================
def handle_hf_spot_outcome(r: redis.Redis, params: Dict[str, Any]) -> None:
    status = str(params.get("status") or "").strip()

    # HF uses the same status keys as the POTA-style modal.
    if status not in {"worked", "heard_not_worked", "cannot_hear"}:
        return

    # --- enrich from selected detail if missing ---
    detail = _json_get(r, HF_SELECTED_DETAIL_KEY, {}) or {}

    callsign = str(params.get("callsign") or detail.get("callsign") or detail.get("call") or "").strip()
    freq_hz = int(params.get("freq_hz") or detail.get("freq_hz") or 0)
    band = str(params.get("band") or detail.get("band") or "").strip()
    mode = str(params.get("mode") or detail.get("mode") or _hf_mode_for_freq(freq_hz, band) or "").strip()

    ts_utc = params.get("timestamp_utc") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    date_key = _utc_date_str()
    redis_key = f"rt:hf:spot_status:{date_key}"

    spot_key = f"{callsign}|{freq_hz}|{band}"

    value = {
        "status": status,
        "callsign": callsign,
        "freq_hz": freq_hz,
        "band": band,
        "mode": mode,
        "timestamp_utc": ts_utc,
        "source": "hf",
    }

    r.hset(redis_key, spot_key, json.dumps(value, separators=(",", ":")))

    changed_keys = [redis_key]

    # Update currently selected HF spot model immediately so the row color changes
    # without waiting for a band reselect or poller refresh.
    spots_model = _json_get(r, "rt:hf:spots:selected", {}) or {}
    changed_selected_model = False

    def matches_current_spot(item: Dict[str, Any]) -> bool:
        try:
            item_freq_hz = int(item.get("freq_hz") or item.get("frequency") or 0)
        except Exception:
            item_freq_hz = 0

        item_callsign = str(item.get("callsign") or item.get("call") or "").strip()
        item_band = str(item.get("band") or "").strip()

        return (
            item_callsign == callsign
            and item_freq_hz == freq_hz
            and item_band == band
        )

    for list_key in ("items", "spots"):
        items = spots_model.get(list_key)
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if matches_current_spot(item):
                item["status"] = status
                item["row_style"] = status
                changed_selected_model = True

    if changed_selected_model:
        _json_set(r, "rt:hf:spots:selected", spots_model)
        changed_keys.append("rt:hf:spots:selected")

    selected_detail = _json_get(r, HF_SELECTED_DETAIL_KEY, {}) or {}
    if isinstance(selected_detail, dict):
        try:
            detail_freq_hz = int(selected_detail.get("freq_hz") or 0)
        except Exception:
            detail_freq_hz = 0

        detail_callsign = str(selected_detail.get("callsign") or selected_detail.get("call") or "").strip()
        detail_band = str(selected_detail.get("band") or "").strip()

        if detail_callsign == callsign and detail_freq_hz == freq_hz and detail_band == band:
            selected_detail["status"] = status
            selected_detail["row_style"] = status
            _json_set(r, HF_SELECTED_DETAIL_KEY, selected_detail)
            changed_keys.append(HF_SELECTED_DETAIL_KEY)

    # --- only log contacts when worked ---
    if status == "worked":
        intent = {
            "intent": "radio.log_qso",
            "params": {
                "call": callsign,
                "band": band,
                "freq_hz": freq_hz,
                "mode": mode,
                "timestamp_utc": ts_utc,
                "comment": "HF contact",
                "source": "hf",
            },
            "source": {
                "type": "hf",
                "node": NODE_ID,
            },
            "timestamp": int(time.time() * 1000),
        }

        r.publish(INTENTS_CH, json.dumps(intent, separators=(",", ":")))

    _hf_publish_state_changed(r, changed_keys)

#=================================================================================
# END OF HF PROCEDURES
#================================================================================

def main() -> None:
    r = redis_client()
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    publish_last_result(
        r,
        {
            "topic": "ui.intent.worker.hello",
            "node": NODE_ID,
            "ts_ms": now_ms(),
            "intents_channel": INTENTS_CH,
            "capabilities": {
                "node_reboot": ALLOW_NODE_REBOOT,
                "radio_tune": NODE_ID == "rt-radio",
                "radio_atas_tune": NODE_ID == "rt-radio",
                "radio_tune_backend": NODE_ID == "rt-radio",
                "pota_select_band": NODE_ID == "rt-controller",
                "pota_select_park": NODE_ID == "rt-controller",
                "mode": REBOOT_MODE,
            },
        },
    )

    while True:
        msg = ps.get_message(timeout=1.0)
        if not msg or msg.get("type") != "message":
            time.sleep(0.05)
            continue

        raw = msg.get("data")
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            continue

        intent = str(obj.get("intent") or "").strip()
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}

        if intent == "node.reboot":
            handle_node_reboot(r, params)
            continue

        if intent == "ui.browse.delta":
            handle_ui_browse_delta(r, params)
            continue

#=================================================================================
# Radio intents
#=================================================================================
        if intent == "radio.tune":
            if NODE_ID == "rt-radio":
                handle_radio_tune(r, params)
            continue

#=================================================================================
# POTA intents
#=================================================================================        
        if intent == "pota.select_band":
            handle_pota_select_band(r, params)
            continue

        if intent == "radio.atas_tune":
            handle_radio_atas_tune(r, params)
            continue

        if intent == "pota.select_park":
            handle_pota_select_park(r, params)
            continue

#=================================================================================
# HF intents
#=================================================================================
        if intent in ("hf.select_band", "hf.select_spot", "hf.spot.outcome"):
            if NODE_ID != "rt-controller":
                continue    
            if intent == "hf.select_band":

                params = obj.get("params") or {}
                band = params.get("band") or params.get("id") or params.get("selected_id")

                if not band:
                    continue
                _hf_update_bands_selected_id(r, band)                                

                HF_CONTEXT_KEY = os.environ.get("RT_HF_CONTEXT_KEY", "rt:hf:context")

                ctx = load_json_object(r, HF_CONTEXT_KEY) or {}
                ctx["selected_band"] = band
                ctx["selection_ts"] = now_ms()

                r.set(HF_CONTEXT_KEY, compact_json(ctx))

                # Load spots
                spots_key = f"rt:hf:spots:{band}"
                spots_model = _json_get(r, spots_key)

                if not spots_model:
                    spots_model = _json_get(r, "rt:hf:spots:selected", {}) or {}

                spots_model["band"] = band

                status_map = _hf_status_map(r)
                _hf_apply_status(spots_model, status_map)
                items = spots_model.get("items") or []
                selected = items[0] if items else None

                if not selected:
                    continue

                selected_id = selected.get("id")

                ctx["selected_spot_id"] = selected_id
                r.set(HF_CONTEXT_KEY, compact_json(ctx))

                spots_model["selected_id"] = selected_id
                spots_model["band"] = band

                detail = dict(selected)
                detail["band"] = band
                detail["mode"] = detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band)

                _json_set(r, "rt:hf:spots:selected", spots_model)
                _json_set(r, "rt:hf:spots:selected_detail", detail)

                _hf_update_qso_history_for_callsign(
                    r,
                    str(detail.get("callsign") or detail.get("call") or ""),
                )

                _hf_update_qrz_for_callsign(
                    r,
                    str(detail.get("callsign") or detail.get("call") or ""),
                )

                _hf_publish_state_changed(r, [
                    HF_BANDS_KEY,
                    "rt:hf:context",
                    "rt:hf:spots:selected",
                    "rt:hf:spots:selected_detail",
                    HF_QSO_HISTORY_SELECTED_KEY,
                    HF_QRZ_SELECTED_KEY,
                    HF_MAP_SELECTED_KEY,
                ])

                freq = selected.get("freq_hz")

                if freq:

                    r.publish("rt:ui:intents", json.dumps({
                        "intent": "radio.tune",
                        "params": {
                            "freq_hz": int(freq),
                            "band": band,
                            "mode": detail["mode"],
                            "source": "hf",
                            "spot_id": selected_id,
                            "callsign": selected.get("callsign"),
                            "nodeId": "rt-radio"
                        },
                        "source": {
                            "type": "ui_intent_worker",
                            "node": "rt-controller"
                        },
                        "timestamp": now_ms()
                    }))

                continue

            if intent == "hf.select_spot":
                params = obj.get("params") or {}

                selected_id = (
                    params.get("spot_id")
                    or params.get("id")
                    or params.get("selected_id")
                )

                context = _json_get(r, "rt:hf:context", {}) or {}
                band = params.get("band") or context.get("selected_band")

                spots_model = _json_get(r, "rt:hf:spots:selected", {}) or {}

                if band:
                    spots_model["band"] = band

                status_map = _hf_status_map(r)
                _hf_apply_status(spots_model, status_map)

                selected = _hf_selected_spot(spots_model, selected_id)

                if not selected:
                    _json_set(
                        r,
                        HF_QSO_HISTORY_SELECTED_KEY,
                        {
                            "source": "sqlite",
                            "callsign": "",
                            "items": [],
                            "limit": 5,
                            "updated_at_ms": now_ms(),
                        },
                    )
                    _hf_publish_state_changed(r, [HF_QSO_HISTORY_SELECTED_KEY])
                    continue

                selected_id = selected.get("id")
                band = band or spots_model.get("band") or selected.get("band")

                context["selected_band"] = band
                context["selected_spot_id"] = selected_id
                context["selection_ts"] = int(time.time() * 1000)

                spots_model["selected_id"] = selected_id

                detail = dict(selected)
                detail["band"] = band
                detail["mode"] = detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band)

                if detail.get("freq_hz"):
                    tune_payload = {
                        "intent": "radio.tune",
                        "params": {
                            "freq_hz": int(detail["freq_hz"]),
                            "band": band,
                            "mode": detail.get("mode") or _hf_mode_for_freq(detail.get("freq_hz"), band),
                            "source": "hf",
                            "spot_id": selected_id,
                            "callsign": detail.get("callsign"),
                            "nodeId": "rt-radio",
                        },
                        "source": {
                            "type": "ui_intent_worker:hf",
                            "node": "rt-controller",
                        },
                        "timestamp": int(time.time() * 1000),
                    }
                    r.publish("rt:ui:intents", json.dumps(tune_payload, separators=(",", ":")))

                _json_set(r, "rt:hf:context", context)
                _json_set(r, "rt:hf:spots:selected", spots_model)
                _json_set(r, "rt:hf:spots:selected_detail", detail)

                _hf_update_qso_history_for_callsign(
                    r,
                    str(
                        detail.get("callsign")
                        or detail.get("call")
                        or params.get("callsign")
                        or params.get("call")
                        or ""
                    ),
                )

                _hf_update_qrz_for_callsign(
                    r,
                    str(
                        detail.get("callsign")
                        or detail.get("call")
                        or params.get("callsign")
                        or params.get("call")
                        or ""
                    ),
                )
                
                _hf_publish_state_changed(r, [
                    "rt:hf:context",
                    "rt:hf:spots:selected",
                    "rt:hf:spots:selected_detail",
                    HF_QSO_HISTORY_SELECTED_KEY,
                    HF_QRZ_SELECTED_KEY,
                    HF_MAP_SELECTED_KEY,
                ])

                continue

            elif intent == "hf.spot.outcome":
                handle_hf_spot_outcome(r, params)                 
    

if __name__ == "__main__":
    main()