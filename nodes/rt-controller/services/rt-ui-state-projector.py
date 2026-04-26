#!/usr/bin/env python3
"""RollingThunder UI state projection service.

Authoritative controller-owned projector for `rt:ui:*` keys.

Primary upstream source:
- RT_UI_INTERACTION_STATE_KEY (default: rt:interaction:state)

Optional page-family source:
- RT_UI_PAGE_CONTEXT_KEY (default: rt:pota:context)

Fallback behavior:
- If the primary interaction-state key is absent, the projector may fall back to
  older split candidate keys.
- Missing or ambiguous state fails closed:
  affected rt:ui:* keys are deleted and rt:ui:authority is degraded.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import redis
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

POTA_SPOT_STATUS_KEY_PREFIX = "rt:pota:spot_status:"
UI_INTERACTION_STATE_KEY = os.environ.get("RT_UI_INTERACTION_STATE_KEY", "rt:interaction:state")
UI_PAGE_CONTEXT_KEY = os.environ.get("RT_UI_PAGE_CONTEXT_KEY", "rt:pota:context")

DEFAULT_SNAPSHOT_KEYS = [
    "rt:controller:ui_state",
    "rt:controller:interaction_state",
    "rt:controller:runtime_state",
    "rt:controller:state",
]
DEFAULT_PAGE_KEYS = [
    "rt:controller:page",
    "rt:controller:page:current",
    "rt:page:current",
]
DEFAULT_FOCUS_KEYS = [
    "rt:controller:focus",
    "rt:controller:focus:current",
    "rt:focus:current",
]
DEFAULT_MODAL_KEYS = [
    "rt:controller:modal",
    "rt:controller:ui:modal",
]
DEFAULT_BROWSE_KEYS = [
    "rt:controller:browse",
    "rt:controller:ui:browse",
]
DEFAULT_AUTHORITY_KEYS = [
    "rt:controller:authority",
    "rt:controller:ui:authority",
]
DEFAULT_RESULT_KEYS = [
    "rt:controller:last_result",
    "rt:controller:ui:last_result",
]
DEFAULT_PAGE_CONTEXT_KEYS = [
    "rt:controller:page_context",
    "rt:controller:ui:page_context",
]
DEFAULT_SYSTEM_HEALTH_KEYS = [
    "rt:system:health",
]

PROJECTED_KEYS = {
    "page": "rt:ui:page",
    "focus": "rt:ui:focus",
    "layer": "rt:ui:layer",
    "modal": "rt:ui:modal",
    "browse": "rt:ui:browse",
    "authority": "rt:ui:authority",
    "last_result": "rt:ui:last_result",
    "page_context": "rt:ui:page_context",
    "led_snapshot": "rt:ui:led_snapshot",
}

UI_BUS_CHANNEL = os.environ.get("RT_UI_BUS_CHANNEL", "rt:ui:bus")
SYSTEM_BUS_CHANNEL = os.environ.get("RT_SYSTEM_BUS_CHANNEL", "rt:system:bus")
UI_INTENTS_CHANNEL = os.environ.get("RT_UI_INTENTS_CHANNEL", "rt:ui:intents")
UI_PROJECTION_CHANGED_TOPIC = os.environ.get("RT_UI_PROJECTION_CHANGED_TOPIC", "ui.projection.changed")

# Event-driven projection with a slow safety pass. The safety pass keeps the
# single-writer lock fresh and catches any legacy writer that still does not
# publish a state.changed event.
DEFAULT_EVENT_TIMEOUT_MS = int(os.environ.get("UI_PROJECTOR_EVENT_TIMEOUT_MS", "5000"))
DEFAULT_INTENT_DEBOUNCE_MS = int(os.environ.get("UI_PROJECTOR_INTENT_DEBOUNCE_MS", "75"))

CONTROL_NAMES = ("back", "page", "primary", "cancel", "mode", "info")

BLINK_SLOW_MS = 900
BLINK_FAST_MS = 400
PULSE_MS = 900

CONFIG_PAGES_DIR = Path(os.environ.get("RT_PAGES_PATH", "/opt/rollingthunder/config/pages"))
CONFIG_PANELS_DIR = Path(os.environ.get("RT_PANELS_PATH", "/opt/rollingthunder/config/panels"))


class GracefulExit(SystemExit):
    pass


@dataclass(frozen=True)
class Config:
    redis_url: str
    poll_ms: int
    stale_ms: int
    lock_key: str
    lock_ttl_ms: int
    lock_value: str
    interaction_state_key: str
    page_context_key: str
    snapshot_keys: Sequence[str]
    page_keys: Sequence[str]
    focus_keys: Sequence[str]
    modal_keys: Sequence[str]
    browse_keys: Sequence[str]
    authority_keys: Sequence[str]
    result_keys: Sequence[str]
    page_context_keys: Sequence[str]
    system_health_keys: Sequence[str]
    event_timeout_ms: int
    intent_debounce_ms: int


class UIStateProjector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.log = logging.getLogger("ui_state_projector")
        self.redis_client = self._connect()
        self.running = True
        self.last_projection: Dict[str, str] = {}
        self.last_comparison_projection: Dict[str, str] = {}
        self.last_optional_keys: set[str] = set()
        self._page_ids = self._load_page_ids()
        self._browsable_panel_ids = self._load_browsable_panel_ids()
        self._breadcrumb_state: dict[str, Any] = {"last_page": None, "return_button": None}

    def _connect(self) -> Redis:
        client = redis.Redis.from_url(
            self.config.redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            retry_on_timeout=True,
            health_check_interval=15,
        )
        client.ping()
        return client

    def _read_pota_spot_statuses_for_band(self, band: Optional[str]) -> Dict[str, Any]:
        band_text = self._normalize_scalar(band)
        if not band_text:
            return {"day_utc": None, "spots": {}}

        key = f"{POTA_SPOT_STATUS_KEY_PREFIX}{band_text.lower()}"
        raw = self._read_key_any(key)
        obj = self._normalize_object(raw) or {}

        spots = obj.get("spots")
        if not isinstance(spots, Mapping):
            spots = {}

        normalized_spots: Dict[str, Any] = {}
        for spot_id, entry in spots.items():
            sid = self._normalize_scalar(spot_id)
            if not sid:
                continue

            if isinstance(entry, Mapping):
                normalized_spots[sid] = {
                    "status": self._normalize_scalar(entry.get("status")),
                    "updated_at_ms": self._coerce_int(entry.get("updated_at_ms")),
                }
            else:
                normalized_spots[sid] = {
                    "status": self._normalize_scalar(entry),
                    "updated_at_ms": None,
                }

        return {
            "day_utc": self._normalize_scalar(obj.get("day_utc")),
            "spots": normalized_spots,
        }

    def reconnect(self) -> None:
        while self.running:
            try:
                self.redis_client = self._connect()
                self.log.info("connected to redis")
                return
            except (RedisConnectionError, RedisTimeoutError) as exc:
                self.log.warning("redis reconnect failed: %s", exc)
                time.sleep(1.0)
        raise GracefulExit()

    def stop(self, *_args: Any) -> None:
        self.running = False
        raise GracefulExit()

    def run(self) -> None:
        self.log.info(
            "starting event-driven ui state projector; system_bus=%s intents=%s safety_ms=%s",
            SYSTEM_BUS_CHANNEL,
            UI_INTENTS_CHANNEL,
            self.config.event_timeout_ms,
        )

        # Initial projection at startup, then event-driven refreshes after that.
        self._project_once(reason="startup")

        pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(SYSTEM_BUS_CHANNEL, UI_INTENTS_CHANNEL)

        safety_interval = max(0.5, self.config.event_timeout_ms / 1000.0)
        debounce_interval = max(0.0, self.config.intent_debounce_ms / 1000.0)
        next_safety_at = time.monotonic() + safety_interval
        pending_projection_at: Optional[float] = None
        pending_reason = "event"

        while self.running:
            try:
                timeout = 0.25
                now = time.monotonic()
                next_due = next_safety_at
                if pending_projection_at is not None:
                    next_due = min(next_due, pending_projection_at)
                timeout = max(0.05, min(timeout, next_due - now))

                msg = pubsub.get_message(timeout=timeout)
                now = time.monotonic()

                if msg and self._message_should_trigger_projection(msg):
                    if msg.get("channel") == UI_INTENTS_CHANNEL:
                        pending_projection_at = now + debounce_interval
                        pending_reason = "intent"
                    else:
                        pending_projection_at = now
                        pending_reason = "state.changed"

                if pending_projection_at is not None and now >= pending_projection_at:
                    self._project_once(reason=pending_reason)
                    pending_projection_at = None
                    next_safety_at = time.monotonic() + safety_interval

                if now >= next_safety_at:
                    self._project_once(reason="safety")
                    next_safety_at = time.monotonic() + safety_interval

            except (RedisConnectionError, RedisTimeoutError) as exc:
                self.log.warning("redis error: %s", exc)
                try:
                    pubsub.close()
                except Exception:
                    pass
                self.reconnect()
                pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(SYSTEM_BUS_CHANNEL, UI_INTENTS_CHANNEL)
                next_safety_at = time.monotonic() + safety_interval
                pending_projection_at = time.monotonic()
                pending_reason = "reconnect"
            except GracefulExit:
                try:
                    pubsub.close()
                except Exception:
                    pass
                raise
            except Exception:
                self.log.exception("unexpected projector loop failure")
                time.sleep(0.25)

    def _project_once(self, reason: str) -> None:
        if not self._acquire_writer_lock():
            return

        upstream = self._read_upstream_state()
        projection, optional_keys = self._build_projection(upstream)
        self._apply_projection(projection, optional_keys)
        self.log.debug("projection pass completed reason=%s", reason)

    def _message_should_trigger_projection(self, msg: Mapping[str, Any]) -> bool:
        if msg.get("type") != "message":
            return False

        channel = msg.get("channel")
        if channel == UI_INTENTS_CHANNEL:
            # ui_interaction_state mutates rt:interaction:state in response to
            # intents, but it does not currently publish a state.changed event.
            # Treat intents as a cheap event trigger and let semantic dedupe
            # suppress no-op/rejected intents.
            return True

        if channel != SYSTEM_BUS_CHANNEL:
            return False

        raw = msg.get("data")
        if not isinstance(raw, str):
            return False

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return False

        if event.get("topic") != "state.changed":
            return False

        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return False

        keys = payload.get("keys")
        if not isinstance(keys, list):
            return False

        return self._keys_affect_projection(keys)

    def _keys_affect_projection(self, keys: Sequence[Any]) -> bool:
        relevant_exact = {
            self.config.interaction_state_key,
            self.config.page_context_key,
            *self.config.snapshot_keys,
            *self.config.page_keys,
            *self.config.focus_keys,
            *self.config.modal_keys,
            *self.config.browse_keys,
            *self.config.authority_keys,
            *self.config.result_keys,
            *self.config.page_context_keys,
            *self.config.system_health_keys,
        }

        for key in keys:
            if not isinstance(key, str):
                continue
            if key in relevant_exact:
                return True
            if key.startswith(POTA_SPOT_STATUS_KEY_PREFIX):
                return True
        return False

    def _acquire_writer_lock(self) -> bool:
        current = self.redis_client.get(self.config.lock_key)
        if current == self.config.lock_value:
            self.redis_client.pexpire(self.config.lock_key, self.config.lock_ttl_ms)
            return True

        acquired = self.redis_client.set(
            self.config.lock_key,
            self.config.lock_value,
            nx=True,
            px=self.config.lock_ttl_ms,
        )
        if acquired:
            self.log.info("acquired single-writer lock %s", self.config.lock_key)
            return True

        if current and current != self.config.lock_value:
            self.log.warning("ui projection lock held by %s; projector remaining passive", current)
        return False

    def _read_upstream_state(self) -> Dict[str, Any]:
        upstream: Dict[str, Any] = {"_sources": {}}

        interaction, interaction_key = self._read_first_object([self.config.interaction_state_key])
        if interaction is not None:
            upstream.update(interaction)
            upstream["_sources"]["interaction_state"] = interaction_key

        page_context_from_primary, page_context_key = self._read_first_object([self.config.page_context_key])
        if page_context_from_primary is not None:
            upstream["page_context"] = page_context_from_primary
            upstream["_sources"]["page_context_primary"] = page_context_key

        if "page" not in upstream:
            snapshot, snapshot_key = self._read_first_object(self.config.snapshot_keys)
            if snapshot is not None:
                for key, value in snapshot.items():
                    upstream.setdefault(key, value)
                upstream["_sources"]["snapshot"] = snapshot_key

        page = self._extract_scalar(upstream, ["page", "current_page"])
        if page is None:
            page, page_key = self._read_first_scalar(self.config.page_keys)
            if page_key:
                upstream["_sources"]["page"] = page_key
        if page is not None:
            upstream["page"] = page

        focus = self._extract_scalar(upstream, ["focus", "focused_panel", "focus_panel"])
        if focus is None:
            focus, focus_key = self._read_first_scalar(self.config.focus_keys)
            if focus_key:
                upstream["_sources"]["focus"] = focus_key
        if focus is not None:
            upstream["focus"] = focus

        modal = self._extract_object(upstream, ["modal", "active_modal"])
        if modal is None:
            modal, modal_key = self._read_first_object(self.config.modal_keys)
            if modal_key:
                upstream["_sources"]["modal"] = modal_key
        if modal is not None:
            upstream["modal"] = modal

        browse = self._extract_object(upstream, ["browse", "browse_state"])
        if browse is None:
            browse, browse_key = self._read_first_object(self.config.browse_keys)
            if browse_key:
                upstream["_sources"]["browse"] = browse_key
        if browse is not None:
            upstream["browse"] = browse

        authority = self._extract_object(upstream, ["authority", "ui_authority"])
        if authority is None:
            authority, authority_key = self._read_first_object(self.config.authority_keys)
            if authority_key:
                upstream["_sources"]["authority"] = authority_key
        if authority is not None:
            upstream["authority"] = authority

        last_result = self._extract_object(upstream, ["last_result", "result"])
        if last_result is None:
            last_result, result_key = self._read_first_object(self.config.result_keys)
            if result_key:
                upstream["_sources"]["last_result"] = result_key
        if last_result is not None:
            upstream["last_result"] = last_result

        if "page_context" not in upstream:
            page_context, context_key = self._read_first_object(self.config.page_context_keys)
            if context_key:
                upstream["_sources"]["page_context"] = context_key
            if page_context is not None:
                upstream["page_context"] = page_context

        health, health_key = self._read_first_object(self.config.system_health_keys)
        if health is not None:
            upstream["system_health"] = health
            upstream["_sources"]["system_health"] = health_key

        return upstream

    def _read_first_scalar(self, keys: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
        for key in keys:
            value = self._read_key_any(key)
            scalar = self._normalize_scalar(value)
            if scalar is not None:
                return scalar, key
        return None, None

    def _read_first_object(self, keys: Sequence[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        for key in keys:
            value = self._read_key_any(key)
            obj = self._normalize_object(value)
            if obj is not None:
                return obj, key
        return None, None

    def _read_key_any(self, key: str) -> Any:
        key_type = self.redis_client.type(key)
        if key_type == "none":
            return None
        if key_type == "string":
            return self.redis_client.get(key)
        if key_type == "hash":
            return self.redis_client.hgetall(key)
        if key_type == "list":
            values = self.redis_client.lrange(key, 0, -1)
            return values[-1] if values else None
        return None

    @staticmethod
    def _normalize_scalar(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.startswith("{") or text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    return text
                if isinstance(parsed, str):
                    return parsed.strip() or None
                return None
            return text
        return str(value).strip() or None

    @staticmethod
    def _normalize_object(value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return None

    @staticmethod
    def _extract_scalar(container: Mapping[str, Any], names: Sequence[str]) -> Optional[str]:
        for name in names:
            if name in container:
                value = UIStateProjector._normalize_scalar(container[name])
                if value is not None:
                    return value
        ui = container.get("ui")
        if isinstance(ui, Mapping):
            return UIStateProjector._extract_scalar(ui, names)
        return None

    @staticmethod
    def _extract_object(container: Mapping[str, Any], names: Sequence[str]) -> Optional[Dict[str, Any]]:
        for name in names:
            if name in container:
                value = UIStateProjector._normalize_object(container[name])
                if value is not None:
                    return value
        ui = container.get("ui")
        if isinstance(ui, Mapping):
            return UIStateProjector._extract_object(ui, names)
        return None

    def _build_projection(self, upstream: Mapping[str, Any]) -> Tuple[Dict[str, str], set[str]]:
        now_ms = int(time.time() * 1000)

        page = self._normalize_page(upstream.get("page"))
        focus = self._normalize_focus(upstream.get("focus"), page)
        modal = self._normalize_modal(upstream.get("modal"), page, focus)
        browse = self._normalize_browse(upstream.get("browse"), page, focus, now_ms)
        page_context = self._normalize_page_context(upstream.get("page_context"), page)
        last_result = self._normalize_last_result(upstream.get("last_result"), page, focus, now_ms)

        authority = self._normalize_authority(
            upstream.get("authority"),
            upstream.get("system_health"),
            upstream,
            page,
            focus,
            modal,
            browse,
            now_ms,
        )
        layer = self._compute_layer(modal=modal, browse=browse, authority=authority)

        self._breadcrumb_state = self._update_breadcrumb_state(
            self._breadcrumb_state,
            self._page_ids,
            page,
        )

        led_snapshot = self._build_led_snapshot(
            page=page,
            focus=focus,
            layer=layer,
            modal=modal,
            browse=browse,
            authority=authority,
            last_result=last_result,
            breadcrumb=self._breadcrumb_state,
        )

        projection: Dict[str, str] = {
            PROJECTED_KEYS["layer"]: layer,
            PROJECTED_KEYS["authority"]: self._json(authority),
            PROJECTED_KEYS["led_snapshot"]: self._json_any(led_snapshot),
        }
        optional_keys: set[str] = set()

        if page is not None:
            projection[PROJECTED_KEYS["page"]] = page
        if focus is not None:
            projection[PROJECTED_KEYS["focus"]] = focus
        if modal is not None:
            projection[PROJECTED_KEYS["modal"]] = self._json(modal)
        if browse is not None:
            browse_json = self._json(browse)
            projection[PROJECTED_KEYS["browse"]] = browse_json
            indexed_key = f"rt:ui:browse:{browse['panel']}"
            projection[indexed_key] = browse_json
            optional_keys.add(indexed_key)
        if page_context is not None:
            projection[PROJECTED_KEYS["page_context"]] = self._json(page_context)
        if last_result is not None:
            projection[PROJECTED_KEYS["last_result"]] = self._json(last_result)

        if authority["degraded"]:
            if page is None:
                projection.pop(PROJECTED_KEYS["focus"], None)
                projection.pop(PROJECTED_KEYS["browse"], None)
                projection.pop(PROJECTED_KEYS["page_context"], None)
                for key in list(optional_keys):
                    projection.pop(key, None)
                optional_keys.clear()
            if focus is None:
                projection.pop(PROJECTED_KEYS["browse"], None)
                for key in list(optional_keys):
                    projection.pop(key, None)
                optional_keys.clear()

        return projection, optional_keys

    @staticmethod
    def _normalize_page(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        page = value.strip()
        return page or None

    @staticmethod
    def _normalize_focus(value: Any, page: Optional[str]) -> Optional[str]:
        if page is None:
            return None
        if not isinstance(value, str):
            return None
        focus = value.strip()
        return focus or None

    def _normalize_modal(self, value: Any, page: Optional[str], focus: Optional[str]) -> Optional[Dict[str, Any]]:
        modal = self._normalize_object(value)
        if not modal:
            return None
        if modal.get("active") is False:
            return None

        modal_type = self._normalize_scalar(modal.get("type"))
        modal_id = self._normalize_scalar(modal.get("id"))
        opened_at_ms = self._coerce_int(modal.get("opened_at_ms"))

        if modal_id is None:
            if modal_type is None:
                return None
            modal_id = self._stable_id(modal)
        if modal_type is None:
            modal_type = "generic"

        return {
            "id": modal_id,
            "type": modal_type,
            "title": self._normalize_scalar(modal.get("title")),
            "confirmable": bool(modal.get("confirmable", True)),
            "cancelable": bool(modal.get("cancelable", True)),
            "destructive": bool(modal.get("destructive", False)),
            "confirm_label": self._normalize_scalar(modal.get("confirm_label")),
            "cancel_label": self._normalize_scalar(modal.get("cancel_label")),
            "warning": self._normalize_scalar(modal.get("warning")),
            "message": self._normalize_scalar(modal.get("message")),
            "submessage": self._normalize_scalar(modal.get("submessage")),
            "node_id": self._normalize_scalar(modal.get("node_id")),
            "step": self._normalize_scalar(modal.get("step")),
            "duration_ms": self._coerce_int(modal.get("duration_ms")),
            "auto_close_at_ms": self._coerce_int(modal.get("auto_close_at_ms")),
            "spot_id": self._normalize_scalar(modal.get("spot_id")),
            "callsign": self._normalize_scalar(modal.get("callsign")),
            "park_ref": self._normalize_scalar(modal.get("park_ref")),
            "band": self._normalize_scalar(modal.get("band")),
            "freq_hz": self._coerce_int(modal.get("freq_hz")),
            "selected_option_index": self._coerce_int(modal.get("selected_option_index")) or 0,
            "options": [
                {
                    "key": self._normalize_scalar(item.get("key")),
                    "label": self._normalize_scalar(item.get("label")),
                }
                for item in modal.get("options", [])
                if isinstance(item, Mapping)
            ],
            "context": {
                "page": page,
                "focused_panel": focus,
            },
            "opened_at_ms": opened_at_ms or int(time.time() * 1000),
        }

    def _normalize_browse(
        self,
        value: Any,
        page: Optional[str],
        focus: Optional[str],
        now_ms: int,
    ) -> Optional[Dict[str, Any]]:
        browse = self._normalize_object(value)
        if not browse:
            return None
        if not bool(browse.get("active", True)):
            return None

        effective_page = self._normalize_scalar(browse.get("page")) or page
        effective_panel = self._normalize_scalar(browse.get("panel")) or focus
        if effective_page is None or effective_panel is None:
            return None

        selected_index = self._coerce_int(browse.get("selected_index"))
        if selected_index is None:
            return None

        return {
            "active": True,
            "page": effective_page,
            "panel": effective_panel,
            "selected_index": selected_index,
            "selected_id": self._normalize_scalar(browse.get("selected_id")),
            "count": self._coerce_int(browse.get("count")) or 0,
            "updated_at_ms": self._coerce_int(browse.get("updated_at_ms")) or now_ms,
        }

    def _normalize_authority(
        self,
        authority_value: Any,
        system_health_value: Any,
        upstream: Mapping[str, Any],
        page: Optional[str],
        focus: Optional[str],
        modal: Optional[Mapping[str, Any]],
        browse: Optional[Mapping[str, Any]],
        now_ms: int,
    ) -> Dict[str, Any]:
        authority = self._normalize_object(authority_value) or {}
        degraded_reasons: List[str] = []
        degraded = False
        stale = False
        controller_authoritative = bool(authority.get("controller_authoritative", True))

        if page is None:
            degraded = True
            controller_authoritative = False
            degraded_reasons.append("missing_page")

        if browse is not None and focus is None:
            degraded = True
            controller_authoritative = False
            degraded_reasons.append("missing_focus_for_browse")

        source_ts_ms = (
            self._coerce_int(authority.get("ts_ms"))
            or self._coerce_int(authority.get("updated_at_ms"))
            or self._find_timestamp_ms(upstream)
        )
        if source_ts_ms is not None and (now_ms - source_ts_ms) > self.config.stale_ms:
            stale = True
            degraded = True
            controller_authoritative = False
            degraded_reasons.append("stale_upstream_state")

        system_health = self._normalize_object(system_health_value) or {}
        health_status = self._normalize_scalar(
            system_health.get("status")
            or system_health.get("state")
            or system_health.get("health")
        )
        if health_status and health_status.lower() not in {"ok", "healthy", "ready"}:
            degraded = True
            degraded_reasons.append(f"system_health_{health_status.lower()}")

        if bool(authority.get("degraded", False)):
            degraded = True
            degraded_reasons.append(self._normalize_scalar(authority.get("reason")) or "upstream_degraded")

        if bool(authority.get("stale", False)):
            stale = True
            degraded = True
            controller_authoritative = False
            degraded_reasons.append(self._normalize_scalar(authority.get("reason")) or "upstream_stale")

        if modal is None and browse is None and page is None:
            controller_authoritative = False

        reason = self._dedupe_reason_codes(degraded_reasons)
        return {
            "controller_authoritative": controller_authoritative,
            "degraded": degraded,
            "stale": stale,
            "reason": reason,
            "ts_ms": now_ms,
        }

    def _normalize_last_result(
        self,
        value: Any,
        page: Optional[str],
        focus: Optional[str],
        now_ms: int,
    ) -> Optional[Dict[str, Any]]:
        result = self._normalize_object(value)
        if not result:
            return None

        result_name = self._normalize_scalar(result.get("result"))
        intent = self._normalize_scalar(result.get("intent"))
        if result_name is None or intent is None:
            return None

        return {
            "result": result_name,
            "intent": intent,
            "reason": self._normalize_scalar(result.get("reason")),
            "execution_id": self._normalize_scalar(result.get("execution_id")),
            "page": self._normalize_scalar(result.get("page")) or page,
            "focused_panel": self._normalize_scalar(result.get("focused_panel")) or focus,
            "ts_ms": self._coerce_int(result.get("ts_ms")) or now_ms,
        }

    def _normalize_page_context(self, value: Any, page: Optional[str]) -> Optional[Dict[str, Any]]:
        context = self._normalize_object(value)
        if not context or page is None:
            return None

        if page != "pota":
            return None

        normalized = dict(context)
        normalized["page"] = page

        selected_band = self._normalize_scalar(
            normalized.get("selected_band") or normalized.get("band")
        )
        status_state = self._read_pota_spot_statuses_for_band(selected_band)

        normalized["spot_status_day_utc"] = status_state.get("day_utc")
        normalized["spot_statuses"] = status_state.get("spots") or {}

        return normalized

    @staticmethod
    def _compute_layer(
        modal: Optional[Mapping[str, Any]],
        browse: Optional[Mapping[str, Any]],
        authority: Mapping[str, Any],
    ) -> str:
        if modal is not None:
            return "modal"
        if bool(authority.get("degraded")) or bool(authority.get("stale")) or not bool(
            authority.get("controller_authoritative", True)
        ):
            return "degraded"
        if browse is not None:
            return "browse"
        return "default"

    @staticmethod
    def _strip_volatile_fields(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(k): UIStateProjector._strip_volatile_fields(v)
                for k, v in value.items()
                if str(k) not in {
                    "ts",
                    "ts_ms",
                    "timestamp",
                    "timestamp_ms",
                    "updated_at_ms",
                    "last_update_ms",
                    "gps_last_seen_ms",
                    "pos_last_good_ms",
                    "opened_at_ms",
                }
            }
        if isinstance(value, list):
            return [UIStateProjector._strip_volatile_fields(item) for item in value]
        return value

    def _semantic_projection(self, projection: Dict[str, str]) -> Dict[str, str]:
        semantic: Dict[str, str] = {}

        for key, value in projection.items():
            if not isinstance(value, str):
                semantic[key] = str(value)
                continue

            text = value.strip()
            if not text:
                semantic[key] = value
                continue

            if not (text.startswith("{") or text.startswith("[")):
                semantic[key] = value
                continue

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                semantic[key] = value
                continue

            stripped = self._strip_volatile_fields(parsed)
            semantic[key] = json.dumps(stripped, sort_keys=True, separators=(",", ":"))

        return semantic

    def _apply_projection(self, projection: Dict[str, str], optional_keys: set[str]) -> None:
        desired_keys = set(projection.keys())
        managed_keys = set(PROJECTED_KEYS.values()) | self.last_optional_keys | optional_keys
        stale_keys = sorted(managed_keys - desired_keys)

        comparison_projection = self._semantic_projection(projection)

        changed_keys: list[str] = []
        for key, value in sorted(comparison_projection.items()):
            prev = self.last_comparison_projection.get(key)
            if prev != value:
                changed_keys.append(key)

        deleted_keys: list[str] = []
        for key in stale_keys:
            if key in self.last_comparison_projection or key in self.last_optional_keys:
                deleted_keys.append(key)

        if (
            comparison_projection == self.last_comparison_projection
            and optional_keys == self.last_optional_keys
        ):
            return

        pipe = self.redis_client.pipeline(transaction=False)
        for key, value in sorted(projection.items()):
            pipe.set(key, value)
        if PROJECTED_KEYS["last_result"] in projection:
            pipe.pexpire(PROJECTED_KEYS["last_result"], 5000)
        for key in stale_keys:
            pipe.delete(key)
        pipe.execute()

        self.last_projection = dict(projection)
        self.last_comparison_projection = dict(comparison_projection)
        self.last_optional_keys = set(optional_keys)
        self._publish_projection_changed(changed_keys, deleted_keys)
        self.log.debug("applied ui projection keys=%s deleted=%s", sorted(desired_keys), stale_keys)

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _find_timestamp_ms(upstream: Mapping[str, Any]) -> Optional[int]:
        candidates = [
            upstream.get("ts_ms"),
            upstream.get("updated_at_ms"),
            upstream.get("timestamp_ms"),
        ]
        for candidate in candidates:
            coerced = UIStateProjector._coerce_int(candidate)
            if coerced is not None:
                return coerced

        for key in ("authority", "browse", "modal", "last_result", "page_context"):
            value = upstream.get(key)
            if isinstance(value, Mapping):
                for nested_key in ("ts_ms", "updated_at_ms", "opened_at_ms"):
                    coerced = UIStateProjector._coerce_int(value.get(nested_key))
                    if coerced is not None:
                        return coerced
        return None

    @staticmethod
    def _dedupe_reason_codes(reasons: Iterable[str]) -> Optional[str]:
        ordered: List[str] = []
        seen: set[str] = set()
        for reason in reasons:
            reason_text = (reason or "").strip()
            if not reason_text or reason_text in seen:
                continue
            seen.add(reason_text)
            ordered.append(reason_text)
        if not ordered:
            return None
        return ",".join(ordered)

    @staticmethod
    def _stable_id(payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha1(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _json_any(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def _publish_projection_changed(self, changed_keys: Sequence[str], deleted_keys: Sequence[str]) -> None:
        if not changed_keys and not deleted_keys:
            return

        event = {
            "topic": UI_PROJECTION_CHANGED_TOPIC,
            "payload": {
                "keys": list(changed_keys) + list(deleted_keys),
                "changed_keys": list(changed_keys),
                "deleted_keys": list(deleted_keys),
                "ts_ms": int(time.time() * 1000),
            },
            "ts_ms": int(time.time() * 1000),
            "source": "rt-ui-state-projector",
        }

        try:
            self.redis_client.publish(
                UI_BUS_CHANNEL,
                json.dumps(event, sort_keys=True, separators=(",", ":")),
            )
        except Exception:
            self.log.exception("failed to publish ui.projection.changed")

    @staticmethod
    def _truthy(value: Any) -> bool:
        if value is True:
            return True
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {
                "1", "true", "yes", "y", "on", "open", "active", "enabled"
            }
        if isinstance(value, dict):
            if not value:
                return False
            for key in ("active", "open", "visible", "present", "ok", "value"):
                if key in value and UIStateProjector._truthy(value[key]):
                    return True
            return True
        return bool(value)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _coerce_int_default(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _load_page_ids(self) -> list[str]:
        pages: list[tuple[int, str]] = []
        try:
            if not CONFIG_PAGES_DIR.exists():
                return []
            for f in CONFIG_PAGES_DIR.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                page_id = self._string_or_none(data.get("id"))
                if not page_id:
                    continue
                order = self._coerce_int_default(data.get("order"), 9999)
                pages.append((order, page_id))
        except Exception:
            return []

        pages.sort(key=lambda item: (item[0], item[1]))
        seen: set[str] = set()
        ids: list[str] = []
        for _order, page_id in pages:
            if page_id in seen:
                continue
            seen.add(page_id)
            ids.append(page_id)
        return ids

    def _load_browsable_panel_ids(self) -> set[str]:
        browsable: set[str] = set()
        try:
            if not CONFIG_PANELS_DIR.exists():
                return browsable

            for f in CONFIG_PANELS_DIR.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue

                panel_id = self._string_or_none(data.get("id"))
                if not panel_id:
                    continue

                interaction = self._as_dict(data.get("interaction"))
                if bool(interaction.get("browsable", False)):
                    browsable.add(panel_id)
        except Exception:
            return set()

        return browsable

    def _return_button_for_transition(
        self,
        page_ids: list[str],
        previous_page: str | None,
        current_page: str | None,
    ) -> str | None:
        if not previous_page or not current_page:
            return None
        if previous_page == current_page:
            return None
        if previous_page not in page_ids or current_page not in page_ids:
            return None

        count = len(page_ids)
        if count <= 1:
            return None

        prev_idx = page_ids.index(previous_page)
        if page_ids[(prev_idx + 1) % count] == current_page:
            return "back"
        if page_ids[(prev_idx - 1) % count] == current_page:
            return "page"
        return None

    def _update_breadcrumb_state(
        self,
        breadcrumb: dict[str, Any],
        page_ids: list[str],
        current_page: str | None,
    ) -> dict[str, Any]:
        if not isinstance(breadcrumb, dict):
            breadcrumb = {}

        last_page = self._string_or_none(breadcrumb.get("last_page"))
        return_button = self._string_or_none(breadcrumb.get("return_button"))

        if current_page != last_page:
            return_button = self._return_button_for_transition(page_ids, last_page, current_page)
            breadcrumb = {
                "last_page": current_page,
                "return_button": return_button,
            }
        else:
            breadcrumb = {
                "last_page": last_page,
                "return_button": return_button,
            }

        return breadcrumb

    @staticmethod
    def _page_navigation_available(page: str | None) -> bool:
        return bool(page)

    @staticmethod
    def _back_available(page: str | None, modal_active: bool, browse_active: bool) -> bool:
        if modal_active or browse_active:
            return True
        return bool(page and page != "home")

    @staticmethod
    def _focus_navigation_available(focus: str | None, modal_active: bool) -> bool:
        if modal_active:
            return False
        return bool(focus)

    def _browse_capable_focus(self, page: str | None, focus: str | None) -> bool:
        _ = self._string_or_none(page)
        focus = self._string_or_none(focus)
        if not focus:
            return False
        return focus in self._browsable_panel_ids

    @staticmethod
    def _has_browse_selection(browse_obj: dict[str, Any]) -> bool:
        return UIStateProjector._coerce_int_default(browse_obj.get("count"), 0) > 0

    @staticmethod
    def _is_destructive_modal(modal_obj: dict[str, Any]) -> bool:
        return bool(modal_obj.get("destructive", False))

    @staticmethod
    def _modal_confirmable(modal_obj: dict[str, Any]) -> bool:
        return bool(modal_obj.get("confirmable", False))

    @staticmethod
    def _modal_cancelable(modal_obj: dict[str, Any]) -> bool:
        return bool(modal_obj.get("cancelable", False))

    @staticmethod
    def _destructive_modal_armed(modal_obj: dict[str, Any]) -> bool:
        step = str(modal_obj.get("step") or "").strip().lower()
        return step == "armed"

    @staticmethod
    def _has_recent_result(last_result_obj: Any) -> bool:
        if last_result_obj is None:
            return False
        if isinstance(last_result_obj, str):
            return bool(last_result_obj.strip())
        if isinstance(last_result_obj, dict):
            return bool(last_result_obj)
        return True

    @staticmethod
    def _semantic_to_snapshot(mode: str) -> dict[str, Any]:
        if mode == "off":
            return {"mode": "off"}
        if mode == "on":
            return {"mode": "on"}
        if mode == "blink_slow":
            return {"mode": "blink_slow", "period_ms": BLINK_SLOW_MS}
        if mode == "blink_fast":
            return {"mode": "blink_fast", "period_ms": BLINK_FAST_MS}
        if mode == "pulse":
            return {"mode": "pulse", "period_ms": PULSE_MS}
        return {"mode": "off"}

    @staticmethod
    def _last_result_token(last_result_obj: dict[str, Any]) -> str | None:
        if not isinstance(last_result_obj, dict) or not last_result_obj:
            return None

        execution_id = UIStateProjector._string_or_none(last_result_obj.get("execution_id"))
        if execution_id:
            return execution_id

        ts_ms = UIStateProjector._string_or_none(last_result_obj.get("ts_ms"))
        intent = UIStateProjector._string_or_none(last_result_obj.get("intent"))
        result = UIStateProjector._string_or_none(last_result_obj.get("result"))
        if ts_ms and intent and result:
            return f"{ts_ms}:{intent}:{result}"
        return None

    @staticmethod
    def _result_is_positive(last_result_obj: dict[str, Any]) -> bool:
        result = str(last_result_obj.get("result") or "").strip().lower()
        if not result:
            return False

        negative = {
            "rejected", "error", "failed", "denied", "ignored",
            "invalid", "timeout", "unavailable", "blocked",
        }
        return result not in negative

    @staticmethod
    def _show_push_button_for_result(last_result_obj: dict[str, Any]) -> str | None:
        if not UIStateProjector._result_is_positive(last_result_obj):
            return None

        intent = str(last_result_obj.get("intent") or "").strip().lower()
        return {
            "ui.ok": "primary",
            "ui.cancel": "cancel",
            "ui.back": "back",
            "ui.page.next": "page",
            "ui.focus.next": "mode",
            "ui.focus.prev": "info",
        }.get(intent)

    def _derive_semantic_leds(
        self,
        page: str | None,
        focus: str | None,
        layer: str,
        modal_obj: dict[str, Any],
        browse_obj: dict[str, Any],
        authority_obj: dict[str, Any],
        last_result_obj: dict[str, Any],
        breadcrumb: dict[str, Any],
    ) -> dict[str, str]:
        degraded = bool(authority_obj.get("degraded"))
        stale = bool(authority_obj.get("stale"))
        controller_authoritative = bool(authority_obj.get("controller_authoritative"))
        modal_active = self._truthy(modal_obj)
        browse_active = self._truthy(browse_obj)
        recent_result = self._has_recent_result(last_result_obj)

        leds: dict[str, str] = {name: "off" for name in CONTROL_NAMES}
        return_button = self._string_or_none(self._as_dict(breadcrumb).get("return_button"))

        if self._page_navigation_available(page):
            leds["page"] = "on"

        if self._back_available(page, modal_active, browse_active):
            leds["back"] = "on"

        if self._focus_navigation_available(focus, modal_active) and self._browse_capable_focus(page, focus):
            leds["mode"] = "pulse"

        leds["primary"] = "off"

        if recent_result and not (degraded or stale):
            leds["info"] = "pulse"

        if not modal_active and not browse_active and not (degraded or stale or not controller_authoritative):
            if return_button == "back" and leds["back"] != "off":
                leds["back"] = "pulse"
            elif return_button == "page" and leds["page"] != "off":
                leds["page"] = "pulse"

        if browse_active or layer == "browse":
            leds["mode"] = "on"
            leds["back"] = "on"
            leds["cancel"] = "on"
            if self._has_browse_selection(browse_obj):
                leds["primary"] = "on"
            else:
                leds["primary"] = "off"

        if modal_active or layer == "modal":
            leds["mode"] = "off"
            leds["back"] = "on"

            if self._modal_cancelable(modal_obj):
                leds["cancel"] = "on"
            else:
                leds["cancel"] = "off"

            if self._modal_confirmable(modal_obj):
                if self._is_destructive_modal(modal_obj):
                    if self._destructive_modal_armed(modal_obj):
                        leds["primary"] = "blink_fast"
                        if self._modal_cancelable(modal_obj):
                            leds["cancel"] = "blink_slow"
                    else:
                        leds["primary"] = "blink_slow"
                        if self._modal_cancelable(modal_obj):
                            leds["cancel"] = "on"
                else:
                    leds["primary"] = "blink_slow"
            else:
                leds["primary"] = "off"

        if degraded or stale or not controller_authoritative or layer == "degraded":
            leds["info"] = "blink_slow"
            leds["primary"] = "off"
            leds["mode"] = "off"

            if modal_active and self._modal_confirmable(modal_obj):
                if self._is_destructive_modal(modal_obj):
                    leds["primary"] = "blink_fast" if self._destructive_modal_armed(modal_obj) else "blink_slow"
                else:
                    leds["primary"] = "blink_slow"

            if self._page_navigation_available(page):
                leds["page"] = "pulse"

            if self._back_available(page, modal_active, browse_active):
                leds["back"] = "pulse"

            if self._modal_cancelable(modal_obj) or browse_active or (page and page != "home"):
                leds["cancel"] = "pulse" if not modal_active else leds["cancel"]

        return leds

    def _build_led_snapshot(
        self,
        page: str | None,
        focus: str | None,
        layer: str,
        modal: Optional[Mapping[str, Any]],
        browse: Optional[Mapping[str, Any]],
        authority: Mapping[str, Any],
        last_result: Optional[Mapping[str, Any]],
        breadcrumb: dict[str, Any],
    ) -> dict[str, Any]:
        modal_obj = dict(modal) if isinstance(modal, Mapping) else {}
        browse_obj = dict(browse) if isinstance(browse, Mapping) else {}
        authority_obj = dict(authority) if isinstance(authority, Mapping) else {}
        last_result_obj = dict(last_result) if isinstance(last_result, Mapping) else {}

        semantic_leds = self._derive_semantic_leds(
            page=page,
            focus=focus,
            layer=layer,
            modal_obj=modal_obj,
            browse_obj=browse_obj,
            authority_obj=authority_obj,
            last_result_obj=last_result_obj,
            breadcrumb=breadcrumb,
        )

        leds = {
            name: self._semantic_to_snapshot(mode)
            for name, mode in semantic_leds.items()
        }

        show_push = None
        token = self._last_result_token(last_result_obj)
        button = self._show_push_button_for_result(last_result_obj)
        if token and button:
            show_push = {
                "button": button,
                "token": token,
            }

        return {
            "schema": 1,
            "type": "led_snapshot",
            "ts_ms": int(time.time() * 1000),
            "leds": leds,
            "show_push": show_push,
        }


def csv_env(name: str, default: Sequence[str]) -> Sequence[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


def build_redis_url() -> str:
    if os.environ.get("RT_REDIS_URL"):
        return os.environ["RT_REDIS_URL"]
    host = os.environ.get("RT_REDIS_HOST", "127.0.0.1")
    port = os.environ.get("RT_REDIS_PORT", "6379")
    db = int(os.environ.get("RT_REDIS_DB", "0"))
    password = os.environ.get("RT_REDIS_PASSWORD") or None
    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def build_config() -> Config:
    host = socket.gethostname()
    pid = os.getpid()
    return Config(
        redis_url=build_redis_url(),
        poll_ms=int(os.environ.get("UI_PROJECTOR_POLL_MS", "250")),
        stale_ms=int(os.environ.get("UI_PROJECTOR_STALE_MS", "5000")),
        lock_key=os.environ.get("UI_PROJECTOR_LOCK_KEY", "rt:ui:writer"),
        lock_ttl_ms=int(os.environ.get("UI_PROJECTOR_LOCK_TTL_MS", "10000")),
        lock_value=f"{host}:{pid}",
        interaction_state_key=UI_INTERACTION_STATE_KEY,
        page_context_key=UI_PAGE_CONTEXT_KEY,
        snapshot_keys=csv_env("UI_PROJECTOR_SNAPSHOT_KEYS", DEFAULT_SNAPSHOT_KEYS),
        page_keys=csv_env("UI_PROJECTOR_PAGE_KEYS", DEFAULT_PAGE_KEYS),
        focus_keys=csv_env("UI_PROJECTOR_FOCUS_KEYS", DEFAULT_FOCUS_KEYS),
        modal_keys=csv_env("UI_PROJECTOR_MODAL_KEYS", DEFAULT_MODAL_KEYS),
        browse_keys=csv_env("UI_PROJECTOR_BROWSE_KEYS", DEFAULT_BROWSE_KEYS),
        authority_keys=csv_env("UI_PROJECTOR_AUTHORITY_KEYS", DEFAULT_AUTHORITY_KEYS),
        result_keys=csv_env("UI_PROJECTOR_RESULT_KEYS", DEFAULT_RESULT_KEYS),
        page_context_keys=csv_env("UI_PROJECTOR_PAGE_CONTEXT_KEYS", DEFAULT_PAGE_CONTEXT_KEYS),
        system_health_keys=csv_env("UI_PROJECTOR_SYSTEM_HEALTH_KEYS", DEFAULT_SYSTEM_HEALTH_KEYS),
        event_timeout_ms=int(os.environ.get("UI_PROJECTOR_EVENT_TIMEOUT_MS", str(DEFAULT_EVENT_TIMEOUT_MS))),
        intent_debounce_ms=int(os.environ.get("UI_PROJECTOR_INTENT_DEBOUNCE_MS", str(DEFAULT_INTENT_DEBOUNCE_MS))),
    )


def configure_logging() -> None:
    level_name = os.environ.get("UI_PROJECTOR_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    configure_logging()
    config = build_config()
    projector = UIStateProjector(config)
    signal.signal(signal.SIGTERM, projector.stop)
    signal.signal(signal.SIGINT, projector.stop)
    try:
        projector.run()
    except GracefulExit:
        logging.getLogger("ui_state_projector").info("stopping ui state projector")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())