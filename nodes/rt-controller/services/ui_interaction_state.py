#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import redis

REDIS_HOST = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("RT_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("RT_REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("RT_REDIS_PASSWORD") or None

INTENTS_CH = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")

CONFIG_PAGES_DIR = Path(
    os.environ.get("RT_PAGES_PATH", "/opt/rollingthunder/config/pages")
)

INTERACTION_KEY = "rt:interaction:state"
WRITER_LOCK_KEY = "rt:interaction:writer"

NODE_ID = os.environ.get("RT_NODE_ID", "rt-controller")


def now_ms() -> int:
    return int(time.time() * 1000)


def redis_client() -> redis.Redis:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    r.ping()
    return r


def load_pages() -> List[Dict[str, Any]]:
    pages = []
    for f in CONFIG_PAGES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            pages.append(data)
        except Exception:
            continue

    pages.sort(key=lambda p: int(p.get("order", 9999)))
    return pages


def build_page_index(pages):
    return {p["id"]: p for p in pages}


def default_state(pages):
    if not pages:
        return None

    first = pages[0]
    focus = first.get("focusPolicy", {}).get("defaultPanel")

    return {
        "page": first["id"],
        "focus": focus,
        "modal": None,
        "browse": None,
        "authority": {
            "degraded": False,
            "stale": False,
            "reason": None,
        },
        "updated_at_ms": now_ms(),
    }


def acquire_lock(r: redis.Redis):
    ok = r.set(WRITER_LOCK_KEY, NODE_ID, nx=True)
    if not ok:
        raise RuntimeError("interaction state writer lock already held")


def save_state(r: redis.Redis, state: Dict[str, Any]):
    state["updated_at_ms"] = now_ms()
    r.set(INTERACTION_KEY, json.dumps(state, separators=(",", ":")))


def rotate(lst, current, direction):
    if current not in lst:
        return lst[0] if lst else None

    idx = lst.index(current)
    if direction == "next":
        idx = (idx + 1) % len(lst)
    else:
        idx = (idx - 1) % len(lst)
    return lst[idx]


def main():
    r = redis_client()
    acquire_lock(r)

    pages = load_pages()
    page_index = build_page_index(pages)

    state = default_state(pages)
    if not state:
        raise RuntimeError("no pages loaded")

    save_state(r, state)

    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(INTENTS_CH)

    while True:
        msg = ps.get_message(timeout=1.0)
        if not msg:
            time.sleep(0.05)
            continue

        try:
            obj = json.loads(msg["data"])
        except Exception:
            continue

        intent = obj.get("intent")
        params = obj.get("params") or {}

        current_page = page_index.get(state["page"])
        allowed = current_page.get("controls", {}).get("allowedIntents", [])

        if intent not in allowed:
            continue

        if intent == "ui.page.next":
            ids = [p["id"] for p in pages]
            next_page = rotate(ids, state["page"], "next")
            page = page_index[next_page]
            state["page"] = next_page
            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
            state["browse"] = None
            state["modal"] = None

        elif intent == "ui.page.prev":
            ids = [p["id"] for p in pages]
            prev_page = rotate(ids, state["page"], "prev")
            page = page_index[prev_page]
            state["page"] = prev_page
            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
            state["browse"] = None
            state["modal"] = None

        elif intent == "ui.page.goto":
            target = params.get("page")
            if target in page_index:
                page = page_index[target]
                state["page"] = target
                state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                state["browse"] = None
                state["modal"] = None

        elif intent == "ui.focus.next":
            rotation = current_page.get("focusPolicy", {}).get("rotation", [])
            state["focus"] = rotate(rotation, state["focus"], "next")

        elif intent == "ui.focus.prev":
            rotation = current_page.get("focusPolicy", {}).get("rotation", [])
            state["focus"] = rotate(rotation, state["focus"], "prev")

        elif intent == "ui.focus.set":
            panel = params.get("panel")
            if panel in current_page.get("focusPolicy", {}).get("rotation", []):
                state["focus"] = panel

        save_state(r, state)


if __name__ == "__main__":
    main()