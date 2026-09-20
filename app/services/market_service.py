from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation


async def get_tradeable_nation(
    session: AsyncSession,
    nation_id: int,
    *,
    lock: bool = False,
) -> Nation | None:
    """Return a currently active market nation.

    Human and AI nations both trade through the same market surface, while
    inactive nations are excluded at the service boundary.
    """
    statement = (
        select(Nation)
        .where(
            Nation.nation_id == nation_id,
            Nation.is_active.is_(True),
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)
