from __future__ import annotations

import logging

from redis.asyncio import Redis

from config import settings

logger = logging.getLogger(__name__)

_redis: Redis | None = None


def get_redis() -> Redis | None:
    """Return the shared Redis client used by the application Redis URL."""
    global _redis

    if not settings.redis_url:
        return None

    if _redis is None:
        try:
            _redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        except Exception:
            logger.exception("Could not create shared Redis client")
            return None

    return _redis


async def close_redis() -> None:
    global _redis

    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            logger.exception("Could not close shared Redis client")
        finally:
            _redis = None


__all__ = ["close_redis", "get_redis"]
