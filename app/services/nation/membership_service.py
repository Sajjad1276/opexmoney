from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationJoinRequest,
    NationLog,
    NationMember,
    NationMemberRole,
    NationTelegramMember,
    User,
    UserActivity,
)
from app.repositories.nation_repository import NationRepository
from app.repositories.user_repository import UserRepository
from app.services.nation.nation_service import convert_holding_to_xr
from app.services.user_service import sync_user_balance


class NationPrivateError(ValueError):
    """Raised when a normal join is attempted against an invite-only nation."""


@dataclass(frozen=True)
class MembershipResult:
    success: bool
    user_id: int
    nation_id: int
    role: str
    message: str


@dataclass(frozen=True)
class MembershipRequest:
    request_id: int
    user_id: int
    nation_id: int
    message: str


def _role_value(role: NationMemberRole | str) -> str:
    return role.value if isinstance(role, NationMemberRole) else str(role)


async def _active_membership(
    session: AsyncSession,
    user_id: int,
) -> NationMember | None:
    return await session.scalar(
        select(NationMember)
        .where(
            NationMember.user_id == user_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )


async def _ensure_membership(
    session: AsyncSession,
    *,
    user: User,
    nation: Nation,
    role: NationMemberRole,
    actor_id: int | None,
    source: str,
) -> MembershipResult:
    existing = await _active_membership(session, user.user_id)
    if existing is not None:
        raise ValueError("⚠️ تو همین الان عضو یک ملت هستی.")

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
        member.left_at = None

    user.home_nation_id = nation.nation_id
    user.role = _role_value(role)

    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user.user_id,
            CurrencyHolding.nation_id == nation.nation_id,
        )
        .with_for_update()
        .limit(1)
    )
    if holding is None:
        amount = Decimal("500.00")
        holding = CurrencyHolding(
            user_id=user.user_id,
            nation_id=nation.nation_id,
            amount=amount,
        )
        session.add(holding)
        user.balance = amount
    else:
        user.balance = Decimal(str(holding.amount))

    telegram = await session.scalar(
        select(NationTelegramMember)
        .where(
            NationTelegramMember.nation_id == nation.nation_id,
            NationTelegramMember.telegram_user_id == user.user_id,
        )
        .with_for_update()
        .limit(1)
    )
    now = datetime.utcnow()
    if telegram is None:
        telegram = NationTelegramMember(
            nation_id=nation.nation_id,
            telegram_user_id=user.user_id,
            telegram_status="member" if nation.group_id is not None else "virtual",
            is_member=True,
            is_active=True,
            joined_at=now,
            last_seen_at=now,
        )
        session.add(telegram)
    else:
        telegram.telegram_status = "member" if nation.group_id is not None else "virtual"
        telegram.is_member = True
        telegram.is_active = True
        telegram.left_at = None
        telegram.joined_at = telegram.joined_at or now
        telegram.last_seen_at = now

    nation.member_count = max(0, int(nation.member_count or 0)) + 1

    session.add(
        UserActivity(
            user_id=user.user_id,
            nation_id=nation.nation_id,
            activity_type=ActivityType.LOGIN,
        )
    )
    session.add(
        NationLog(
            nation_id=nation.nation_id,
            actor_id=actor_id or user.user_id,
            action_type="MEMBER_JOIN",
            target_id=user.user_id,
            event_metadata={
                "role": _role_value(role),
                "source": source,
            },
        )
    )

    await session.flush()
    await sync_user_balance(session, user.user_id)

    return MembershipResult(
        success=True,
        user_id=user.user_id,
        nation_id=nation.nation_id,
        role=_role_value(role),
        message=f"🎉 به «{nation.name}» پیوستی!",
    )


async def _join_nation_internal(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    *,
    allow_private: bool,
    source: str,
) -> MembershipResult:
    users = UserRepository(session)
    nations = NationRepository(session)

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ValueError("⚠️ اول با /start وارد بازی شو.")

    if await _active_membership(session, user_id) is not None or user.home_nation_id is not None:
        raise ValueError("⚠️ تو همین الان عضو یک ملت هستی.")

    nation = await session.get(Nation, nation_id, with_for_update=True)
    if nation is None or not nation.is_active:
        raise ValueError("⚠️ این ملت فعال نیست.")

    policy = (nation.join_policy or "OPEN").upper()
    if policy == "INVITE_ONLY" and not allow_private:
        raise NationPrivateError(
            "🔐 این ملت خصوصی است و فقط با لینک دعوت می‌توانی واردش شوی."
        )
    if policy == "APPROVAL" and not allow_private:
        raise ValueError("📝 ورود به این ملت نیاز به تأیید بنیان‌گذار دارد.")

    return await _ensure_membership(
        session,
        user=user,
        nation=nation,
        role=NationMemberRole.CITIZEN,
        actor_id=user_id,
        source=source,
    )


async def join_nation(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
) -> MembershipResult:
    return await _join_nation_internal(
        session,
        user_id,
        nation_id,
        allow_private=False,
        source="join_nation",
    )


async def leave_nation(
    session: AsyncSession,
    user_id: int,
) -> bool:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return False

    membership = await _active_membership(session, user_id)
    if membership is None:
        return False

    if membership.role == NationMemberRole.FOUNDER:
        raise ValueError("👑 بنیان‌گذار نمی‌تواند ملت را با خروج عادی ترک کند.")

    nation = await session.get(Nation, membership.nation_id, with_for_update=True)
    if nation is None:
        return False

    await convert_holding_to_xr(user, nation, session)
    membership.is_active = False
    membership.left_at = datetime.utcnow()
    nation.member_count = max(0, int(nation.member_count or 0) - 1)
    user.home_nation_id = None
    user.role = "player"

    telegram = await session.scalar(
        select(NationTelegramMember)
        .where(
            NationTelegramMember.nation_id == nation.nation_id,
            NationTelegramMember.telegram_user_id == user_id,
        )
        .with_for_update()
        .limit(1)
    )
    if telegram is not None:
        telegram.is_active = False
        telegram.is_member = False
        telegram.left_at = datetime.utcnow()
        telegram.last_seen_at = datetime.utcnow()

    session.add(
        NationLog(
            nation_id=nation.nation_id,
            actor_id=user_id,
            action_type="MEMBER_LEAVE",
            target_id=user_id,
            event_metadata={"previous_role": _role_value(membership.role)},
        )
    )
    await session.flush()
    return True


async def request_membership(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
) -> MembershipRequest:
    user = await session.get(User, user_id, with_for_update=True)
    nation = await session.get(Nation, nation_id, with_for_update=True)
    if user is None:
        raise ValueError("⚠️ کاربر پیدا نشد.")
    if nation is None or not nation.is_active:
        raise ValueError("⚠️ این ملت فعال نیست.")
    if await _active_membership(session, user_id) is not None or user.home_nation_id is not None:
        raise ValueError("⚠️ تو همین الان عضو یک ملت هستی.")
    if (nation.join_policy or "OPEN").upper() == "INVITE_ONLY":
        raise NationPrivateError("🔐 این ملت خصوصی است و فقط با لینک دعوت عضو می‌پذیرد.")

    pending = await session.scalar(
        select(NationJoinRequest)
        .where(
            NationJoinRequest.nation_id == nation_id,
            NationJoinRequest.user_id == user_id,
            NationJoinRequest.status == "pending",
        )
        .with_for_update()
        .limit(1)
    )
    if pending is not None:
        return MembershipRequest(
            request_id=pending.id,
            user_id=user_id,
            nation_id=nation_id,
            message="📝 درخواستت قبلاً ثبت شده و هنوز در حال بررسیه.",
        )

    request = NationJoinRequest(
        nation_id=nation_id,
        user_id=user_id,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(hours=48),
    )
    session.add(request)
    await session.flush()
    return MembershipRequest(
        request_id=request.id,
        user_id=user_id,
        nation_id=nation_id,
        message="✅ درخواست عضویت ارسال شد. تا 48 ساعت فرصت بررسی دارد.",
    )


async def approve_membership(
    session: AsyncSession,
    founder_id: int,
    request_id: int,
) -> MembershipResult:
    request = await session.get(NationJoinRequest, request_id, with_for_update=True)
    if request is None or str(request.status) != "pending":
        raise ValueError("⚠️ درخواست پیدا نشد.")

    nation = await session.get(Nation, request.nation_id, with_for_update=True)
    actor_member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == request.nation_id,
            NationMember.user_id == founder_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    if nation is None or not nation.is_active or actor_member is None:
        raise ValueError("⛔ بنیان‌گذار این ملت معتبر نیست.")
    if actor_member.role != NationMemberRole.FOUNDER:
        raise ValueError("⛔ فقط بنیان‌گذار می‌تواند درخواست را تأیید کند.")

    if request.expires_at <= datetime.utcnow():
        request.status = "rejected"
        request.resolved_by = founder_id
        request.resolved_at = datetime.utcnow()
        raise ValueError("⌛ مهلت درخواست تمام شده است.")

    result = await _join_nation_internal(
        session,
        request.user_id,
        request.nation_id,
        allow_private=True,
        source="membership_approval",
    )
    request.status = "approved"
    request.resolved_by = founder_id
    request.resolved_at = datetime.utcnow()
    return MembershipResult(
        success=result.success,
        user_id=result.user_id,
        nation_id=result.nation_id,
        role=result.role,
        message="✅ درخواست عضویت تأیید شد.",
    )


async def reject_membership(
    session: AsyncSession,
    founder_id: int,
    request_id: int,
) -> bool:
    request = await session.get(NationJoinRequest, request_id, with_for_update=True)
    if request is None or str(request.status) != "pending":
        return False

    actor_member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == request.nation_id,
            NationMember.user_id == founder_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    if actor_member is None or actor_member.role != NationMemberRole.FOUNDER:
        raise ValueError("⛔ فقط بنیان‌گذار می‌تواند درخواست را رد کند.")

    request.status = "rejected"
    request.resolved_by = founder_id
    request.resolved_at = datetime.utcnow()
    await session.flush()
    return True
