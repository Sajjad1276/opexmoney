from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import wraps
from typing import Any, TypeVar
from uuid import UUID

import orjson
from pydantic import BaseModel
from redis.asyncio import Redis

logger = logging.getLogger("opex.admin.cache")

T = TypeVar("T")

TTL_STATS = 60
TTL_ECONOMY_HEALTH = 120
TTL_PLAYERS = 30
TTL_PLAYER_DETAIL = 15
TTL_NATIONS = 30
TTL_NATION_DETAIL = 20
TTL_MARKET_STATES = 30
TTL_RATE_HISTORY = 60
TTL_BEHAVIOR = 120
TTL_WORLD_EVENTS = 30
TTL_WARS = 20
TTL_PROPOSALS = 30
TTL_GOVERNANCE_LEDGER = 60
TTL_RULE_OVERRIDES = 60
TTL_TRANSACTIONS = 15


def _default(obj: Any):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _result_payload(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in result
        ]
    if isinstance(result, dict):
        return {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in result.items()
        }
    return result


def _cache_key(prefix: str, kwargs: dict[str, Any]) -> str:
    ignored = {"db", "redis", "admin_user", "request"}
    filtered = {key: value for key, value in kwargs.items() if key not in ignored}
    raw = str(filtered).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:8]
    return f"admin:{prefix}:{digest}"


def cached(ttl: int, key_prefix: str):
    def decorator(func: Callable[..., Awaitable[T]]):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            redis: Redis | None = kwargs.get("redis")
            key = _cache_key(key_prefix, kwargs)

            if redis is not None:
                try:
                    cached_value = await redis.get(key)
                    if cached_value is not None:
                        return orjson.loads(cached_value)
                except Exception:
                    logger.warning("ADMIN_CACHE|get_failed|key=%s", key, exc_info=True)

            result = await func(*args, **kwargs)
            payload = _result_payload(result)

            if redis is not None:
                try:
                    await redis.set(key, orjson.dumps(payload, default=_default), ex=ttl)
                except Exception:
                    logger.warning("ADMIN_CACHE|set_failed|key=%s", key, exc_info=True)

            return result

        return wrapper

    return decorator


async def invalidate_prefix(redis: Redis | None, prefix: str) -> int:
    if redis is None:
        return 0

    deleted = 0
    try:
        keys = []
        async for key in redis.scan_iter(match=f"admin:{prefix}:*"):
            keys.append(key)
        if keys:
            deleted = await redis.delete(*keys)
    except Exception:
        logger.warning("ADMIN_CACHE|invalidate_failed|prefix=%s", prefix, exc_info=True)
    return int(deleted)
