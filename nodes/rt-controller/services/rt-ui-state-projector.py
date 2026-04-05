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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import redis
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
}


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


class UIStateProjector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.log = logging.getLogger("ui_state_projector")
        self.redis_client = self._connect()
        self.running = True
        self.last_projection: Dict[str, str] = {}
        self.last_optional_keys: set[str] = set()

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
        self.log.info("starting ui state projector")
        while self.running:
            started = time.monotonic()
            try:
                if not self._acquire_writer_lock():
                    time.sleep(min(self.config.poll_ms / 1000.0, 1.0))
                    continue

                upstream = self._read_upstream_state()
                projection, optional_keys = self._build_projection(upstream)
                self._apply_projection(projection, optional_keys)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                self.log.warning("redis error: %s", exc)
                self.reconnect()
            except GracefulExit:
                raise
            except Exception:
                self.log.exception("unexpected projector loop failure")

            elapsed = time.monotonic() - started
            sleep_for = max(0.0, (self.config.poll_ms / 1000.0) - elapsed)
            time.sleep(sleep_for)

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

        # Primary authoritative input: committed controller interaction state.
        interaction, interaction_key = self._read_first_object([self.config.interaction_state_key])
        if interaction is not None:
            upstream.update(interaction)
            upstream["_sources"]["interaction_state"] = interaction_key

        # Optional page-family context source, e.g. rt:pota:context
        page_context_from_primary, page_context_key = self._read_first_object([self.config.page_context_key])
        if page_context_from_primary is not None:
            upstream["page_context"] = page_context_from_primary
            upstream["_sources"]["page_context_primary"] = page_context_key

        # Legacy fallback consolidated snapshot.
        if "page" not in upstream:
            snapshot, snapshot_key = self._read_first_object(self.config.snapshot_keys)
            if snapshot is not None:
                for key, value in snapshot.items():
                    upstream.setdefault(key, value)
                upstream["_sources"]["snapshot"] = snapshot_key

        # Page
        page = self._extract_scalar(upstream, ["page", "current_page"])
        if page is None:
            page, page_key = self._read_first_scalar(self.config.page_keys)
            if page_key:
                upstream["_sources"]["page"] = page_key
        if page is not None:
            upstream["page"] = page

        # Focus
        focus = self._extract_scalar(upstream, ["focus", "focused_panel", "focus_panel"])
        if focus is None:
            focus, focus_key = self._read_first_scalar(self.config.focus_keys)
            if focus_key:
                upstream["_sources"]["focus"] = focus_key
        if focus is not None:
            upstream["focus"] = focus

        # Modal
        modal = self._extract_object(upstream, ["modal", "active_modal"])
        if modal is None:
            modal, modal_key = self._read_first_object(self.config.modal_keys)
            if modal_key:
                upstream["_sources"]["modal"] = modal_key
        if modal is not None:
            upstream["modal"] = modal

        # Browse
        browse = self._extract_object(upstream, ["browse", "browse_state"])
        if browse is None:
            browse, browse_key = self._read_first_object(self.config.browse_keys)
            if browse_key:
                upstream["_sources"]["browse"] = browse_key
        if browse is not None:
            upstream["browse"] = browse

        # Authority
        authority = self._extract_object(upstream, ["authority", "ui_authority"])
        if authority is None:
            authority, authority_key = self._read_first_object(self.config.authority_keys)
            if authority_key:
                upstream["_sources"]["authority"] = authority_key
        if authority is not None:
            upstream["authority"] = authority

        # Last result
        last_result = self._extract_object(upstream, ["last_result", "result"])
        if last_result is None:
            last_result, result_key = self._read_first_object(self.config.result_keys)
            if result_key:
                upstream["_sources"]["last_result"] = result_key
        if last_result is not None:
            upstream["last_result"] = last_result

        # Page context fallback list
        if "page_context" not in upstream:
            page_context, context_key = self._read_first_object(self.config.page_context_keys)
            if context_key:
                upstream["_sources"]["page_context"] = context_key
            if page_context is not None:
                upstream["page_context"] = page_context

        # System health
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

        projection: Dict[str, str] = {
            PROJECTED_KEYS["layer"]: layer,
            PROJECTED_KEYS["authority"]: self._json(authority),
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

        # Fail closed for ambiguous UI semantics.
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

        # First-pass scoping rule:
        # only project POTA context while the active page is the POTA page.
        if page != "pota":
            return None

        normalized = dict(context)
        normalized["page"] = page
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

    def _apply_projection(self, projection: Dict[str, str], optional_keys: set[str]) -> None:
        desired_keys = set(projection.keys())
        managed_keys = set(PROJECTED_KEYS.values()) | self.last_optional_keys | optional_keys
        stale_keys = sorted(managed_keys - desired_keys)

        if projection == self.last_projection and optional_keys == self.last_optional_keys:
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
        self.last_optional_keys = set(optional_keys)
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