from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StateType, StorageKey

from app.core.redis import ServerlessRedis


class UpstashFSMStorage(BaseStorage):
    """FSM storage backed by Upstash REST through the shared Redis adapter."""

    def __init__(self, redis: ServerlessRedis) -> None:
        self.redis = redis
        self.key_builder = DefaultKeyBuilder(
            prefix="opex-fsm",
            with_bot_id=True,
            with_business_connection_id=True,
            with_destiny=True,
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        redis_key = self.key_builder.build(key, "state")
        if state is None:
            await self.redis.delete(redis_key)
            return
        value = state.state if hasattr(state, "state") else str(state)
        await self.redis.set(redis_key, value)

    async def get_state(self, key: StorageKey) -> str | None:
        value = await self.redis.get(self.key_builder.build(key, "state"))
        return None if value is None else str(value)

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        redis_key = self.key_builder.build(key, "data")
        await self.redis.set(redis_key, json.dumps(dict(data), ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        value = await self.redis.get(self.key_builder.build(key, "data"))
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def close(self) -> None:
        await self.redis.aclose()
