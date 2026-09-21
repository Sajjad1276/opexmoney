from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from config import settings

logger = logging.getLogger("opexmoney.support.telemetry")

_MAX_EVENTS = 12
_MAX_ERRORS = 5

_events: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_MAX_EVENTS))
_errors: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_MAX_ERRORS))
_redis: Redis | None = None


def _redis_client() -> Redis | None:
    global _redis
    if not settings.redis_url:
        return None
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _loads(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def record_event(
    user_id: int,
    event: str,
    fsm_state: str | None = None,
    outcome: str = "begin",
) -> None:
    item = {
        "timestamp": _now(),
        "event": _safe_text(event, 180),
        "state": _safe_text(fsm_state or "", 180) or None,
        "outcome": _safe_text(outcome, 30),
    }
    _events[int(user_id)].append(item)

    client = _redis_client()
    if client is None:
        return
    try:
        key = f"opex:support:events:{int(user_id)}"
        await client.rpush(key, json.dumps(item, ensure_ascii=False))
        await client.ltrim(key, -_MAX_EVENTS, -1)
        await client.expire(key, settings.support_telemetry_ttl_seconds)
    except Exception:
        logger.debug("Support event Redis write failed", exc_info=True)


async def record_error(
    user_id: int,
    source: str,
    exception: BaseException,
    event: str | None = None,
) -> None:
    item = {
        "timestamp": _now(),
        "source": _safe_text(source, 80),
        "event": _safe_text(event or "", 180) or None,
        "error_type": type(exception).__name__,
        "message": _safe_text(str(exception), 500),
    }
    _errors[int(user_id)].append(item)

    client = _redis_client()
    if client is None:
        return
    try:
        key = f"opex:support:errors:{int(user_id)}"
        await client.rpush(key, json.dumps(item, ensure_ascii=False))
        await client.ltrim(key, -_MAX_ERRORS, -1)
        await client.expire(key, settings.support_telemetry_ttl_seconds)
    except Exception:
        logger.debug("Support error Redis write failed", exc_info=True)


async def get_recent_telemetry(user_id: int) -> dict[str, list[dict[str, Any]]]:
    events = list(_events.get(int(user_id), ()))
    errors = list(_errors.get(int(user_id), ()))

    client = _redis_client()
    if client is not None:
        try:
            event_rows = await client.lrange(f"opex:support:events:{int(user_id)}", 0, -1)
            error_rows = await client.lrange(f"opex:support:errors:{int(user_id)}", 0, -1)
            redis_events = [
                parsed
                for raw in event_rows
                for parsed in [_loads(raw)]
                if parsed is not None
            ]
            redis_errors = [
                parsed
                for raw in error_rows
                for parsed in [_loads(raw)]
                if parsed is not None
            ]
            if redis_events:
                events = redis_events
            if redis_errors:
                errors = redis_errors
        except Exception:
            logger.debug("Support telemetry Redis read failed", exc_info=True)

    return {
        "events": events[-_MAX_EVENTS:],
        "errors": errors[-_MAX_ERRORS:],
    }


async def close_support_telemetry() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


__all__ = [
    "close_support_telemetry",
    "get_recent_telemetry",
    "record_error",
    "record_event",
]
