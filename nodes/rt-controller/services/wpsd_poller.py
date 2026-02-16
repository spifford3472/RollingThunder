#!/usr/bin/env python3
from __future__ import annotations

import json, os, time, subprocess, re
from typing import Any, Dict, Optional, Tuple
from urllib.request import Request, urlopen

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST","127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT","6379"))
REDIS_DB   = int(os.environ.get("RT_REDIS_DB","0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None
REDIS_TIMEOUT = float(os.environ.get("RT_REDIS_TIMEOUT_SEC","0.35"))

UI_BUS_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL","rt:ui:bus")

WPSD_BASE = os.environ.get("RT_WPSD_BASE_URL","http://192.168.8.184").rstrip("/")
CALLER_URL = WPSD_BASE + "/mmdvmhost/caller_details_table.php"

WPSD_SSH_HOST = os.environ.get("RT_WPSD_SSH_HOST","rt-wpsd.local")
WPSD_SSH_USER = os.environ.get("RT_WPSD_SSH_USER","spiff")
WPSD_SSH_OPTS = os.environ.get("RT_WPSD_SSH_OPTS","-o BatchMode=yes -o ConnectTimeout=2 -o StrictHostKeyChecking=accept-new")

POLL_RF_SEC = float(os.environ.get("RT_WPSD_POLL_RF_SEC","1.5"))
POLL_CFG_SEC = float(os.environ.get("RT_WPSD_POLL_CFG_SEC","60"))

KEY_SNAPSHOT = "rt:wpsd:snapshot"
KEY_LASTCALL = "rt:wpsd:rf:last_call"

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KV_RE = re.compile(r"^\s*([^=]+?)\s*=\s*(.*?)\s*$")

def now_ms() -> int:
    return int(time.time() * 1000)

def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v

def publish_changed(r: redis.Redis, keys: list[str], source: str) -> None:
    evt = {"topic":"state.changed","payload":{"keys":keys},"ts_ms":now_ms(),"source":source}
    try:
        r.publish(UI_BUS_CHANNEL, json.dumps(evt, separators=(",",":")))
    except Exception:
        pass

def ssh_cat(path: str) -> Optional[str]:
    cmd = ["ssh"] + WPSD_SSH_OPTS.split() + [f"{WPSD_SSH_USER}@{WPSD_SSH_HOST}", "cat", path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=3.0)
        return out
    except Exception:
        return None

def http_get(url: str) -> Optional[str]:
    try:
        req = Request(url, headers={"User-Agent":"RollingThunder/1.0"})
        with urlopen(req, timeout=2.0) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

# TODO: implement these parsers deterministically
def parse_caller_details_html(html: str) -> Optional[Dict[str, Any]]: ...

def parse_wpsd_release(txt: str) -> Dict[str, Any]:
    """
    Input: /etc/WPSD-release (INI-ish key = value lines)
    Output: dict with at least wpsd_version and a few useful stable fields.
    """
    out: Dict[str, Any] = {}
    if not txt:
        return out

    for line in txt.splitlines():
        m = _KV_RE.match(line)
        if not m:
            continue
        k = m.group(1).strip()
        v = _strip_quotes(m.group(2).strip())
        out[k] = v

    # Normalize the key we care about:
    if "WPSD_Ver" in out:
        out["wpsd_version"] = out["WPSD_Ver"]

    return out

def parse_dmrgateway(txt: str) -> Dict[str, Any]:
    """
    Input: /etc/dmrgateway (INI-like)
    Output:
      {
        "rf": { "rx_freq_hz": int|None, "tx_freq_hz": int|None },
        "dmr_networks": [ {id, enabled, address, port, name} ... ]
      }
    """
    rf = {"rx_freq_hz": None, "tx_freq_hz": None}
    networks: List[Dict[str, Any]] = []

    if not txt:
        return {"rf": rf, "dmr_networks": networks}

    current_section: Optional[str] = None
    sections: Dict[str, Dict[str, str]] = {}

    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        sm = _SECTION_RE.match(line)
        if sm:
            current_section = sm.group(1).strip()
            sections.setdefault(current_section, {})
            continue

        km = _KV_RE.match(line)
        if km and current_section:
            k = km.group(1).strip()
            v = _strip_quotes(km.group(2).strip())
            sections[current_section][k] = v

    # RF from [Info]
    info = sections.get("Info", {})
    try:
        if "RXFrequency" in info:
            rf["rx_freq_hz"] = int(info["RXFrequency"])
        if "TXFrequency" in info:
            rf["tx_freq_hz"] = int(info["TXFrequency"])
    except Exception:
        # leave as None if parse fails
        pass

    # Networks: any section named "DMR Network N"
    for sec_name, kv in sections.items():
        if not sec_name.startswith("DMR Network "):
            continue
        # Extract numeric ID if possible
        try:
            nid = int(sec_name.split("DMR Network ", 1)[1].strip())
        except Exception:
            nid = None

        enabled = (kv.get("Enabled", "0").strip() == "1")
        addr = kv.get("Address")
        port = None
        try:
            if "Port" in kv:
                port = int(kv["Port"])
        except Exception:
            port = None

        name = kv.get("Name")

        networks.append(
            {
                "id": nid,
                "enabled": enabled,
                "address": addr,
                "port": port,
                "name": name,
            }
        )

    # stable ordering
    networks.sort(key=lambda x: (x["id"] is None, x["id"]))

    return {"rf": rf, "dmr_networks": networks}

def parse_bm_config(txt: str) -> List[Dict[str, Any]]:
    """
    Input: /etc/wpsd-bm-config.json
    Output: [ {tg:int, slot:int} ... ] sorted by slot, then tg
    """
    if not txt:
        return []

    try:
        obj = json.loads(txt)
    except Exception:
        return []

    fav = obj.get("favTGs")
    if not isinstance(fav, dict):
        return []

    out: List[Dict[str, Any]] = []
    for tg_str, meta in fav.items():
        try:
            tg = int(str(tg_str))
        except Exception:
            continue
        slot = None
        if isinstance(meta, dict):
            try:
                slot = int(meta.get("slot"))
            except Exception:
                slot = None
        out.append({"tg": tg, "slot": slot})

    out.sort(key=lambda x: (x["slot"] is None, x["slot"], x["tg"]))
    return out

def build_wpsd_snapshot(rel_txt: Optional[str], dmr_txt: Optional[str], bm_txt: Optional[str]) -> Dict[str, Any]:
    rel = parse_wpsd_release(rel_txt or "")
    dmr = parse_dmrgateway(dmr_txt or "")
    bm  = parse_bm_config(bm_txt or "")

    snapshot: Dict[str, Any] = {
        "schema_version": "rt.wpsd.snapshot.v1",
        "node_id": "rt-wpsd",
        "status": "stale",
        "last_update_ms": now_ms(),

        "wpsd_version": rel.get("wpsd_version"),

        "rx_freq_hz": dmr.get("rf", {}).get("rx_freq_hz"),
        "tx_freq_hz": dmr.get("rf", {}).get("tx_freq_hz"),

        "dmr_networks": [],
        "bm_mapped_talkgroups": bm,

        "source": { "config": "ssh" }
    }

    # Only include enabled networks, and only safe fields
    nets = dmr.get("dmr_networks") or []
    for n in nets:
        if n.get("enabled") is True:
            snapshot["dmr_networks"].append(
                {
                    "id": n.get("id"),
                    "address": n.get("address"),
                    "port": n.get("port"),
                    "name": n.get("name"),
                    "enabled": True,
                }
            )

    # If we got config data, consider it online-ish
    if snapshot.get("wpsd_version") or snapshot.get("rx_freq_hz") or snapshot["dmr_networks"]:
        snapshot["status"] = "online"

    return snapshot


def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD,
        decode_responses=True, socket_timeout=REDIS_TIMEOUT, socket_connect_timeout=REDIS_TIMEOUT
    )

    last_cfg = 0.0

    while True:
        t0 = time.time()

        # 1) slow config refresh
        if (t0 - last_cfg) >= POLL_CFG_SEC:
            rel = ssh_cat("/etc/WPSD-release")
            dmr = ssh_cat("/etc/dmrgateway")
            bm  = ssh_cat("/etc/wpsd-bm-config.json")

            snapshot = build_wpsd_snapshot(rel, dmr, bm)


            if dmr:
                d = parse_dmrgateway(dmr)
                snapshot["rf"] = d.get("rf", {})
                snapshot["dmr_networks"] = d.get("dmr_networks", [])

            if bm:
                snapshot["bm_mapped_talkgroups"] = parse_bm_config(bm)

            # if we got anything meaningful, we can call it online-ish
            if snapshot.get("wpsd_version") or snapshot["rf"] or snapshot["dmr_networks"]:
                snapshot["status"] = "online"

            r.set(KEY_SNAPSHOT, json.dumps(snapshot, separators=(",",":")))
            publish_changed(r, [KEY_SNAPSHOT], "wpsd_poller")
            last_cfg = t0

        # 2) fast RF refresh
        html = http_get(CALLER_URL)
        if html:
            parsed = parse_caller_details_html(html)
            if parsed:
                r.set(KEY_LASTCALL, json.dumps(parsed, separators=(",",":")))
                publish_changed(r, [KEY_LASTCALL], "wpsd_poller")

        # sleep bounded
        elapsed = time.time() - t0
        time.sleep(max(0.2, POLL_RF_SEC - elapsed))

if __name__ == "__main__":
    main()
