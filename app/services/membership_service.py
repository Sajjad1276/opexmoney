from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram.types import ChatMember

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationLog,
    NationMember,
    NationMemberRole,
    NationTelegramMember,
    User,
)


TELEGRAM_ACTIVE_STATUSES = frozenset({
    "member",
    "administrator",
    "creator",
})


@dataclass(frozen=True)
class MembershipSyncResult:
    nation_id: int
    telegram_user_id: int
    active: bool
    changed: bool
    became_active: bool
    became_inactive: bool
    action_type: str | None
    user_registered: bool


def telegram_chat_member_state(member: ChatMember) -> tuple[str, bool, bool]:
    status = getattr(member.status, "value", member.status)
    status = str(status)

    if status == "restricted":
        is_member = bool(getattr(member, "is_member", False))
        return status, is_member, is_member

    is_active = status in TELEGRAM_ACTIVE_STATUSES
    return status, is_active, is_active


def telegram_status_is_active(status: str, is_member: bool) -> bool:
    return status in TELEGRAM_ACTIVE_STATUSES or (
        status == "restricted" and is_member
    )


async def get_human_nation_by_group(
    session: AsyncSession,
    group_id: int,
    *,
    lock: bool = False,
) -> Nation | None:
    stmt = (
        select(Nation)
        .where(
            Nation.group_id == group_id,
            Nation.is_active.is_(True),
            Nation.is_ai.is_(False),
        )
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def _get_or_create_telegram_membership(
    session: AsyncSession,
    *,
    nation_id: int,
    telegram_user_id: int,
    telegram_status: str,
    is_member: bool,
    observed_at: datetime,
) -> tuple[NationTelegramMember, bool]:
    row = await session.scalar(
        select(NationTelegramMember)
        .where(
            NationTelegramMember.nation_id == nation_id,
            NationTelegramMember.telegram_user_id == telegram_user_id,
        )
        .with_for_update()
        .limit(1)
    )
    if row is not None:
        return row, False

    row = NationTelegramMember(
        nation_id=nation_id,
        telegram_user_id=telegram_user_id,
        telegram_status=telegram_status,
        is_member=is_member,
        is_active=telegram_status_is_active(telegram_status, is_member),
        joined_at=observed_at if telegram_status_is_active(telegram_status, is_member) else None,
        left_at=None if telegram_status_is_active(telegram_status, is_member) else observed_at,
        last_seen_at=observed_at,
    )
    session.add(row)
    await session.flush()
    return row, True


async def _ensure_registered_user_projection(
    session: AsyncSession,
    *,
    nation: Nation,
    user_id: int,
) -> tuple[User | None, bool]:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or not (user.username or "").strip():
        return user, False

    member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation.nation_id,
            NationMember.user_id == user_id,
        )
        .with_for_update()
        .limit(1)
    )

    created = False
    if member is None:
        role = (
            NationMemberRole.FOUNDER
            if nation.founder_user_id == user_id
            else NationMemberRole.CITIZEN
        )
        member = NationMember(
            nation_id=nation.nation_id,
            user_id=user_id,
            role=role,
            is_active=True,
        )
        session.add(member)
        created = True
    else:
        member.is_active = True

    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user_id,
            CurrencyHolding.nation_id == nation.nation_id,
        )
        .with_for_update()
        .limit(1)
    )

    if holding is None:
        initial_amount = (
            "1000.0000"
            if nation.founder_user_id == user_id
            else "500.0000"
        )
        from decimal import Decimal
        holding = CurrencyHolding(
            user_id=user_id,
            nation_id=nation.nation_id,
            amount=Decimal(initial_amount),
        )
        session.add(holding)
        await session.flush()

    if role == NationMemberRole.FOUNDER:
        user.role = "founder"
    elif user.role in {"player", "citizen"}:
        user.role = "citizen"

    if user.home_nation_id is None:
        user.home_nation_id = nation.nation_id

    return user, created


async def sync_telegram_membership(
    session: AsyncSession,
    *,
    group_id: int,
    telegram_user_id: int,
    telegram_status: str,
    is_member: bool,
    observed_at: datetime | None = None,
    source: str = "chat_member",
) -> MembershipSyncResult | None:
    observed_at = observed_at or datetime.utcnow()

    nation = await get_human_nation_by_group(
        session,
        group_id,
        lock=True,
    )
    if nation is None:
        return None

    row, created = await _get_or_create_telegram_membership(
        session,
        nation_id=nation.nation_id,
        telegram_user_id=telegram_user_id,
        telegram_status=telegram_status,
        is_member=is_member,
        observed_at=observed_at,
    )

    previous_active = bool(row.is_active)
    active_now = telegram_status_is_active(telegram_status, is_member)

    row.telegram_status = telegram_status
    row.is_member = is_member
    row.is_active = active_now
    row.last_seen_at = observed_at

    became_active = active_now and not previous_active
    became_inactive = previous_active and not active_now

    if active_now:
        if row.joined_at is None:
            row.joined_at = observed_at
        row.left_at = None
    else:
        row.left_at = observed_at

    user = await session.get(User, telegram_user_id, with_for_update=True)
    user_registered = user is not None and bool((user.username or "").strip())

    if active_now:
        user, _created_projection = await _ensure_registered_user_projection(
            session,
            nation=nation,
            user_id=telegram_user_id,
        )
        user_registered = user is not None and bool((user.username or "").strip())
    elif user_registered:
        member = await session.scalar(
            select(NationMember)
            .where(
                NationMember.nation_id == nation.nation_id,
                NationMember.user_id == telegram_user_id,
            )
            .with_for_update()
            .limit(1)
        )
        if member is not None:
            member.is_active = False

        if user.home_nation_id == nation.nation_id:
            fallback_nation_id = await session.scalar(
                select(NationMember.nation_id)
                .join(
                    NationTelegramMember,
                    (
                        (NationTelegramMember.nation_id == NationMember.nation_id)
                        & (
                            NationTelegramMember.telegram_user_id
                            == NationMember.user_id
                        )
                    ),
                )
                .where(
                    NationMember.user_id == telegram_user_id,
                    NationMember.is_active.is_(True),
                    NationTelegramMember.is_active.is_(True),
                    Nation.nation_id == NationMember.nation_id,
                )
                .order_by(NationMember.joined_at.desc(), NationMember.nation_id.asc())
                .limit(1)
            )
            user.home_nation_id = fallback_nation_id

    action_type: str | None = None
    if became_active:
        action_type = "MEMBER_JOIN"
        nation.member_count = max(0, int(nation.member_count or 0) + 1)
    elif became_inactive:
        action_type = (
            "MEMBER_KICKED"
            if telegram_status == "kicked"
            else "MEMBER_LEAVE"
        )
        nation.member_count = max(0, int(nation.member_count or 0) - 1)

    if action_type is not None:
        session.add(
            NationLog(
                nation_id=nation.nation_id,
                actor_id=telegram_user_id,
                action_type=action_type,
                target_id=telegram_user_id,
                event_metadata={
                    "source": source,
                    "telegram_status": telegram_status,
                    "is_member": is_member,
                    "telegram_user_id": telegram_user_id,
                },
            )
        )

    await session.flush()

    return MembershipSyncResult(
        nation_id=nation.nation_id,
        telegram_user_id=telegram_user_id,
        active=active_now,
        changed=became_active or became_inactive,
        became_active=became_active or (created and active_now),
        became_inactive=became_inactive,
        action_type=action_type,
        user_registered=user_registered,
    )


async def sync_registered_user_memberships(
    session: AsyncSession,
    user_id: int,
) -> list[int]:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or not (user.username or "").strip():
        return []

    rows = (
        await session.execute(
            select(NationTelegramMember, Nation)
            .join(Nation, Nation.nation_id == NationTelegramMember.nation_id)
            .where(
                NationTelegramMember.telegram_user_id == user_id,
                NationTelegramMember.is_active.is_(True),
                Nation.is_active.is_(True),
                Nation.is_ai.is_(False),
            )
            .order_by(NationTelegramMember.joined_at.desc(), Nation.nation_id.asc())
        )
    ).all()

    synced: list[int] = []
    for membership, nation in rows:
        await _ensure_registered_user_projection(
            session,
            nation=nation,
            user_id=user_id,
        )
        synced.append(nation.nation_id)

    if user.home_nation_id is None and synced:
        user.home_nation_id = synced[0]

    await session.flush()
    return synced


async def active_telegram_membership_count(
    session: AsyncSession,
    nation_id: int,
) -> int:
    return int(
        await session.scalar(
            select(func.count(NationTelegramMember.id)).where(
                NationTelegramMember.nation_id == nation_id,
                NationTelegramMember.is_active.is_(True),
            )
        )
        or 0
    )
