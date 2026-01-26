import json
from pathlib import Path

import pytest

from tests.support.redis_write_guard import OwnershipRegistry, OwnershipRule, RedisWriteGuard


class FakeRedis:
    def __init__(self):
        self.data = {}

    def hset(self, name: str, mapping: dict):
        self.data.setdefault(name, {}).update(mapping)
        return 1


def load_registry() -> OwnershipRegistry:
    path = Path("config/state_ownership.json")
    raw = json.loads(path.read_text())
    rules = []
    for entry in raw["keys"]:
        rules.append(OwnershipRule(pattern=entry["pattern"], fields=entry["fields"]))
    return OwnershipRegistry(rules)


def test_heartbeat_cannot_write_node_status():
    """
    Phase 14 guardrail:
    heartbeat must not set rt:nodes:* status (presence ingestor owns status derivation + TTL).
    """
    r = FakeRedis()
    reg = load_registry()
    guarded = RedisWriteGuard(r, reg, writer_id="heartbeat")

    with pytest.raises(AssertionError):
        guarded.hset("rt:nodes:rt-controller", mapping={"status": "online"})


def test_presence_ingestor_can_write_status():
    r = FakeRedis()
    reg = load_registry()
    guarded = RedisWriteGuard(r, reg, writer_id="node_presence_ingestor")

    guarded.hset("rt:nodes:rt-radio", mapping={"status": "stale", "age_sec": 12})
