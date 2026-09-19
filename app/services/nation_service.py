from __future__ import annotations

import secrets

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationLog,
    NationMember,
    NationMemberRole,
    User,
    UserActivity,
)


async def get_active_nations(session: AsyncSession, limit: int = 3) -> list[Nation]:
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
    currency_code: str
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

        if not (user.username or "").strip():
            raise ValueError("اول باید اسم معامله‌گرت رو ثبت کنی.")

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
            join_policy="OPEN",
            personality="neutral",
            invite_code=f"OPX-{secrets.token_urlsafe(12)}",
            treasury=Decimal("0.00"),
        )
        session.add(nation)
        try:
            await session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(constraint, "constraint_name", None)
            if constraint_name == "uq_nations_currency_code":
                raise ValueError("این کد ارز همزمان توسط ملت دیگری ثبت شد.") from exc
            if constraint_name == "uq_nations_active_group":
                raise ValueError("این گروه همزمان توسط ملت دیگری ثبت شد.") from exc
            raise

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
            NationMember(
                nation_id=nation.nation_id,
                user_id=founder_user_id,
                role=NationMemberRole.FOUNDER,
                is_active=True,
            )
        )
        session.add(
            NationLog(
                nation_id=nation.nation_id,
                actor_id=founder_user_id,
                action_type="MEMBER_JOIN",
                target_id=founder_user_id,
                event_metadata={
                    "role": NationMemberRole.FOUNDER.value,
                    "source": "nation_creation",
                },
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
