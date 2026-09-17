from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation


async def get_active_nations(session: AsyncSession, limit: int = 3) -> list[Nation]:
    result = await session.execute(
        select(Nation)
        .order_by(desc(Nation.member_count), Nation.nation_id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_nation_rank(session: AsyncSession, nation_id: int) -> int:
    nation = await session.get(Nation, nation_id)
    if nation is None:
        return 0

    result = await session.execute(
        select(Nation.nation_id)
        .where(Nation.member_count > nation.member_count)
        .order_by(desc(Nation.member_count), Nation.nation_id.asc())
    )
    return len(result.scalars().all()) + 1
