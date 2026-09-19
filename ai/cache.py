"""Redis cache for AI responses and short-term bot continuity."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from config import settings

logger = logging.getLogger("opex.ai.cache")


class AICache:
    """Async Redis cache with fail-open behavior."""

    def __init__(self) -> None:
        self._redis: Redis | None = None

    def _client(self) -> Redis | None:
        """Lazily create the Redis client."""
        if not settings.redis_url:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    async def get(self, key: str) -> str | None:
        """Return a cached AI reply or None when cache is unavailable."""
        client = self._client()
        if client is None:
            return None
        try:
            value = await client.get(key)
            return value if isinstance(value, str) else None
        except Exception:
            logger.exception("AI cache read failed")
            return None

    async def set(self, key: str, value: str) -> None:
        """Cache an AI reply for the configured TTL."""
        client = self._client()
        if client is None:
            return
        try:
            await client.setex(key, settings.ai_cache_ttl_seconds, value)
        except Exception:
            logger.exception("AI cache write failed")

    async def get_last_bot_message(self, user_id: int) -> str | None:
        """Return the most recent bot message recorded by the AI layer."""
        return await self.get(f"opex:ai:last-bot:{int(user_id)}")

    async def set_last_bot_message(self, user_id: int, message: str) -> None:
        """Record the latest bot reply for short-term continuity."""
        client = self._client()
        if client is None:
            return
        try:
            await client.set(
                f"opex:ai:last-bot:{int(user_id)}",
                message,
                ex=settings.ai_last_message_ttl_seconds,
            )
        except Exception:
            logger.exception("AI continuity cache write failed")

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Read a JSON object from the cache."""
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in AI cache key=%s", key)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def close(self) -> None:
        """Close the underlying Redis client."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


_cache = AICache()


async def get_cached_response(key: str) -> str | None:
    """Read a cached AI reply."""
    return await _cache.get(key)


async def set_cached_response(key: str, value: str) -> None:
    """Write a cached AI reply."""
    await _cache.set(key, value)


async def get_last_bot_message(user_id: int) -> str | None:
    """Read the last bot message tracked by the AI layer."""
    return await _cache.get_last_bot_message(user_id)


async def remember_bot_message(user_id: int, message: str) -> None:
    """Persist one bot message for continuity."""
    await _cache.set_last_bot_message(user_id, message)


async def close_cache() -> None:
    """Close the shared Redis client."""
    await _cache.close()


__all__ = [
    "AICache",
    "close_cache",
    "get_cached_response",
    "get_last_bot_message",
    "remember_bot_message",
    "set_cached_response",
]