# nodes/rt-controller/redis_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import redis


class RedisConnectError(RuntimeError):
    pass


@dataclass(frozen=True)
class RedisConnInfo:
    host: str
    port: int
    db: int
    username: Optional[str]
    password: Optional[str]
    socket_timeout_sec: float


def _int_or(default: int, value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float_or(default: float, value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return default


def resolve_redis_conn_info(cfg: Dict[str, Any]) -> RedisConnInfo:
    """
    Config-first resolution.

    Reads from:
      globals.state.redis.host
      globals.state.redis.port
      globals.state.redis.db
      globals.state.redis.username
      globals.state.redis.password
      globals.state.redis.socketTimeoutSec

    If absent, defaults to localhost:6379 db 0.
    """
    gs = (cfg.get("globals") or {}).get("state") or {}
    r = (gs.get("redis") or {}) if isinstance(gs, dict) else {}

    host = r.get("host") if isinstance(r, dict) else None
    port = r.get("port") if isinstance(r, dict) else None
    db = r.get("db") if isinstance(r, dict) else None

    username = r.get("username") if isinstance(r, dict) else None
    password = r.get("password") if isinstance(r, dict) else None
    sto = r.get("socketTimeoutSec") if isinstance(r, dict) else None

    return RedisConnInfo(
        host=str(host) if host else "127.0.0.1",
        port=_int_or(6379, port),
        db=_int_or(0, db),
        username=str(username) if username else None,
        password=str(password) if password else None,
        socket_timeout_sec=_float_or(1.5, sto),
    )


def connect_and_ping(info: RedisConnInfo) -> redis.Redis:
    """
    Connect and PING. Raises RedisConnectError if anything fails.
    """
    try:
        client = redis.Redis(
            host=info.host,
            port=info.port,
            db=info.db,
            username=info.username,
            password=info.password,
            socket_timeout=info.socket_timeout_sec,
            socket_connect_timeout=info.socket_timeout_sec,
            decode_responses=True,  # easier for later state keys
        )
        ok = client.ping()
        if ok is not True:
            raise RedisConnectError(f"Redis PING returned {ok!r}")
        return client
    except Exception as e:
        raise RedisConnectError(
            f"Unable to connect to Redis at {info.host}:{info.port} db={info.db}: {e}"
        )
