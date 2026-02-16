#!/usr/bin/env python3
"""
wpsd_log_ingestor.py — RollingThunder (rt-controller)

Purpose:
- Read-only ingest of WPSD / Pi-Star MMDVMHost logs (via SSH) for real RF truth
- Derive per-slot activity + recent TX ring for UI
- Publish changes to UI bus (Redis PubSub), bounded

Writes (Redis):
- rt:wpsd:rf:slots     (string JSON) schema rt.wpsd.rf.slots.v1
- rt:wpsd:rf:recent    (string JSON) schema rt.wpsd.rf.recent.v1

Publishes (Redis PubSub):
- rt:ui:bus message: {"topic":"state.changed","payload":{"keys":[...]}, ...}

Constraints:
- Read-only to WPSD appliance (no changes, only ssh + tail)
- Bounded memory and bounded Redis payload sizes
- Deterministic, restart-safe, tolerant of missing/partial lines

Log source:
- /var/log/pi-star/MMDVM-YYYY-MM-DD.log  (date is UTC)

Assumptions:
- SSH key auth is configured from rt-controller -> rt-wpsd
- Hostname resolves: rt-wpsd.local (override via env)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

import redis


# ---- Environment / config ----
REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC", "0.35"))

UI_BUS_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")

# WPSD SSH target
WPSD_HOST = os.environ.get("RT_WPSD_HOST", "rt-wpsd.local")
WPSD_USER = os.environ.get("RT_WPSD_USER", "pi-star")

# How many recent transmissions to keep
RECENT_MAX = int(os.environ.get("RT_WPSD_RECENT_MAX", "25"))

# Slot "active" TTL: if we saw a header but not an end, show active only briefly.
ACTIVE_TTL_MS = int(os.environ.get("RT_WPSD_ACTIVE_TTL_MS", "5000"))

# Poll/backoff loop behavior
RECONNECT_BACKOFF_MS = int(os.environ.get("RT_WPSD_RECONNECT_BACKOFF_MS", "1500"))
MAX_BACKOFF_MS = int(os.environ.get("RT_WPSD_MAX_BACKOFF_MS", "15000"))

# How often to re-evaluate UTC filename rollover even if tail is alive (ms)
ROLLOVER_CHECK_MS = int(os.environ.get("RT_WPSD_ROLLOVER_CHECK_MS", "20000"))

# Redis keys
KEY_SLOTS = os.environ.get("RT_KEY_WPSD_SLOTS", "rt:wpsd:rf:slots")
KEY_RECENT = os.environ.get("RT_KEY_WPSD_RECENT", "rt:wpsd:rf:recent")

LOCAL_NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")
# WPSD HTTP (for flag lookup)
WPSD_HTTP_BASE = os.environ.get("RT_WPSD_HTTP_BASE", "http://192.168.8.184")
WPSD_CALLER_DETAILS_PATH = os.environ.get("RT_WPSD_CALLER_DETAILS_PATH", "/mmdvmhost/caller_details_table.php")
WPSD_HTTP_TIMEOUT_SEC = float(os.environ.get("RT_WPSD_HTTP_TIMEOUT_SEC", "0.8"))

# Cache: callsign -> cc (country code) TTL
CALLSIGN_CC_TTL_SEC = int(os.environ.get("RT_WPSD_CC_TTL_SEC", "86400"))  # 24h default


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_ymd() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def build_log_path() -> str:
    # UTC-dated file
    return f"/var/log/pi-star/MMDVM-{utc_ymd()}.log"


def safe_json_dumps(obj: Any, max_bytes: int = 60_000) -> str:
    """
    Serialize to JSON and hard-cap size. If too large, truncate defensively.
    """
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        s = json.dumps({"_error": "json_serialize_failed"}, separators=(",", ":"))

    b = s.encode("utf-8", errors="replace")
    if len(b) <= max_bytes:
        return s

    # Truncate string, keep valid JSON wrapper
    preview = b[: min(len(b), 2000)].decode("utf-8", errors="replace")
    return json.dumps({"_truncated": True, "_preview": preview + "…"}, ensure_ascii=False, separators=(",", ":"))


# ---- Log parsing ----
# Examples you provided:
# M: 2026-02-16 04:40:44.411 DMR Slot 1, received network voice header from W5SWG to TG 3100
# M: 2026-02-16 04:40:49.001 DMR Slot 1, received network end of voice transmission from W5SWG to TG 3100, 4.8 seconds, 0% packet loss, BER: 0.0%
# M: 2026-02-16 04:40:47.063 DMR Slot 1, Talker Alias "W5SWG Scott"

_HEADER_RE = re.compile(
    r"DMR Slot (?P<slot>[12]), received (?P<src>network|RF) voice header from (?P<call>[A-Z0-9/]+) to TG (?P<tg>\d+)",
    re.IGNORECASE,
)
_ALIAS_RE = re.compile(
    r'DMR Slot (?P<slot>[12]), Talker Alias "(?P<alias>[^"]+)"',
    re.IGNORECASE,
)
_END_RE = re.compile(
    r"DMR Slot (?P<slot>[12]), received (?P<src>network|RF) end of voice transmission from (?P<call>[A-Z0-9/]+) to TG (?P<tg>\d+), (?P<dur>[0-9.]+) seconds, (?P<loss>\d+)% packet loss, BER:\s*(?P<ber>[0-9.]+)%",
    re.IGNORECASE,
)


def normalize_src(src: str) -> str:
    s = (src or "").strip().lower()
    # UI-friendly short values
    return "net" if s == "network" else ("rf" if s == "rf" else s)


@dataclass
class SlotState:
    active: bool = False
    since_ms: Optional[int] = None
    last_end_ms: Optional[int] = None

    direction: Optional[str] = None   # net|rf
    callsign: Optional[str] = None
    tg: Optional[int] = None
    dur_s: Optional[float] = None
    loss_pct: Optional[int] = None
    ber: Optional[float] = None
    alias: Optional[str] = None
    cc: Optional[str] = None   # e.g. "us", "jp", "au"


    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": bool(self.active),
            "since_ms": self.since_ms,
            "last_end_ms": self.last_end_ms,
            "direction": self.direction,
            "callsign": self.callsign,
            "tg": self.tg,
            "dur_s": self.dur_s,
            "loss_pct": self.loss_pct,
            "ber": self.ber,
            "alias": self.alias,
            "cc": self.cc,
        }


def parse_line(line: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Returns (kind, payload) where kind in {"header","alias","end","other"}.
    payload includes slot and extracted fields where applicable.
    """
    if not line:
        return "other", None

    m = _HEADER_RE.search(line)
    if m:
        slot = int(m.group("slot"))
        return "header", {
            "slot": slot,
            "src": normalize_src(m.group("src")),
            "callsign": m.group("call").upper(),
            "tg": int(m.group("tg")),
        }

    m = _ALIAS_RE.search(line)
    if m:
        slot = int(m.group("slot"))
        alias = m.group("alias").strip()
        return "alias", {"slot": slot, "alias": alias}

    m = _END_RE.search(line)
    if m:
        slot = int(m.group("slot"))
        return "end", {
            "slot": slot,
            "src": normalize_src(m.group("src")),
            "callsign": m.group("call").upper(),
            "tg": int(m.group("tg")),
            "dur_s": float(m.group("dur")),
            "loss_pct": int(m.group("loss")),
            "ber": float(m.group("ber")),
        }

    return "other", None


# ---- Redis IO ----
def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT,
        socket_connect_timeout=REDIS_TIMEOUT,
    )
    r.ping()
    return r


def publish_state_changed(r: redis.Redis, keys: List[str], source: str) -> None:
    evt = {
        "topic": "state.changed",
        "payload": {"keys": keys[:50]},  # bounded
        "ts_ms": now_ms(),
        "source": source,
    }
    try:
        r.publish(UI_BUS_CHANNEL, json.dumps(evt, separators=(",", ":")))
    except Exception:
        pass


def load_json(r: redis.Redis, key: str) -> Optional[Dict[str, Any]]:
    try:
        raw = r.get(key)
        if not raw:
            return None
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ---- Main ingestion loop ----
def start_tail_process(log_path: str) -> subprocess.Popen:
    """
    SSH tail -F the given log file on WPSD host.
    We rely on stdio streaming, line-buffered by Python.
    """
    # Use BatchMode so we fail fast if keys/auth are wrong.
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=3",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=2",
        f"{WPSD_USER}@{WPSD_HOST}",
        # tail:
        "tail", "-n", "0", "-F", log_path,
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line buffered
    )


def prune_active_ttl(slots: Dict[int, SlotState]) -> bool:
    """
    If a slot is 'active' but it's older than TTL, mark inactive.
    Returns True if any changes made.
    """
    changed = False
    now = now_ms()
    for s in slots.values():
        if s.active and s.since_ms is not None:
            if (now - s.since_ms) > ACTIVE_TTL_MS:
                s.active = False
                s.since_ms = None
                changed = True
    return changed


def build_slots_payload(slots: Dict[int, SlotState]) -> Dict[str, Any]:
    return {
        "schema_version": "rt.wpsd.rf.slots.v1",
        "node_id": "rt-wpsd",
        "last_update_ms": now_ms(),
        "slots": {
            "1": slots[1].to_dict(),
            "2": slots[2].to_dict(),
        },
    }


def build_recent_payload(recent: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": "rt.wpsd.rf.recent.v1",
        "node_id": "rt-wpsd",
        "last_update_ms": now_ms(),
        "items": recent[:RECENT_MAX],
    }

# --- callsign -> country code (cc) lookup via WPSD HTML (bounded, cached) ---

_FLAG_IMG_RE = re.compile(r"/images/flags/(?P<cc>[a-z0-9\-]+)\.png", re.IGNORECASE)
_CALLSIGN_IN_ROW_RE = re.compile(r"callsign=([A-Z0-9/]+)", re.IGNORECASE)

def _http_get_text(url: str, timeout_sec: float) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RollingThunder/rt-controller (flag-lookup)",
            "Accept": "text/html,*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        # WPSD is typically UTF-8 but be defensive.
        data = resp.read(200_000)  # hard cap read
        return data.decode("utf-8", errors="replace")

def fetch_callsign_cc_map() -> Dict[str, str]:
    """
    Pull caller_details_table.php and extract callsign -> cc (country code).
    We intentionally parse the *same* source that renders flags in WPSD.
    """
    url = WPSD_HTTP_BASE.rstrip("/") + WPSD_CALLER_DETAILS_PATH
    try:
        html = _http_get_text(url, timeout_sec=WPSD_HTTP_TIMEOUT_SEC)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}
    except Exception:
        return {}

    # Strategy:
    # - Split into coarse "rows" using <tr ...> boundaries.
    # - If a row contains callsign=FOO and /images/flags/xx.png then map FOO->xx.
    # This avoids trying to build a brittle full HTML parser.
    out: Dict[str, str] = {}

    # crude but effective row split
    parts = re.split(r"<tr\b", html, flags=re.IGNORECASE)
    for p in parts:
        mcall = _CALLSIGN_IN_ROW_RE.search(p)
        if not mcall:
            continue
        cs = mcall.group(1).strip().upper()
        if not cs:
            continue

        mflag = _FLAG_IMG_RE.search(p)
        if not mflag:
            continue

        cc = mflag.group("cc").strip().lower()
        if not cc:
            continue

        out[cs] = cc

    return out

def get_cc_for_callsign(
    callsign: Optional[str],
    cache: Dict[str, Dict[str, Any]],
    now_ms_fn=now_ms,
) -> Optional[str]:
    """
    Cache entries: cache[CALL] = {"cc": "us", "ts_ms": <when learned>}
    TTL is CALLSIGN_CC_TTL_SEC.
    """
    if not callsign:
        return None
    cs = str(callsign).strip().upper()
    if not cs:
        return None

    nowt = now_ms_fn()
    ent = cache.get(cs)
    if ent:
        ts = ent.get("ts_ms")
        if isinstance(ts, int) and (nowt - ts) <= (CALLSIGN_CC_TTL_SEC * 1000):
            cc = ent.get("cc")
            return cc if isinstance(cc, str) and cc else None

    # Miss/expired: refresh map once and update cache
    mp = fetch_callsign_cc_map()
    if mp:
        t = nowt
        for k, v in mp.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                cache[k.upper()] = {"cc": v.lower(), "ts_ms": t}

    ent2 = cache.get(cs)
    if ent2:
        cc = ent2.get("cc")
        return cc if isinstance(cc, str) and cc else None

    return None


def main() -> None:
    r = redis_client()

    # Local derived state
    slots: Dict[int, SlotState] = {1: SlotState(), 2: SlotState()}
    recent: List[Dict[str, Any]] = []
    # callsign -> cc cache (in-memory, bounded by TTL + natural churn)
    cc_cache: Dict[str, Dict[str, Any]] = {}

    # Try to load existing recent list (optional continuity)
    prev_recent = load_json(r, KEY_RECENT)
    if isinstance(prev_recent, dict) and isinstance(prev_recent.get("items"), list):
        recent = [x for x in prev_recent["items"] if isinstance(x, dict)][:RECENT_MAX]

    backoff = RECONNECT_BACKOFF_MS
    proc: Optional[subprocess.Popen] = None
    current_path = build_log_path()
    last_roll_check = 0

    # Initial publish so UI has something
    r.set(KEY_SLOTS, safe_json_dumps(build_slots_payload(slots)))
    r.set(KEY_RECENT, safe_json_dumps(build_recent_payload(recent)))
    publish_state_changed(r, [KEY_SLOTS, KEY_RECENT], source="wpsd_log_ingestor:init")

    while True:
        # roll check / ensure we are tailing today's UTC file
        nowt = now_ms()
        if (nowt - last_roll_check) >= ROLLOVER_CHECK_MS:
            desired = build_log_path()
            last_roll_check = nowt
            if desired != current_path:
                current_path = desired
                # force restart tail to new file
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                proc = None

        # Maintain TTL state even if no new lines arrive
        if prune_active_ttl(slots):
            r.set(KEY_SLOTS, safe_json_dumps(build_slots_payload(slots)))
            publish_state_changed(r, [KEY_SLOTS], source="wpsd_log_ingestor:ttl")

        if proc is None or proc.poll() is not None:
            try:
                proc = start_tail_process(current_path)
                backoff = RECONNECT_BACKOFF_MS
            except Exception:
                time.sleep(backoff / 1000.0)
                backoff = min(MAX_BACKOFF_MS, int(backoff * 1.7))
                continue

        assert proc.stdout is not None

        # Read one line (non-busy): if SSH dies, readline returns ""
        line = proc.stdout.readline()
        if line == "":
            # process likely ended; collect stderr for debugging (bounded)
            try:
                if proc.stderr:
                    _ = proc.stderr.read(512)
            except Exception:
                pass
            time.sleep(backoff / 1000.0)
            backoff = min(MAX_BACKOFF_MS, int(backoff * 1.7))
            proc = None
            continue

        line = line.strip()
        kind, payload = parse_line(line)
        if kind == "other" or not payload:
            continue

        slot = int(payload["slot"])
        s = slots.get(slot)
        if not s:
            continue

        changed_keys: List[str] = []
        nowt = now_ms()

        if kind == "header":
            # Mark active briefly; store basics
            s.active = True
            s.since_ms = nowt
            s.direction = payload.get("src")
            s.callsign = payload.get("callsign")
            s.cc = get_cc_for_callsign(s.callsign, cc_cache)

            s.tg = payload.get("tg")
            # don't reset alias/dur/ber/loss; those reflect last completed TX
            changed_keys.append(KEY_SLOTS)

        elif kind == "alias":
            # Associate alias with the slot; don't force active/inactive
            s.alias = payload.get("alias")
            changed_keys.append(KEY_SLOTS)

        elif kind == "end":
            # End of a TX: mark inactive and record metrics
            s.active = False
            s.since_ms = None
            s.last_end_ms = nowt
            s.direction = payload.get("src")
            s.callsign = payload.get("callsign")
            s.cc = get_cc_for_callsign(s.callsign, cc_cache)

            s.tg = payload.get("tg")
            s.dur_s = payload.get("dur_s")
            s.loss_pct = payload.get("loss_pct")
            s.ber = payload.get("ber")
            changed_keys.append(KEY_SLOTS)

            # Push into recent ring
            recent_item = {
                "ts_ms": nowt,
                "slot": slot,
                "direction": s.direction,
                "callsign": s.callsign,
                "cc": s.cc,
                "tg": s.tg,
                "dur_s": s.dur_s,
                "loss_pct": s.loss_pct,
                "ber": s.ber,
                "alias": s.alias,
            }

            recent.insert(0, recent_item)
            if len(recent) > RECENT_MAX:
                recent = recent[:RECENT_MAX]
            changed_keys.append(KEY_RECENT)

        # Write changed keys (bounded JSON)
        if changed_keys:
            if KEY_SLOTS in changed_keys:
                r.set(KEY_SLOTS, safe_json_dumps(build_slots_payload(slots)))
            if KEY_RECENT in changed_keys:
                r.set(KEY_RECENT, safe_json_dumps(build_recent_payload(recent)))

            publish_state_changed(r, list(dict.fromkeys(changed_keys)), source="wpsd_log_ingestor")


if __name__ == "__main__":
    main()
