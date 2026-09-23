from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from config import settings


class ServerlessRedis:
    """Small async Redis compatibility layer for Vercel/Upstash REST and redis-py."""

    def __init__(self, backend: Any, *, rest: bool) -> None:
        self._backend = backend
        self._rest = rest

    async def ping(self) -> bool:
        result = await self._backend.ping()
        return bool(result is True or result == "PONG" or result == b"PONG")

    async def get(self, key: str) -> Any:
        return await self._backend.get(key)

    async def set(self, key: str, value: Any, **kwargs: Any) -> Any:
        return await self._backend.set(key, value, **kwargs)

    async def setex(self, key: str, seconds: int, value: Any) -> Any:
        if self._rest:
            return await self._backend.set(key, value, ex=seconds)
        return await self._backend.setex(key, seconds, value)

    async def delete(self, *keys: str) -> Any:
        return await self._backend.delete(*keys)

    async def expire(self, key: str, seconds: int, **kwargs: Any) -> Any:
        return await self._backend.expire(key, seconds, **kwargs)

    async def incr(self, key: str) -> Any:
        return await self._backend.incr(key)

    async def rpush(self, key: str, *values: Any) -> Any:
        return await self._backend.rpush(key, *values)

    async def lrange(self, key: str, start: int, end: int) -> Any:
        return await self._backend.lrange(key, start, end)

    async def ltrim(self, key: str, start: int, end: int) -> Any:
        return await self._backend.ltrim(key, start, end)

    async def llen(self, key: str) -> Any:
        return await self._backend.llen(key)

    async def hget(self, key: str, field: str) -> Any:
        return await self._backend.hget(key, field)

    async def hgetall(self, key: str) -> Any:
        return await self._backend.hgetall(key)

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        *,
        mapping: Mapping[str, Any] | None = None,
    ) -> Any:
        if mapping is not None:
            if self._rest:
                return await self._backend.hset(key, values=dict(mapping))
            return await self._backend.hset(key, mapping=dict(mapping))
        if field is None:
            raise ValueError("hset requires field or mapping")
        if self._rest:
            return await self._backend.hset(key, field, value)
        return await self._backend.hset(key, field, value)

    async def hincrby(self, key: str, field: str, amount: int = 1) -> Any:
        return await self._backend.hincrby(key, field, amount)

    async def hincrbyfloat(
        self,
        key: str,
        field: str,
        amount: float = 1.0,
    ) -> Any:
        method = getattr(self._backend, "hincrbyfloat", None)
        if method is not None:
            return await method(key, field, amount)
        return await self._backend.execute(
            command=["HINCRBYFLOAT", key, field, amount]
        )

    async def scan_iter(
        self,
        *,
        match: str | None = None,
        count: int = 100,
    ) -> AsyncIterator[str]:
        cursor: int | str = 0
        while True:
            cursor, keys = await self._backend.scan(
                cursor,
                match=match,
                count=count,
            )
            for key in keys:
                yield key
            if int(cursor) == 0:
                break

    async def blpop(self, key: str, timeout: int = 0) -> Any:
        if self._rest:
            if timeout:
                raise RuntimeError("Blocking Redis operations are unavailable on Upstash REST")
            value = await self._backend.lpop(key)
            return (key, value) if value is not None else None
        return await self._backend.blpop(key, timeout)

    async def aclose(self) -> None:
        if self._backend is None:
            return
        close = getattr(self._backend, "aclose", None)
        if close is None:
            close = getattr(self._backend, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._backend = None


def create_redis_client() -> ServerlessRedis | None:
    if settings.upstash_redis_rest_url and settings.upstash_redis_rest_token:
        from upstash_redis.asyncio import Redis

        return ServerlessRedis(
            Redis(
                url=settings.upstash_redis_rest_url,
                token=settings.upstash_redis_rest_token,
                allow_telemetry=False,
            ),
            rest=True,
        )

    if settings.redis_url:
        from redis.asyncio import Redis

        return ServerlessRedis(
            Redis.from_url(
                settings.redis_url,
                decode_responses=True,
            ),
            rest=False,
        )

    return None


__all__ = ["ServerlessRedis", "create_redis_client"]
