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
    NationTelegramMember,
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
) -> str | None:
    if not nation.is_ai:
        telegram_membership = await session.scalar(
            select(NationTelegramMember)
            .where(
                NationTelegramMember.nation_id == nation.nation_id,
                NationTelegramMember.telegram_user_id == user.user_id,
                NationTelegramMember.is_active.is_(True),
            )
            .limit(1)
        )
        if telegram_membership is None:
            return None

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


async def _active_member_for_nation(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    *,
    lock: bool,
) -> NationMember | None:
    stmt = (
        select(NationMember)
        .where(
            NationMember.user_id == user_id,
            NationMember.nation_id == nation_id,
            NationMember.is_active.is_(True),
        )
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def _has_active_telegram_membership(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    *,
    lock: bool,
) -> bool:
    stmt = (
        select(NationTelegramMember.id)
        .where(
            NationTelegramMember.telegram_user_id == user_id,
            NationTelegramMember.nation_id == nation_id,
            NationTelegramMember.is_active.is_(True),
        )
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.scalar(stmt)) is not None


async def get_user_active_nation_context(
    session: AsyncSession,
    user_id: int,
    *,
    repair: bool = False,
    lock: bool = False,
) -> tuple[Nation, str, str] | None:
    """Resolve active nation context from authoritative membership state.

    Human nations require an active Telegram membership projection plus an
    active game membership. AI nations remain virtual and resolve through
    NationMember without Telegram requirements.

    Read-only calls never mutate state. Repairs require a transaction and row
    locks and may only repair a human membership when Telegram membership is
    already authoritative.
    """
    if repair and not lock:
        raise ValueError(
            "repair=True requires lock=True; run repairs inside an explicit transaction."
        )

    user = await session.get(User, user_id, with_for_update=lock)
    if user is None:
        return None

    def maybe_lock(statement):
        return statement.with_for_update() if lock else statement

    async def resolve_nation(nation: Nation) -> tuple[Nation, str, str] | None:
        if not nation.is_ai and not await _has_active_telegram_membership(
            session,
            user_id,
            nation.nation_id,
            lock=lock,
        ):
            return None

        member = await _active_member_for_nation(
            session,
            user_id,
            nation.nation_id,
            lock=lock,
        )
        if member is not None:
            role = (
                member.role.value
                if isinstance(member.role, NationMemberRole)
                else str(member.role)
            )
            source = "home_membership" if user.home_nation_id == nation.nation_id else (
                "ai_membership" if nation.is_ai else "telegram_membership"
            )
            return nation, role, source

        if nation.is_ai:
            if repair and nation.founder_user_id == user_id:
                role = await _repair_membership(
                    session,
                    user,
                    nation,
                    role=NationMemberRole.FOUNDER,
                )
                if role is not None:
                    return nation, role, "repaired_ai_founder"

            if repair:
                holding = await session.scalar(
                    maybe_lock(
                        select(CurrencyHolding)
                        .where(
                            CurrencyHolding.user_id == user_id,
                            CurrencyHolding.nation_id == nation.nation_id,
                        )
                        .limit(1)
                    )
                )
                if holding is not None:
                    role = await _repair_membership(
                        session,
                        user,
                        nation,
                        role=NationMemberRole.CITIZEN,
                    )
                    if role is not None:
                        return nation, role, "repaired_ai_holding"

            return None

        if not await _has_active_telegram_membership(
            session,
            user_id,
            nation.nation_id,
            lock=lock,
        ):
            return None

        if repair:
            role = (
                NationMemberRole.FOUNDER
                if nation.founder_user_id == user_id
                else NationMemberRole.CITIZEN
            )
            repaired_role = await _repair_membership(
                session,
                user,
                nation,
                role=role,
            )
            if repaired_role is not None:
                return nation, repaired_role, "repaired_telegram_membership"

        return None

    if user.home_nation_id is not None:
        home_nation = await session.scalar(
            maybe_lock(
                select(Nation).where(
                    Nation.nation_id == user.home_nation_id,
                    Nation.is_active.is_(True),
                )
            )
        )
        if home_nation is not None:
            context = await resolve_nation(home_nation)
            if context is not None:
                return context

    human_stmt = (
        select(Nation)
        .join(
            NationTelegramMember,
            NationTelegramMember.nation_id == Nation.nation_id,
        )
        .where(
            NationTelegramMember.telegram_user_id == user_id,
            NationTelegramMember.is_active.is_(True),
            Nation.is_active.is_(True),
            Nation.is_ai.is_(False),
        )
        .order_by(
            NationTelegramMember.joined_at.desc(),
            Nation.nation_id.asc(),
        )
    )
    human_nations = (
        await session.execute(maybe_lock(human_stmt))
    ).scalars().all()

    for nation in human_nations:
        member = await _active_member_for_nation(
            session,
            user_id,
            nation.nation_id,
            lock=lock,
        )
        if member is not None:
            if repair:
                user.home_nation_id = nation.nation_id
            role = (
                member.role.value
                if isinstance(member.role, NationMemberRole)
                else str(member.role)
            )
            return nation, role, "telegram_membership"

        if repair:
            role = (
                NationMemberRole.FOUNDER
                if nation.founder_user_id == user_id
                else NationMemberRole.CITIZEN
            )
            repaired_role = await _repair_membership(
                session,
                user,
                nation,
                role=role,
            )
            if repaired_role is not None:
                return nation, repaired_role, "repaired_telegram_membership"

    ai_membership_stmt = (
        select(NationMember, Nation)
        .join(Nation, Nation.nation_id == NationMember.nation_id)
        .where(
            NationMember.user_id == user_id,
            NationMember.is_active.is_(True),
            Nation.is_active.is_(True),
            Nation.is_ai.is_(True),
        )
        .order_by(NationMember.joined_at.desc(), Nation.nation_id.asc())
    )
    ai_membership_row = (
        await session.execute(maybe_lock(ai_membership_stmt))
    ).first()
    if ai_membership_row is not None:
        member, nation = ai_membership_row
        if repair:
            user.home_nation_id = nation.nation_id
        role = (
            member.role.value
            if isinstance(member.role, NationMemberRole)
            else str(member.role)
        )
        return nation, role, "ai_membership"

    ai_founder_stmt = (
        select(Nation)
        .where(
            Nation.founder_user_id == user_id,
            Nation.is_active.is_(True),
            Nation.is_ai.is_(True),
        )
        .order_by(Nation.nation_id.asc())
        .limit(1)
    )
    ai_founder_nation = await session.scalar(maybe_lock(ai_founder_stmt))
    if ai_founder_nation is not None:
        if repair:
            role = await _repair_membership(
                session,
                user,
                ai_founder_nation,
                role=NationMemberRole.FOUNDER,
            )
            if role is not None:
                return ai_founder_nation, role, "repaired_ai_founder"
        return (
            ai_founder_nation,
            NationMemberRole.FOUNDER.value,
            "ai_founder",
        )

    ai_holding_stmt = (
        select(CurrencyHolding, Nation)
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(
            CurrencyHolding.user_id == user_id,
            Nation.is_active.is_(True),
            Nation.is_ai.is_(True),
        )
        .order_by(CurrencyHolding.created_at.desc(), Nation.nation_id.asc())
    )
    ai_holding_row = (
        await session.execute(maybe_lock(ai_holding_stmt))
    ).first()
    if ai_holding_row is not None:
        _, nation = ai_holding_row
        if repair:
            role = await _repair_membership(
                session,
                user,
                nation,
                role=NationMemberRole.CITIZEN,
            )
            if role is not None:
                return nation, role, "repaired_ai_holding"
        return (
            nation,
            NationMemberRole.CITIZEN.value,
            "ai_holding",
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
        lock=repair_founder_membership,
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
            NationTelegramMember(
                nation_id=nation.nation_id,
                telegram_user_id=founder_user_id,
                telegram_status="administrator",
                is_member=True,
                is_active=True,
                joined_at=datetime.utcnow(),
                left_at=None,
                last_seen_at=datetime.utcnow(),
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
