from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Nation,
    NationMember,
    NationTelegramMember,
    NationMemberRole,
    User,
)


async def require_nation_control(
    session: AsyncSession,
    *,
    user_id: int,
    nation_id: int,
    allowed_roles: Iterable[NationMemberRole | str],
    lock: bool = True,
) -> tuple[User, Nation, NationMember]:
    nation_stmt = select(Nation).where(Nation.nation_id == nation_id).limit(1)
    if lock:
        nation_stmt = nation_stmt.with_for_update()
    nation = await session.scalar(nation_stmt)
    if nation is None or not nation.is_active:
        raise ValueError("ملت فعال پیدا نشد.")

    roles = {
        role.value if isinstance(role, NationMemberRole) else str(role)
        for role in allowed_roles
    }
    member_stmt = (
        select(NationMember)
        .where(
            NationMember.user_id == user_id,
            NationMember.nation_id == nation_id,
            NationMember.is_active.is_(True),
        )
        .limit(1)
    )
    if lock:
        member_stmt = member_stmt.with_for_update()
    member = await session.scalar(member_stmt)
    if member is None:
        raise ValueError("عضویت مدیریتی معتبر پیدا نشد.")

    role_value = member.role.value if isinstance(member.role, NationMemberRole) else str(member.role)
    if role_value not in roles:
        raise ValueError("دسترسی مدیریتی کافی نیست.")

    user = await session.get(User, user_id, with_for_update=lock)
    if user is None:
        raise ValueError("کاربر پیدا نشد.")

    if not nation.is_ai:
        telegram_stmt = (
            select(NationTelegramMember.id)
            .where(
                NationTelegramMember.nation_id == nation_id,
                NationTelegramMember.telegram_user_id == user_id,
                NationTelegramMember.is_active.is_(True),
            )
            .limit(1)
        )
        if lock:
            telegram_stmt = telegram_stmt.with_for_update()
        if await session.scalar(telegram_stmt) is None:
            raise ValueError("عضویت تلگرامی این مدیر فعال نیست.")

    return user, nation, member
