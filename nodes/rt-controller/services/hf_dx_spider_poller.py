#!/opt/rollingthunder/.venv/bin/python3
"""
RollingThunder - HF DX Spider Poller

Controller-side only.

Reads:
- /opt/rollingthunder/config/app.json -> globals.dxSpider
- rt:hf:context
- rt:interaction:state
- rt:hf:spots:selected
- rt:hf:spots:selected_detail

Writes:
- rt:hf:bands
- rt:hf:spots:selected
- rt:hf:spots:selected_detail
- rt:hf:context
- rt:hf:dxspider:last_poll
- rt:hf:dxspider:last_error

Rules:
- UI remains renderer-only.
- Browser does not poll DX Spider.
- Browser does not dedupe/filter/select spots.
- Preserve focused/selected spot during refresh when browsing hf_spots_summary.

Concurrency:
- Each DX Spider node is polled concurrently.
- Redis is written once after all node polls complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis


SERVICE_NAME = "hf_dx_spider_poller"
SERVICE_VERSION = "0.3.0"

APP_JSON_DEFAULT = "/opt/rollingthunder/config/app.json"

DEFAULT_BAND_ORDER = ["40m", "20m", "17m", "15m", "12m", "10m"]


# Live DX Spider alert format:
# DX de AJ4LN:      7194.0  K8ERS                                       0200Z
DX_LINE_RE = re.compile(
    r"DX\s+de\s+(?P<spotter>[A-Z0-9/]+)[-#: ]+\s*"
    r"(?P<freq_khz>\d+(?:\.\d+)?)\s+"
    r"(?P<callsign>[A-Z0-9/]+)"
    r"(?:\s+(?P<comment>.*?))?"
    r"\s*$",
    re.IGNORECASE,
)


# sh/dx table format:
# 7143.9 PP5IP       17-May-2026 0158Z LSB                           <PP6RF>
# 14210.0 PP5DZ      17-May-2026 0157Z CQ Marechal Rondon            <PP5DZ>
DX_TABLE_LINE_RE = re.compile(
    r"^\s*"
    r"(?P<freq_khz>\d+(?:\.\d+)?)\s+"
    r"(?P<callsign>[A-Z0-9/]+)\s+"
    r"(?P<date>\d{1,2}-[A-Za-z]{3}-\d{4})\s+"
    r"(?P<time>\d{4})Z\s*"
    r"(?P<comment>.*?)"
    r"(?:\s+<(?P<spotter>[A-Z0-9/]+)>)?"
    r"\s*$",
    re.IGNORECASE,
)


def utc_now_ms() -> int:
    return int(time.time() * 1000)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def stable_compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def clean_dx_text(value: str) -> str:
    """
    Remove bell/control characters commonly emitted by telnet DX clusters.
    """
    return "".join(
        ch for ch in str(value or "")
        if ch == "\t" or ch == "\n" or ord(ch) >= 32
    ).strip()


def load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return obj if isinstance(obj, dict) else {}


def get_path(obj: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


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


@dataclass
class DxNode:
    id: str
    name: str
    location: str
    telnet_address: str
    telnet_port: int
    logon_name: str
    set_name_command: str
    set_location_command: str


@dataclass
class Config:
    app_json: str
    redis_url: str
    key_prefix: str
    poll_interval_sec: int
    spots_per_node: int
    connect_timeout_sec: int
    read_timeout_sec: int
    ssb_segments: Dict[str, List[Dict[str, Any]]]
    nodes: List[DxNode]
    debug_samples: bool


def normalize_dxspider_config(app: Dict[str, Any], app_json: str) -> Config:
    dx = get_path(app, "globals.dxSpider", {}) or {}

    explicit_redis_url = env_str("RT_REDIS_URL", "")
    if explicit_redis_url:
        redis_url = explicit_redis_url
    else:
        redis_host = env_str("RT_REDIS_HOST", "127.0.0.1")
        redis_port = env_int("RT_REDIS_PORT", 6379)
        redis_db = env_int("RT_REDIS_DB", 0)
        redis_password = env_str("RT_REDIS_PASSWORD", "")

        if redis_password:
            redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}"
        else:
            redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
    key_prefix = env_str(
        "RT_KEY_PREFIX",
        str(get_path(app, "globals.state.namespace", "rt")),
    )

    default_logon = str(dx.get("defaultLogonName") or dx.get("logonName") or "N0CALL")

    nodes: List[DxNode] = []
    for raw in dx.get("nodes", []) or []:
        if not isinstance(raw, dict):
            continue

        node_id = str(raw.get("id") or raw.get("name") or "").strip()
        addr = str(raw.get("telnetAddress") or "").strip()
        if not node_id or not addr:
            continue

        nodes.append(
            DxNode(
                id=node_id,
                name=str(raw.get("name") or node_id),
                location=str(raw.get("location") or ""),
                telnet_address=addr,
                telnet_port=int(raw.get("telnetPort") or 7300),
                logon_name=str(raw.get("logonName") or default_logon),
                set_name_command=str(raw.get("setNameCommand") or ""),
                set_location_command=str(raw.get("setLocationCommand") or ""),
            )
        )

    return Config(
        app_json=app_json,
        redis_url=redis_url,
        key_prefix=key_prefix,
        poll_interval_sec=env_int(
            "RT_HF_DXSPIDER_POLL_SEC",
            int(dx.get("pollIntervalSec") or 600),
        ),
        spots_per_node=env_int(
            "RT_HF_DXSPIDER_SPOTS_PER_NODE",
            int(dx.get("spotsPerNode") or 40),
        ),
        connect_timeout_sec=env_int(
            "RT_HF_DXSPIDER_CONNECT_TIMEOUT_SEC",
            int(dx.get("connectTimeoutSec") or 10),
        ),
        read_timeout_sec=env_int(
            "RT_HF_DXSPIDER_READ_TIMEOUT_SEC",
            int(dx.get("readTimeoutSec") or 15),
        ),
        ssb_segments=dx.get("ssbBandSegmentsHz") or {},
        nodes=nodes,
        debug_samples=env_str("RT_HF_DXSPIDER_DEBUG_SAMPLES", "0") == "1",
    )


def load_config() -> Config:
    app_json = env_str("RT_APP_JSON", APP_JSON_DEFAULT)
    app = load_json_file(app_json)
    return normalize_dxspider_config(app, app_json)


def redis_client(cfg: Config) -> redis.Redis:
    return redis.Redis.from_url(
        cfg.redis_url,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
        health_check_interval=15,
    )


def load_json_key(r: redis.Redis, key: str, default: Any) -> Any:
    raw = r.get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def publish_system_event(r: redis.Redis, prefix: str, event: str, payload: Dict[str, Any]) -> None:
    body = {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "event": event,
        "ts": utc_now_ms(),
        **payload,
    }
    try:
        r.publish(f"{prefix}:system:bus", compact_json(body))
    except Exception:
        pass


def connect_and_read_node(node: DxNode, cfg: Config) -> List[str]:
    """
    Pull raw DX Spider lines.

    Intentionally simple:
    - connect
    - send logon
    - optional set/name and set/location commands
    - send "sh/dx <n>"
    - collect text until timeout
    """
    with socket.create_connection(
        (node.telnet_address, node.telnet_port),
        timeout=cfg.connect_timeout_sec,
    ) as s:
        s.settimeout(cfg.read_timeout_sec)

        def send(cmd: str) -> None:
            if cmd:
                s.sendall((cmd.strip() + "\n").encode("utf-8", errors="ignore"))
                time.sleep(0.35)

        # Many clusters prompt for callsign. Sending logon immediately is harmless
        # for most DX Spider nodes.
        send(node.logon_name)
        send(node.set_name_command)
        send(node.set_location_command)
        send(f"sh/dx {cfg.spots_per_node}")

        buf = b""
        deadline = time.time() + cfg.read_timeout_sec

        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 128000:
                    break
            except socket.timeout:
                break

        text = buf.decode("utf-8", errors="ignore")
        return [clean_dx_text(line) for line in text.splitlines() if clean_dx_text(line)]


def freq_to_band_mode(freq_hz: int, cfg: Config) -> Tuple[Optional[str], Optional[str]]:
    for band, segments in cfg.ssb_segments.items():
        if not isinstance(segments, list):
            continue

        for seg in segments:
            try:
                low = int(seg.get("low"))
                high = int(seg.get("high"))
            except Exception:
                continue

            if low <= freq_hz <= high:
                return str(band), str(seg.get("mode") or "SSB")

    return None, None


def reject_non_phone_comment(comment: str) -> bool:
    """
    Keep broad and conservative.

    Reject obvious CW/digital/data spots. Blank comments are allowed because
    many phone spots have no mode text.
    """
    c = f" {clean_dx_text(comment).upper()} "

    reject_tokens = [
        " CW ",
        " CONTEST CW ",
        " RONDON TEST CW ",
        " FT8 ",
        " FT4 ",
        " TQSL/FT8 ",
        " TNX/FT8 ",
        " RTTY ",
        " PSK ",
        " JS8 ",
        " OLIVIA ",
        " MSK144 ",
        " JT65 ",
        " JT9 ",
        " VARA ",
        " SSTV ",
    ]

    return any(token in c for token in reject_tokens)


def parse_dx_line(line: str, node: DxNode, cfg: Config) -> Optional[Dict[str, Any]]:
    raw_line = clean_dx_text(line)
    if not raw_line:
        return None

    m = DX_LINE_RE.search(raw_line)
    line_format = "dx_de"

    if not m:
        m = DX_TABLE_LINE_RE.search(raw_line)
        line_format = "table"

    if not m:
        return None

    try:
        freq_khz = float(m.group("freq_khz"))
    except Exception:
        return None

    freq_hz = int(round(freq_khz * 1000))
    band, mode = freq_to_band_mode(freq_hz, cfg)
    if not band:
        return None

    callsign = str(m.group("callsign") or "").upper().strip()
    spotter = str(m.groupdict().get("spotter") or "").upper().strip()
    comment = clean_dx_text(str(m.groupdict().get("comment") or ""))

    if not callsign:
        return None

    if reject_non_phone_comment(comment):
        return None

    # Dedup key: same station on same approximate frequency and band.
    # Rounding to 1 kHz avoids duplicate decimal formatting.
    rounded_freq_hz = int(round(freq_hz / 1000.0) * 1000)
    spot_id = f"{band}:{callsign}:{rounded_freq_hz}"

    return {
        "id": spot_id,
        "callsign": callsign,
        "call": callsign,
        "freq_hz": freq_hz,
        "freq": f"{freq_hz / 1000000:.3f}",
        "band": band,
        "mode": mode,
        "spotter": spotter,
        "comment": comment,
        "source": node.id,
        "source_name": node.name,
        "source_location": node.location,
        "raw": raw_line,
        "line_format": line_format,
        "spotted_utc": utc_now_iso(),
        "sort_hz": freq_hz,
    }


def dedupe_spots(spots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}

    for spot in spots:
        key = str(spot.get("id") or "")
        if not key:
            continue

        existing = best.get(key)
        if existing is None:
            new_spot = dict(spot)
            src = spot.get("source")
            new_spot["sources"] = [src] if src else []
            best[key] = new_spot
            continue

        sources = existing.setdefault("sources", [])
        src = spot.get("source")
        if src and src not in sources:
            sources.append(src)

        # Keep richer comment if the old one is blank.
        if not existing.get("comment") and spot.get("comment"):
            existing["comment"] = spot.get("comment")

    out = list(best.values())

    out.sort(
        key=lambda s: (
            DEFAULT_BAND_ORDER.index(str(s.get("band")))
            if str(s.get("band")) in DEFAULT_BAND_ORDER
            else 999,
            int(s.get("sort_hz") or 0),
            str(s.get("callsign") or ""),
        )
    )

    return out


def group_by_band(spots: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for spot in spots:
        band = str(spot.get("band") or "")
        if not band:
            continue
        grouped.setdefault(band, []).append(spot)

    for band in grouped:
        grouped[band].sort(
            key=lambda s: (
                int(s.get("sort_hz") or 0),
                str(s.get("callsign") or ""),
            )
        )

    return grouped


def is_hf_spots_browse_active(r: redis.Redis, prefix: str) -> bool:
    """
    Controller-side preservation check.

    This is intentionally tolerant because interaction-state shape has evolved.
    """
    state = load_json_key(r, f"{prefix}:interaction:state", {})
    if not isinstance(state, dict):
        return False

    state_text = stable_compact_json(state)
    focus_panel = str(
        state.get("focus_panel")
        or state.get("panel")
        or state.get("focused_panel")
        or ""
    )
    browse_panel = str(state.get("browse_panel") or "")
    mode = str(state.get("mode") or state.get("state") or "")

    if focus_panel == "hf_spots_summary":
        return True
    if browse_panel == "hf_spots_summary":
        return True
    if "browse" in mode.lower() and "hf_spots_summary" in state_text:
        return True
    if '"hf_spots_summary"' in state_text and '"active":true' in state_text:
        return True

    return False


def preserve_selected_spot_if_needed(
    r: redis.Redis,
    prefix: str,
    selected_band: str,
    new_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    If operator is focused/browsing hf_spots_summary, do not yank the current
    selected spot out from under them. If the selected spot disappeared from
    refreshed data, add the old selected detail back into the selected band list.
    """
    if not is_hf_spots_browse_active(r, prefix):
        return new_items

    context = load_json_key(r, f"{prefix}:hf:context", {})
    if not isinstance(context, dict):
        return new_items

    selected_spot_id = str(context.get("selected_spot_id") or "").strip()
    if not selected_spot_id:
        return new_items

    if any(str(item.get("id") or "") == selected_spot_id for item in new_items):
        return new_items

    old_detail = load_json_key(r, f"{prefix}:hf:spots:selected_detail", {})
    if not isinstance(old_detail, dict):
        return new_items

    old_id = str(old_detail.get("id") or "").strip()
    old_band = str(old_detail.get("band") or "").strip()

    if old_id != selected_spot_id or old_band != selected_band:
        return new_items

    preserved = dict(old_detail)
    preserved["preserved"] = True
    preserved["stale"] = True
    preserved["status"] = str(preserved.get("status") or "preserved")
    preserved["row_style"] = str(preserved.get("row_style") or "")

    return [preserved] + new_items


def build_bands_model(
    grouped: Dict[str, List[Dict[str, Any]]],
    selected_band: str,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []

    for band in DEFAULT_BAND_ORDER:
        count = len(grouped.get(band, []))
        if count <= 0:
            continue

        items.append(
            {
                "id": band,
                "band": band,
                "label": band,
                "count": count,
                "selected": band == selected_band,
            }
        )

    return {
        "items": items,
        "bands": items,
        "selected_id": selected_band,
        "updated_at_ms": utc_now_ms(),
    }


def pick_selected_band(
    context: Dict[str, Any],
    grouped: Dict[str, List[Dict[str, Any]]],
) -> str:
    current = str(context.get("selected_band") or "").strip()
    if current and grouped.get(current):
        return current

    for band in DEFAULT_BAND_ORDER:
        if grouped.get(band):
            return band

    return current or ""


def pick_selected_spot(
    context: Dict[str, Any],
    selected_band: str,
    items: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    current_id = str(context.get("selected_spot_id") or "").strip()

    for item in items:
        if current_id and str(item.get("id") or "") == current_id:
            return current_id, item

    if items:
        first = items[0]
        return str(first.get("id") or ""), first

    return "", {}


def make_band_spots_model(
    band: str,
    items: List[Dict[str, Any]],
    selected_id: str = "",
) -> Dict[str, Any]:
    clean_items: List[Dict[str, Any]] = []

    for item in items:
        row = dict(item)
        row["band"] = band
        row.setdefault("status", "")
        row.setdefault("row_style", "default")
        clean_items.append(row)

    if not selected_id and clean_items:
        selected_id = str(clean_items[0].get("id") or "")

    return {
        "source": "dxspider",
        "band": band,
        "items": clean_items,
        "spots": clean_items,
        "selected_id": selected_id,
        "selected_band": band,
        "count": len(clean_items),
        "updated_at_ms": utc_now_ms(),
    }


def write_models(
    r: redis.Redis,
    cfg: Config,
    grouped: Dict[str, List[Dict[str, Any]]],
) -> None:
    prefix = cfg.key_prefix

    context = load_json_key(r, f"{prefix}:hf:context", {})
    if not isinstance(context, dict):
        context = {}

    selected_band = pick_selected_band(context, grouped)
    selected_items = grouped.get(selected_band, []) if selected_band else []

    # Only preserve a selected row if it belongs to the same selected band.
    # This prevents an old 40m row from being inserted into the 20m model.
    selected_items = preserve_selected_spot_if_needed(
        r,
        prefix,
        selected_band,
        selected_items,
    )

    selected_spot_id, selected_detail = pick_selected_spot(
        context,
        selected_band,
        selected_items,
    )

    bands_model = build_bands_model(grouped, selected_band)

    selected_model = make_band_spots_model(
        selected_band,
        selected_items,
        selected_spot_id,
    )

    new_context = dict(context)
    new_context.update(
        {
            "source": "dxspider",
            "mock": False,
            "selected_band": selected_band,
            "selected_spot_id": selected_spot_id,
            "selection_ts": int(new_context.get("selection_ts") or utc_now_ms()),
            "updated_at_ms": utc_now_ms(),
        }
    )

    pipe = r.pipeline()

    # Band summary model.
    pipe.set(f"{prefix}:hf:bands", compact_json(bands_model))

    # Per-band spot models used by hf.select_band.
    live_bands = set(grouped.keys())
    for band in DEFAULT_BAND_ORDER:
        key = f"{prefix}:hf:spots:{band}"
        items = grouped.get(band, [])

        if items:
            band_model = make_band_spots_model(band, items)
            pipe.set(key, compact_json(band_model))
        else:
            # Remove old/mock/stale per-band keys if the current poll has no
            # live spots for that band.
            pipe.delete(key)

    # Currently selected band model used by renderers/projector.
    pipe.set(f"{prefix}:hf:spots:selected", compact_json(selected_model))
    pipe.set(f"{prefix}:hf:spots:selected_detail", compact_json(selected_detail or {}))
    pipe.set(f"{prefix}:hf:context", compact_json(new_context))

    pipe.execute()


def poll_node(
    node: DxNode,
    cfg: Config,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Poll one DX Spider node.

    Runs in a worker thread.
    Does not write Redis.
    """
    started_ms = utc_now_ms()

    try:
        lines = connect_and_read_node(node, cfg)

        parsed: List[Dict[str, Any]] = []
        rejected_or_unparsed = 0

        for line in lines:
            spot = parse_dx_line(line, node, cfg)
            if spot:
                parsed.append(spot)
            else:
                rejected_or_unparsed += 1

        elapsed_ms = utc_now_ms() - started_ms

        result: Dict[str, Any] = {
            "id": node.id,
            "name": node.name,
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "raw_lines": len(lines),
            "parsed_spots": len(parsed),
            "rejected_or_unparsed": rejected_or_unparsed,
        }

        if cfg.debug_samples:
            dx_like_lines = [
                line for line in lines
                if "DX de" in line or "dx de" in line.lower()
            ]
            table_like_lines = [
                line for line in lines
                if DX_TABLE_LINE_RE.search(line)
            ]
            result["sample_lines"] = lines[:20]
            result["dx_like_lines"] = dx_like_lines[:20]
            result["table_like_lines"] = table_like_lines[:20]

        return parsed, result

    except Exception as exc:
        elapsed_ms = utc_now_ms() - started_ms
        return [], {
            "id": node.id,
            "name": node.name,
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }


def poll_once(r: redis.Redis, cfg: Config) -> None:
    """
    Poll all configured DX Spider nodes concurrently.

    Redis is written once after all node workers finish so the HF page sees one
    coherent model refresh.
    """
    poll_started_ms = utc_now_ms()

    all_spots: List[Dict[str, Any]] = []
    node_results: List[Dict[str, Any]] = []

    max_workers = max(1, len(cfg.nodes))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(poll_node, node, cfg): node
            for node in cfg.nodes
        }

        for future in as_completed(future_map):
            parsed, result = future.result()
            all_spots.extend(parsed)
            node_results.append(result)

    # Keep result order stable for logs and jq comparisons.
    node_order = {node.id: i for i, node in enumerate(cfg.nodes)}
    node_results.sort(
        key=lambda item: node_order.get(str(item.get("id") or ""), 999)
    )

    deduped = dedupe_spots(all_spots)
    grouped = group_by_band(deduped)
    write_models(r, cfg, grouped)

    elapsed_ms = utc_now_ms() - poll_started_ms
    prefix = cfg.key_prefix

    last_poll = {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "updated_at_ms": utc_now_ms(),
        "updated_at_utc": utc_now_iso(),
        "elapsed_ms": elapsed_ms,
        "nodes": node_results,
        "total_spots": len(all_spots),
        "deduped_spots": len(deduped),
        "bands": {band: len(items) for band, items in grouped.items()},
    }

    r.set(f"{prefix}:hf:dxspider:last_poll", compact_json(last_poll))

    publish_system_event(
        r,
        prefix,
        "hf_dxspider_poll_complete",
        {
            "elapsed_ms": elapsed_ms,
            "nodes": node_results,
            "total_spots": len(all_spots),
            "deduped_spots": len(deduped),
            "bands": {band: len(items) for band, items in grouped.items()},
        },
    )


RUNNING = True


def handle_signal(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    cfg = load_config()

    if not cfg.nodes:
        print(
            f"{SERVICE_NAME}: no DX Spider nodes configured in globals.dxSpider.nodes",
            file=sys.stderr,
        )
        return 2

    r = redis_client(cfg)
    r.ping()

    print(
        compact_json(
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "event": "started",
                "nodes": len(cfg.nodes),
                "poll_interval_sec": cfg.poll_interval_sec,
                "ts": utc_now_ms(),
            }
        ),
        flush=True,
    )

    oneshot = os.getenv("RT_HF_DXSPIDER_ONESHOT", "0") == "1"

    while RUNNING:
        try:
            poll_once(r, cfg)
        except Exception as exc:
            err = {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "updated_at_ms": utc_now_ms(),
                "updated_at_utc": utc_now_iso(),
                "error": str(exc),
            }

            try:
                r.set(f"{cfg.key_prefix}:hf:dxspider:last_error", compact_json(err))
            except Exception:
                pass

            print(
                compact_json(
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "event": "poll_error",
                        "error": str(exc),
                        "ts": utc_now_ms(),
                    }
                ),
                flush=True,
            )

        if oneshot:
            break

        for _ in range(max(1, cfg.poll_interval_sec)):
            if not RUNNING:
                break
            time.sleep(1)

    print(
        compact_json(
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "event": "stopped",
                "ts": utc_now_ms(),
            }
        ),
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())