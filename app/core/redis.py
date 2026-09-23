from __future__ import annotations

import logging

from app.core.serverless_redis import ServerlessRedis, create_redis_client

logger = logging.getLogger(__name__)

_redis: ServerlessRedis | None = None


def get_redis() -> ServerlessRedis | None:
    global _redis

    if _redis is None:
        try:
            _redis = create_redis_client()
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


__all__ = ["ServerlessRedis", "close_redis", "get_redis"]
