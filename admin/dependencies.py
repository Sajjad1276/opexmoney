from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


async def get_redis(request: Request) -> Redis | None:
    return getattr(request.app.state, "redis", None)


def pagination_params(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, int]:
    return {"page": page, "limit": limit}


Pagination = Depends(pagination_params)


def page_count(total: int, limit: int) -> int:
    return (total + limit - 1) // limit if total else 0
