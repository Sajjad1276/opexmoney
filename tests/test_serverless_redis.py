from __future__ import annotations

import pytest

from app.core.serverless_redis import ServerlessRedis
from app.storage.upstash_fsm import UpstashFSMStorage
from aiogram.fsm.storage.base import StorageKey


class FakeBackend:
    def __init__(self) -> None:
        self.values = {}
        self.hashes = {}
        self.scan_calls = 0

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value
        return True

    async def delete(self, *keys):
        return sum(1 for key in keys if self.values.pop(key, None) is not None)

    async def rpush(self, key, *values):
        self.values.setdefault(key, [])
        self.values[key].extend(values)
        return len(self.values[key])

    async def lrange(self, key, start, end):
        rows = self.values.get(key, [])
        return rows[start : None if end == -1 else end + 1]

    async def ltrim(self, key, start, end):
        self.values[key] = await self.lrange(key, start, end)
        return True

    async def expire(self, key, seconds, **kwargs):
        return True

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def hset(self, key, field=None, value=None, values=None, **kwargs):
        target = self.hashes.setdefault(key, {})
        if values is not None:
            target.update(values)
            return len(values)
        target[field] = value
        return 1

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def hincrby(self, key, field, amount):
        target = self.hashes.setdefault(key, {})
        target[field] = int(target.get(field, 0)) + amount
        return target[field]

    async def scan(self, cursor, match=None, count=100):
        self.scan_calls += 1
        if self.scan_calls == 1:
            return 0, ["opex:a", "opex:b"]
        return 0, []

    async def ping(self):
        return "PONG"

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_serverless_redis_adapter_maps_common_commands():
    backend = FakeBackend()
    redis = ServerlessRedis(backend, rest=True)

    await redis.hset("pressure:1", mapping={"buy": 2})
    await redis.hset("pressure:1", "sell", 3)

    assert await redis.hgetall("pressure:1") == {"buy": 2, "sell": 3}
    assert await redis.get("missing") is None
    assert await redis.ping() is True

    keys = [key async for key in redis.scan_iter(match="opex:*")]
    assert keys == ["opex:a", "opex:b"]


@pytest.mark.asyncio
async def test_upstash_fsm_storage_round_trip():
    backend = FakeBackend()
    redis = ServerlessRedis(backend, rest=True)
    storage = UpstashFSMStorage(redis)
    key = StorageKey(
        bot_id=100,
        chat_id=200,
        user_id=300,
    )

    await storage.set_state(key, "demo:state")
    await storage.set_data(key, {"foo": "bar", "count": 2})

    assert await storage.get_state(key) == "demo:state"
    assert await storage.get_data(key) == {"foo": "bar", "count": 2}

    await storage.set_state(key, None)
    assert await storage.get_state(key) is None
    await storage.close()
