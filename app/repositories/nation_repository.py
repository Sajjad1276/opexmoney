from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, NationMember, NationMemberRole, User


class NationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, nation_id: int) -> Nation | None:
        return await self.session.get(Nation, nation_id)

    async def get_by_currency_code(self, code: str) -> Nation | None:
        return await self.session.scalar(
            select(Nation).where(Nation.currency_code == code).limit(1)
        )

    async def get_active_nations(self, limit: int = 100) -> list[Nation]:
        safe_limit = max(1, min(int(limit), 100))
        result = await self.session.execute(
            select(Nation)
            .where(Nation.is_active.is_(True))
            .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
            .limit(safe_limit)
        )
        return list(result.scalars().all())

    async def get_members(self, nation_id: int) -> list[User]:
        result = await self.session.execute(
            select(User)
            .join(
                NationMember,
                NationMember.user_id == User.user_id,
            )
            .where(
                NationMember.nation_id == nation_id,
                NationMember.is_active.is_(True),
            )
            .order_by(NationMember.joined_at.asc(), User.user_id.asc())
        )
        return list(result.scalars().all())

    async def get_member_count(self, nation_id: int) -> int:
        count = await self.session.scalar(
            select(func.count(NationMember.id)).where(
                NationMember.nation_id == nation_id,
                NationMember.is_active.is_(True),
            )
        )
        return int(count or 0)

    async def is_member(self, user_id: int, nation_id: int) -> bool:
        result = await self.session.scalar(
            select(NationMember.id)
            .where(
                NationMember.user_id == user_id,
                NationMember.nation_id == nation_id,
                NationMember.is_active.is_(True),
            )
            .limit(1)
        )
        return result is not None
