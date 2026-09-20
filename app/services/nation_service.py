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


async def _repair_membership(
    session: AsyncSession,
    user: User,
    nation: Nation,
    *,
    role: NationMemberRole,
) -> str:
    member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation.nation_id,
            NationMember.user_id == user.user_id,
        )
        .with_for_update()
        .limit(1)
    )

    if member is None:
        member = NationMember(
            nation_id=nation.nation_id,
            user_id=user.user_id,
            role=role,
            is_active=True,
        )
        session.add(member)
    else:
        member.role = role
        member.is_active = True

    user.home_nation_id = nation.nation_id
    if role == NationMemberRole.FOUNDER:
        user.role = NationMemberRole.FOUNDER.value
    await session.flush()
    return role.value


async def get_user_active_nation_context(
    session: AsyncSession,
    user_id: int,
    *,
    repair: bool = True,
) -> tuple[Nation, str, str] | None:
    """Resolve the canonical active nation and membership role."""

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return None

    if user.home_nation_id is not None:
        home_nation = await session.scalar(
            select(Nation)
            .where(
                Nation.nation_id == user.home_nation_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
        )

        if home_nation is not None:
            member = await session.scalar(
                select(NationMember)
                .where(
                    NationMember.user_id == user_id,
                    NationMember.nation_id == home_nation.nation_id,
                    NationMember.is_active.is_(True),
                )
                .with_for_update()
                .limit(1)
            )
            if member is not None:
                return (
                    home_nation,
                    member.role.value
                    if isinstance(member.role, NationMemberRole)
                    else str(member.role),
                    "home_membership",
                )

            if repair and home_nation.founder_user_id == user_id:
                role = await _repair_membership(
                    session,
                    user,
                    home_nation,
                    role=NationMemberRole.FOUNDER,
                )
                return home_nation, role, "repaired_founder"

            holding = await session.scalar(
                select(CurrencyHolding)
                .where(
                    CurrencyHolding.user_id == user_id,
                    CurrencyHolding.nation_id == home_nation.nation_id,
                )
                .with_for_update()
                .limit(1)
            )
            if repair and holding is not None:
                role = await _repair_membership(
                    session,
                    user,
                    home_nation,
                    role=NationMemberRole.CITIZEN,
                )
                return home_nation, role, "repaired_holding"

    row = (
        await session.execute(
            select(NationMember, Nation)
            .join(Nation, Nation.nation_id == NationMember.nation_id)
            .where(
                NationMember.user_id == user_id,
                NationMember.is_active.is_(True),
                Nation.is_active.is_(True),
            )
            .order_by(NationMember.joined_at.desc(), Nation.nation_id.asc())
            .with_for_update()
        )
    ).first()
    if row is not None:
        member, nation = row
        user.home_nation_id = nation.nation_id
        return (
            nation,
            member.role.value
            if isinstance(member.role, NationMemberRole)
            else str(member.role),
            "membership_fallback",
        )

    founder_nation = await session.scalar(
        select(Nation)
        .where(
            Nation.founder_user_id == user_id,
            Nation.is_active.is_(True),
        )
        .order_by(Nation.nation_id.asc())
        .with_for_update()
        .limit(1)
    )
    if founder_nation is not None:
        if repair:
            role = await _repair_membership(
                session,
                user,
                founder_nation,
                role=NationMemberRole.FOUNDER,
            )
            return founder_nation, role, "founder_fallback_repaired"
        return (
            founder_nation,
            NationMemberRole.FOUNDER.value,
            "founder_fallback",
        )

    holding_row = (
        await session.execute(
            select(CurrencyHolding, Nation)
            .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
            .where(
                CurrencyHolding.user_id == user_id,
                Nation.is_active.is_(True),
            )
            .order_by(CurrencyHolding.created_at.desc(), Nation.nation_id.asc())
            .with_for_update()
        )
    ).first()
    if holding_row is not None:
        _, nation = holding_row
        if repair:
            role = await _repair_membership(
                session,
                user,
                nation,
                role=NationMemberRole.CITIZEN,
            )
            return nation, role, "holding_fallback_repaired"
        user.home_nation_id = nation.nation_id
        return (
            nation,
            NationMemberRole.CITIZEN.value,
            "holding_fallback",
        )

    return None


async def get_user_active_nation(
    session: AsyncSession,
    user_id: int,
    *,
    repair_founder_membership: bool = False,
) -> Nation | None:
    """Backward-compatible wrapper around the canonical nation resolver."""
    context = await get_user_active_nation_context(
        session,
        user_id,
        repair=repair_founder_membership,
    )
    return context[0] if context is not None else None

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
    currency_code: str,
    flag_emoji: str = "🏴",
) -> Nation:
    """Create a nation in the caller's transaction.

    A transaction is opened only when the caller has not already started one.
    This keeps the function backward-compatible for existing callers while
    allowing the founder workflow to commit the draft and nation atomically.
    """

    async def _create() -> Nation:
        user = await session.scalar(
            select(User)
            .where(User.user_id == founder_user_id)
            .with_for_update()
            .limit(1)
        )

        if user is None:
            raise ValueError("اول باید وارد بازی بشی.")

        if not (user.username or "").strip():
            raise ValueError("اول باید اسم معامله‌گرت رو ثبت کنی.")

        existing_founder_nation = await session.scalar(
            select(Nation)
            .where(
                Nation.founder_user_id == founder_user_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
            .limit(1)
        )
        if existing_founder_nation is not None or user.role == "founder":
            raise ValueError("هر معامله‌گر فقط یه ملت می‌تونه بسازه.")

        existing_group = await session.scalar(
            select(Nation)
            .where(
                Nation.group_id == group_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
            .limit(1)
        )
        if existing_group is not None:
            raise ValueError("این گروه قبلاً پایتخت یک ملت شده.")

        existing_currency = await session.scalar(
            select(Nation)
            .where(Nation.currency_code == currency_code)
            .with_for_update()
            .limit(1)
        )
        if existing_currency is not None:
            raise ValueError("این کد ارز قبلاً استفاده شده.")

        nation = Nation(
            group_id=group_id,
            name=nation_name,
            flag_emoji=(flag_emoji or "🏴").strip() or "🏴",
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

        await session.flush()
        return nation

    if session.in_transaction():
        return await _create()

    async with session.begin():
        return await _create()
