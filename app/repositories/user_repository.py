from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NationMember, User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_nation_members(self, nation_id: int) -> list[User]:
        result = await self.session.execute(
            select(User)
            .join(NationMember, NationMember.user_id == User.user_id)
            .where(
                NationMember.nation_id == nation_id,
                NationMember.is_active.is_(True),
            )
            .order_by(NationMember.joined_at.asc(), User.user_id.asc())
        )
        return list(result.scalars().all())

    async def update_balance(self, user_id: int, delta: Decimal) -> Decimal:
        user = await self.session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ValueError("کاربر پیدا نشد.")

        current = Decimal(str(user.xr_balance or Decimal("0")))
        user.xr_balance = current + Decimal(str(delta))
        await self.session.flush()
        return Decimal(str(user.xr_balance))
