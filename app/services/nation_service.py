from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ActivityType, CurrencyHolding, Nation, User, UserActivity


async def get_active_nations(session: AsyncSession, limit: int = 3) -> list[Nation]:
    async with session.begin():
        result = await session.execute(
            select(Nation)
            .where(Nation.is_active.is_(True))
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
        .where(
            Nation.is_active.is_(True),
            Nation.exchange_rate > nation.exchange_rate,
        )
    )
    return len(result.scalars().all()) + 1


async def create_nation(
    session: AsyncSession,
    founder_user_id: int,
    group_id: int,
    nation_name: str,
    currency_code: str,
    founder_username: str | None = None,
) -> Nation:
    async with session.begin():
        user_result = await session.execute(
            select(User)
            .where(User.user_id == founder_user_id)
            .with_for_update()
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            raise ValueError("اول باید وارد بازی بشی.")

        holding_result = await session.execute(
            select(CurrencyHolding.id)
            .where(CurrencyHolding.user_id == founder_user_id)
            .limit(1)
        )
        if holding_result.scalar_one_or_none() is None:
            raise ValueError("اول باید وارد بازی بشی.")

        if user.role == "founder":
            raise ValueError("هر معامله‌گر فقط یه ملت می‌تونه بسازه.")

        group_result = await session.execute(
            select(Nation)
            .where(
                Nation.group_id == group_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
        )
        if group_result.scalar_one_or_none() is not None:
            raise ValueError("این گروه قبلاً پایتخت یک ملت شده.")

        currency_result = await session.execute(
            select(Nation)
            .where(Nation.currency_code == currency_code)
            .with_for_update()
        )
        if currency_result.scalar_one_or_none() is not None:
            raise ValueError("این کد ارز قبلاً استفاده شده.")

        nation = Nation(
            group_id=group_id,
            name=nation_name,
            currency_code=currency_code,
            founder_user_id=founder_user_id,
            exchange_rate=Decimal("1.0000"),
            rate_prev=Decimal("1.0000"),
            rate_24h_open=Decimal("1.0000"),
            trade_volume_24h=Decimal("0"),
            active_members_24h=1,
            nation_rank=None,
            last_rate_update=datetime.utcnow(),
            member_count=1,
            is_active=True,
        )
        session.add(nation)
        await session.flush()

        user.role = "founder"
        user.xr_balance += Decimal("1000.00")
        user.balance = Decimal("1000.00")
        user.home_nation_id = nation.nation_id

        session.add(
            CurrencyHolding(
                user_id=founder_user_id,
                nation_id=nation.nation_id,
                amount=Decimal("1000.0000"),
            )
        )
        session.add(
            UserActivity(
                user_id=founder_user_id,
                nation_id=nation.nation_id,
                activity_type=ActivityType.LOGIN,
            )
        )

        return nation
