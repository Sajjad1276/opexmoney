from __future__ import annotations

import secrets

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.user_service import sync_user_balance

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationLog,
    NationMember,
    NationMemberRole,
    Transaction,
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


async def convert_holding_to_xr(
    user: User,
    nation: Nation,
    session: AsyncSession,
) -> dict[str, Decimal] | None:
    """
    Liquidate one user's local-currency holding into XR.

    The caller owns the surrounding transaction and should already hold the
    user/nation locks. The holding itself is locked here before mutation.
    """
    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user.user_id,
            CurrencyHolding.nation_id == nation.nation_id,
        )
        .with_for_update()
    )
    if holding is None:
        return None

    amount = Decimal(str(holding.amount or Decimal("0")))
    if amount <= 0:
        return None

    rate = Decimal(str(nation.exchange_rate or Decimal("0")))
    if rate <= 0:
        rate = Decimal(str(nation.rate_prev or Decimal("0")))
    if rate <= 0:
        rate = Decimal("1.0")

    xr_value = amount * rate

    user.xr_balance = Decimal(str(user.xr_balance or Decimal("0"))) + xr_value
    holding.amount = Decimal("0")

    # ECONOMIC RULE: holdings liquidate to XR on kick/dissolve wherever you touch this logic
    session.add(
        Transaction(
            user_id=user.user_id,
            nation_id=nation.nation_id,
            transaction_type="liquidate",
            spend_xr=xr_value,
            amount=amount,
            fee_xr=Decimal("0"),
            rate=rate,
        )
    )
    session.add(
        NationLog(
            nation_id=nation.nation_id,
            actor_id=None,
            action_type="HOLDING_LIQUIDATED",
            target_id=user.user_id,
            event_metadata={
                "amount": str(amount),
                "rate": str(rate),
                "xr_received": str(xr_value),
            },
        )
    )

    if user.home_nation_id == nation.nation_id:
        await sync_user_balance(session, user.user_id)

    return {
        "amount": amount,
        "rate": rate,
        "xr_received": xr_value,
    }


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
        await session.flush()

        # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
        user.home_nation_id = nation.nation_id
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
