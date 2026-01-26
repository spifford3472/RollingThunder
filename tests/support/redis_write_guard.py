from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class OwnershipRule:
    pattern: str
    fields: Dict[str, List[str]]  # field -> allowed writers OR "*" -> allowed writers


class OwnershipRegistry:
    def __init__(self, rules: List[OwnershipRule]) -> None:
        self._rules = rules

    def allowed_writers(self, key: str, field: str) -> Optional[List[str]]:
        """
        Returns list of allowed writers if we have a rule; otherwise None (unknown key).
        If the key pattern is known but field is not listed: returns [] (strict).
        """
        for r in self._rules:
            if fnmatch.fnmatch(key, r.pattern):
                if field in r.fields:
                    return r.fields[field]
                if "*" in r.fields:
                    return r.fields["*"]
                return []
        return None


class RedisWriteGuard:
    """
    Wrap a redis client and enforce "only the owner writes fields" for HSET/HMSET.
    Intended for unit tests by injecting this instead of a real redis client.
    """
    def __init__(
        self,
        redis_client: Any,
        registry: OwnershipRegistry,
        writer_id: str,
        strict_unknown_keys: bool = True,
    ):
        self._r = redis_client
        self._registry = registry
        self._writer_id = writer_id
        self._strict_unknown_keys = strict_unknown_keys

    def hset(self, name: str, key: Optional[str] = None, value: Optional[Any] = None, mapping: Optional[Dict[str, Any]] = None):
        writes: Dict[str, Any] = {}
        if mapping:
            writes.update(mapping)
        elif key is not None:
            writes[key] = value
        else:
            raise ValueError("hset must be called with (key,value) or mapping")

        self._enforce(name, writes.keys())
        return self._r.hset(name=name, mapping=writes)

    def hmset(self, name: str, mapping: Dict[str, Any]):
        # support legacy usage patterns
        self._enforce(name, mapping.keys())
        return self._r.hset(name=name, mapping=mapping)

    def _enforce(self, redis_key: str, fields: Iterable[str]) -> None:
        for f in fields:
            allowed = self._registry.allowed_writers(redis_key, f)

            if allowed is None:
                if self._strict_unknown_keys:
                    raise AssertionError(
                        f"[STATE_CONTRACT] writer='{self._writer_id}' wrote unknown key='{redis_key}' field='{f}'"
                    )
                continue

            if self._writer_id not in allowed:
                raise AssertionError(
                    f"[STATE_CONTRACT] writer='{self._writer_id}' is NOT allowed to write key='{redis_key}' field='{f}'. "
                    f"Allowed={allowed}"
                )

    def __getattr__(self, item: str):
        return getattr(self._r, item)
