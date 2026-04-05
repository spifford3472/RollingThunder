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
INTERACTION_HEARTBEAT_MS = int(os.environ.get("RT_UI_INTERACTION_HEARTBEAT_MS", "1000"))
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


def acquire_lock(r):
    while True:
        ok = r.set(WRITER_LOCK_KEY, NODE_ID, nx=True, px=10000)
        if ok:
            return

        # Optional: log once every few seconds if you want
        time.sleep(1)


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
    last_persist_ms = 0
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
        state_changed = False

        if msg:
            try:
                obj = json.loads(msg["data"])
            except Exception:
                obj = None

            if obj:
                intent = obj.get("intent")
                params = obj.get("params") or {}

                current_page = page_index.get(state["page"])
                allowed = current_page.get("controls", {}).get("allowedIntents", [])

                if intent in allowed:
                    if intent == "ui.page.next":
                        ids = [p["id"] for p in pages]
                        next_page = rotate(ids, state["page"], "next")
                        page = page_index[next_page]
                        state["page"] = next_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True

                    elif intent == "ui.page.prev":
                        ids = [p["id"] for p in pages]
                        prev_page = rotate(ids, state["page"], "prev")
                        page = page_index[prev_page]
                        state["page"] = prev_page
                        state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                        state["browse"] = None
                        state["modal"] = None
                        state_changed = True

                    elif intent == "ui.page.goto":
                        target = params.get("page")
                        if target in page_index:
                            page = page_index[target]
                            state["page"] = target
                            state["focus"] = page.get("focusPolicy", {}).get("defaultPanel")
                            state["browse"] = None
                            state["modal"] = None
                            state_changed = True

                    elif intent == "ui.focus.next":
                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "next")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True

                    elif intent == "ui.focus.prev":
                        rotation = current_page.get("focusPolicy", {}).get("rotation", [])
                        new_focus = rotate(rotation, state["focus"], "prev")
                        if new_focus != state["focus"]:
                            state["focus"] = new_focus
                            state_changed = True

                    elif intent == "ui.focus.set":
                        panel = params.get("panel")
                        if panel in current_page.get("focusPolicy", {}).get("rotation", []):
                            if panel != state["focus"]:
                                state["focus"] = panel
                                state["browse"] = None
                                state_changed = True

                    elif intent == "ui.browse.delta":
                        if state.get("focus"):
                            browse = state.get("browse")
                            delta = 0
                            try:
                                delta = int(params.get("delta", 0))
                            except Exception:
                                delta = 0

                            if not isinstance(browse, dict) or browse.get("panel") != state["focus"]:
                                state["browse"] = {
                                    "active": True,
                                    "page": state["page"],
                                    "panel": state["focus"],
                                    "selected_index": max(0, delta),
                                }
                                state_changed = True
                            else:
                                current_index = 0
                                try:
                                    current_index = int(browse.get("selected_index", 0))
                                except Exception:
                                    current_index = 0

                                new_index = max(0, current_index + delta)
                                if new_index != current_index:
                                    browse["selected_index"] = new_index
                                    browse["active"] = True
                                    browse["page"] = state["page"]
                                    browse["panel"] = state["focus"]
                                    state["browse"] = browse
                                    state_changed = True

        now = now_ms()

        if state_changed or (now - last_persist_ms) >= INTERACTION_HEARTBEAT_MS:
            save_state(r, state)
            last_persist_ms = now

        time.sleep(0.05)

if __name__ == "__main__":
    main()