from __future__ import annotations

from datetime import datetime, timedelta
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BotGroup, Nation, NationFoundingDraft, User
from app.utils.validators import validate_currency_code


DRAFT_TTL = timedelta(minutes=30)

ACTIVE_DRAFT_STATUSES = (
    "WAITING_GROUP",
    "GROUP_READY",
    "NAMING",
    "FLAG",
    "REVIEW",
    "FINALIZING",
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _is_expired(draft: NationFoundingDraft) -> bool:
    return draft.expires_at <= _utcnow()


async def get_or_create_draft(
    session: AsyncSession,
    founder_user_id: int,
) -> NationFoundingDraft:
    async with session.begin():
        user = await session.get(User, founder_user_id, with_for_update=True)
        if user is None or not (user.username or "").strip():
            raise ValueError("اول باید وارد بازی بشی و اسم معامله‌گرت رو ثبت کنی.")

        existing_nation = await session.scalar(
            select(Nation)
            .where(
                Nation.founder_user_id == founder_user_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
            .limit(1)
        )
        if existing_nation is not None:
            raise ValueError("هر معامله‌گر فقط می‌تونه یک ملت تأسیس کنه.")

        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
            )
            .order_by(NationFoundingDraft.id.desc())
            .with_for_update()
            .limit(1)
        )

        if draft is not None and _is_expired(draft):
            draft.status = "EXPIRED"
            draft = None

        if draft is None:
            draft = NationFoundingDraft(
                founder_user_id=founder_user_id,
                launch_token=token_urlsafe(24),
                status="WAITING_GROUP",
                flag_emoji="🏴",
                expires_at=_utcnow() + DRAFT_TTL,
            )
            session.add(draft)
            await session.flush()

        return draft


async def get_active_draft(
    session: AsyncSession,
    founder_user_id: int,
    *,
    lock: bool = False,
) -> NationFoundingDraft | None:
    statement = (
        select(NationFoundingDraft)
        .where(
            NationFoundingDraft.founder_user_id == founder_user_id,
            NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
        )
        .order_by(NationFoundingDraft.id.desc())
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()

    draft = await session.scalar(statement)
    if draft is not None and _is_expired(draft):
        if lock:
            draft.status = "EXPIRED"
        return None
    return draft


async def get_draft_by_token(
    session: AsyncSession,
    token: str,
) -> NationFoundingDraft | None:
    return await session.scalar(
        select(NationFoundingDraft)
        .where(
            NationFoundingDraft.launch_token == token,
            NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
        )
        .with_for_update()
        .limit(1)
    )


async def bind_group(
    session: AsyncSession,
    *,
    founder_user_id: int,
    token: str,
    group_id: int,
    group_title: str,
    group_username: str | None,
    group_type: str,
) -> NationFoundingDraft:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.launch_token == token,
                NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None:
            raise ValueError("این لینک تأسیس منقضی شده. از پنل تأسیس دوباره شروع کن.")

        if draft.founder_user_id != founder_user_id:
            raise ValueError("این لینک برای بنیان‌گذار دیگری ساخته شده است.")

        if _is_expired(draft):
            draft.status = "EXPIRED"
            raise ValueError("فرآیند تأسیس منقضی شده. دوباره شروع کن.")

        if draft.group_id not in (None, group_id):
            raise ValueError("این فرآیند از قبل به یک گروه دیگر متصل شده است.")

        existing_draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.group_id == group_id,
                NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
                NationFoundingDraft.id != draft.id,
            )
            .with_for_update()
            .limit(1)
        )
        if existing_draft is not None:
            raise ValueError("این گروه در فرآیند تأسیس یک ملت دیگر است.")

        existing_nation = await session.scalar(
            select(Nation)
            .where(
                Nation.group_id == group_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
            .limit(1)
        )
        if existing_nation is not None:
            raise ValueError("این گروه قبلاً پایتخت یک ملت شده است.")

        draft.group_id = group_id
        draft.group_title = group_title
        draft.group_username = group_username
        draft.group_type = group_type
        draft.nation_name = group_title
        draft.status = "NAMING"
        draft.expires_at = _utcnow() + DRAFT_TTL

        bot_group = await session.get(BotGroup, group_id, with_for_update=True)
        if bot_group is None:
            session.add(
                BotGroup(
                    group_id=group_id,
                    title=group_title,
                    username=group_username,
                    is_active=True,
                )
            )
        else:
            bot_group.title = group_title
            bot_group.username = group_username
            bot_group.is_active = True

        await session.flush()
        return draft


async def set_nation_name(
    session: AsyncSession,
    *,
    founder_user_id: int,
    nation_name: str,
) -> NationFoundingDraft:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status.in_(("GROUP_READY", "NAMING", "FLAG", "REVIEW")),
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None or draft.group_id is None:
            raise ValueError("اول باید گروه پایتخت را متصل کنی.")

        if _is_expired(draft):
            draft.status = "EXPIRED"
            raise ValueError("فرآیند تأسیس منقضی شده. دوباره شروع کن.")

        draft.nation_name = nation_name
        draft.status = "NAMING"
        draft.expires_at = _utcnow() + DRAFT_TTL
        await session.flush()
        return draft


async def set_currency_code(
    session: AsyncSession,
    *,
    founder_user_id: int,
    currency_code: str,
) -> NationFoundingDraft:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status.in_(("NAMING", "REVIEW")),
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None:
            raise ValueError("اطلاعات تأسیس پیدا نشد. دوباره شروع کن.")

        if _is_expired(draft):
            draft.status = "EXPIRED"
            raise ValueError("فرآیند تأسیس منقضی شده. دوباره شروع کن.")

        normalized = (currency_code or "").strip().upper()
        valid, error = await validate_currency_code(normalized, session)
        if not valid:
            raise ValueError(error)

        draft.currency_code = normalized
        draft.status = "REVIEW"
        draft.expires_at = _utcnow() + DRAFT_TTL
        await session.flush()
        return draft


async def set_flag(
    session: AsyncSession,
    *,
    founder_user_id: int,
    flag_emoji: str,
) -> NationFoundingDraft:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status.in_(("FLAG", "REVIEW")),
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None:
            raise ValueError("اطلاعات تأسیس پیدا نشد. دوباره شروع کن.")

        if _is_expired(draft):
            draft.status = "EXPIRED"
            raise ValueError("فرآیند تأسیس منقضی شده. دوباره شروع کن.")

        draft.flag_emoji = flag_emoji or "🏴"
        draft.status = "REVIEW"
        draft.expires_at = _utcnow() + DRAFT_TTL
        await session.flush()
        return draft


async def cancel_draft(
    session: AsyncSession,
    founder_user_id: int,
) -> None:
    async with session.begin():
        draft = await get_active_draft(
            session,
            founder_user_id,
            lock=True,
        )
        if draft is not None:
            draft.status = "CANCELLED"


async def finalize_draft(
    session: AsyncSession,
    *,
    founder_user_id: int,
) -> tuple[Nation, int]:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status == "REVIEW",
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None:
            raise ValueError("اطلاعات تأسیس پیدا نشد. دوباره شروع کن.")

        if _is_expired(draft):
            draft.status = "EXPIRED"
            raise ValueError("فرآیند تأسیس منقضی شده. دوباره شروع کن.")

        if not all(
            (
                draft.group_id,
                draft.group_title,
                draft.nation_name,
                draft.currency_code,
            )
        ):
            raise ValueError("اطلاعات تأسیس کامل نیست. دوباره شروع کن.")

        draft.status = "FINALIZING"

        group_id = int(draft.group_id)
        nation_name = draft.nation_name
        flag_emoji = draft.flag_emoji or "🏴"

        # Keep founder-selected currency code and nation creation in
        # one transaction. The DB unique constraint remains the final
        # concurrency guard against duplicate currency codes.
        async with session.begin_nested():
            nation = await create_nation(
                session=session,
                founder_id=founder_user_id,
                name=nation_name,
                currency_code=draft.currency_code,
                is_private=False,
                group_chat_id=group_id,
                flag_emoji=flag_emoji,
            )

        draft.status = "COMPLETED"
        await session.flush()

        return nation, group_id



async def reset_group(
    session: AsyncSession,
    *,
    founder_user_id: int,
) -> NationFoundingDraft:
    async with session.begin():
        draft = await session.scalar(
            select(NationFoundingDraft)
            .where(
                NationFoundingDraft.founder_user_id == founder_user_id,
                NationFoundingDraft.status.in_(ACTIVE_DRAFT_STATUSES),
            )
            .with_for_update()
            .limit(1)
        )
        if draft is None:
            raise ValueError("فرآیند تأسیس فعالی پیدا نشد.")

        draft.group_id = None
        draft.group_title = None
        draft.group_username = None
        draft.group_type = None
        draft.nation_name = None
        draft.currency_code = None
        draft.flag_emoji = "🏴"
        draft.launch_token = token_urlsafe(24)
        draft.status = "WAITING_GROUP"
        draft.expires_at = _utcnow() + DRAFT_TTL
        await session.flush()
        return draft



from dataclasses import dataclass
from datetime import timedelta

from app.database.models import (
    CurrencyHolding,
    NationLog,
    NationMember,
    NationMemberRole,
    NationTelegramMember,
    Transaction,
    UserActivity,
    ActivityType,
)
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from config import settings


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason: str | None
    missing: list[str]


async def _trade_volume_for_user(
    session: AsyncSession,
    user_id: int,
) -> Decimal:
    since = datetime.utcnow() - timedelta(days=30)
    return await TransactionRepository(session).get_user_volume_since(
        user_id,
        since,
    )


async def check_nation_creation_eligibility(
    session: AsyncSession,
    user_id: int,
) -> EligibilityResult:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        return EligibilityResult(False, "اول باید وارد بازی بشی.", ["user"])
    if not (user.username or "").strip():
        return EligibilityResult(False, "اول باید اسم معامله‌گرت رو ثبت کنی.", ["username"])

    total_traded = await _trade_volume_for_user(session, user_id)
    balance = max(
        Decimal(str(user.xr_balance or Decimal("0"))),
        Decimal(str(user.balance or Decimal("0"))),
    )

    traded_ok = total_traded >= Decimal(str(settings.nation_creation_trade_threshold))
    balance_ok = balance >= Decimal(str(settings.nation_creation_cost))
    missing: list[str] = []
    if not traded_ok:
        missing.append("total_traded")
    if not balance_ok:
        missing.append("balance")

    if traded_ok or balance_ok:
        return EligibilityResult(True, None, [])

    return EligibilityResult(
        False,
        (
            f"حداقل معامله تجمعی {settings.nation_creation_trade_threshold} "
            f"یا موجودی {settings.nation_creation_cost} دلار لازم است."
        ),
        missing,
    )


async def _require_founder(
    session: AsyncSession,
    founder_id: int,
    nation_id: int,
) -> tuple[User, Nation]:
    user = await session.get(User, founder_id, with_for_update=True)
    nation = await session.get(Nation, nation_id, with_for_update=True)
    if user is None or nation is None or not nation.is_active:
        raise ValueError("⚠️ ملت فعال پیدا نشد.")
    membership = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation_id,
            NationMember.user_id == founder_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    if membership is None or membership.role != NationMemberRole.FOUNDER:
        raise ValueError("⛔ فقط بنیان‌گذار این عملیات را انجام می‌دهد.")
    return user, nation


async def create_nation(
    session: AsyncSession,
    founder_id: int,
    name: str,
    currency_code: str,
    is_private: bool,
    group_chat_id: int | None,
    flag_emoji: str = "🏴",
) -> Nation:
    eligibility = await check_nation_creation_eligibility(session, founder_id)
    if not eligibility.eligible:
        raise ValueError(eligibility.reason or "شرایط تأسیس ملت کامل نیست.")

    user = await session.get(User, founder_id, with_for_update=True)
    if user is None:
        raise ValueError("اول باید وارد بازی بشی.")

    existing = await session.scalar(
        select(Nation)
        .where(
            Nation.founder_user_id == founder_id,
            Nation.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    if existing is not None or user.role == NationMemberRole.FOUNDER.value:
        raise ValueError("هر معامله‌گر فقط یک ملت می‌تواند تأسیس کند.")

    if group_chat_id is not None:
        same_group = await session.scalar(
            select(Nation)
            .where(
                Nation.group_id == group_chat_id,
                Nation.is_active.is_(True),
            )
            .with_for_update()
            .limit(1)
        )
        if same_group is not None:
            raise ValueError("این گروه قبلاً پایتخت یک ملت شده.")

    same_currency = await session.scalar(
        select(Nation)
        .where(Nation.currency_code == currency_code)
        .with_for_update()
        .limit(1)
    )
    if same_currency is not None:
        raise ValueError("این کد ارز قبلاً استفاده شده.")

    nation = Nation(
        group_id=group_chat_id,
        name=name,
        flag_emoji=(flag_emoji or "🏴").strip() or "🏴",
        currency_code=currency_code,
        founder_user_id=founder_id,
        exchange_rate=Decimal("1.0000"),
        rate_prev=Decimal("1.0000"),
        rate_24h_open=Decimal("1.0000"),
        trade_volume_24h=Decimal("0"),
        active_members_24h=1,
        member_count=1,
        is_active=True,
        join_policy="INVITE_ONLY" if is_private else "OPEN",
        personality="neutral",
        invite_code=f"OPX-{token_urlsafe(12)}",
        treasury=Decimal("0.00"),
    )
    session.add(nation)
    await session.flush()

    user.role = NationMemberRole.FOUNDER.value
    user.home_nation_id = nation.nation_id
    user.xr_balance = Decimal(str(user.xr_balance or Decimal("0"))) + Decimal("1000.00")
    user.balance = Decimal("1000.00")

    session.add(CurrencyHolding(
        user_id=founder_id,
        nation_id=nation.nation_id,
        amount=Decimal("1000.0000"),
    ))
    session.add(NationMember(
        nation_id=nation.nation_id,
        user_id=founder_id,
        role=NationMemberRole.FOUNDER,
        is_active=True,
    ))
    if group_chat_id is not None:
        session.add(
            NationTelegramMember(
                nation_id=nation.nation_id,
                telegram_user_id=founder_id,
                telegram_status="administrator",
                is_member=True,
                is_active=True,
                joined_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            )
        )
    session.add(NationLog(
        nation_id=nation.nation_id,
        actor_id=founder_id,
        action_type="MEMBER_JOIN",
        target_id=founder_id,
        event_metadata={"role": "founder", "source": "nation_creation"},
    ))
    session.add(UserActivity(
        user_id=founder_id,
        nation_id=nation.nation_id,
        activity_type=ActivityType.LOGIN,
    ))
    await session.flush()
    return nation


async def dissolve_nation(
    session: AsyncSession,
    founder_id: int,
    nation_id: int,
) -> bool:
    _, nation = await _require_founder(session, founder_id, nation_id)
    members = (
        await session.execute(
            select(NationMember)
            .where(
                NationMember.nation_id == nation_id,
                NationMember.is_active.is_(True),
            )
            .with_for_update()
        )
    ).scalars().all()

    for member in members:
        user = await session.get(User, member.user_id, with_for_update=True)
        if user is not None:
            await _liquidate_if_needed(session, user, nation)
            user.home_nation_id = None
            user.role = "player"
        member.is_active = False
        member.left_at = datetime.utcnow()

    nation.is_active = False
    nation.deleted_at = datetime.utcnow()
    nation.member_count = 0
    session.add(NationLog(
        nation_id=nation_id,
        actor_id=founder_id,
        action_type="NATION_DISSOLVED",
        target_id=None,
        event_metadata={"source": "founder"},
    ))
    await session.flush()
    return True


async def _liquidate_if_needed(
    session: AsyncSession,
    user: User,
    nation: Nation,
) -> None:
    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user.user_id,
            CurrencyHolding.nation_id == nation.nation_id,
        )
        .with_for_update()
    )
    if holding is None:
        return
    original_amount = Decimal(str(holding.amount or "0"))
    if original_amount <= 0:
        return
    rate = Decimal(str(nation.exchange_rate or Decimal("1")))
    value = original_amount * rate
    user.xr_balance = Decimal(str(user.xr_balance or Decimal("0"))) + value
    holding.amount = Decimal("0")
    session.add(Transaction(
        user_id=user.user_id,
        nation_id=nation.nation_id,
        transaction_type="liquidate",
        spend_xr=value,
        amount=original_amount,
        fee_xr=Decimal("0"),
        rate=rate,
    ))


async def transfer_ownership(
    session: AsyncSession,
    founder_id: int,
    new_founder_id: int,
    nation_id: int,
) -> bool:
    _, nation = await _require_founder(session, founder_id, nation_id)
    target = await session.get(User, new_founder_id, with_for_update=True)
    if target is None:
        raise ValueError("کاربر مقصد پیدا نشد.")

    target_member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation_id,
            NationMember.user_id == new_founder_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    if target_member is None:
        raise ValueError("کاربر جدید باید ابتدا عضو این ملت باشد.")

    old_member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation_id,
            NationMember.user_id == founder_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
        .limit(1)
    )
    old_user = await session.get(User, founder_id, with_for_update=True)
    if old_member is None or old_user is None:
        raise ValueError("بنیان‌گذار فعلی پیدا نشد.")

    old_member.role = NationMemberRole.CITIZEN
    old_user.role = "citizen"
    target_member.role = NationMemberRole.FOUNDER
    target.role = "founder"
    nation.founder_user_id = new_founder_id
    session.add(NationLog(
        nation_id=nation_id,
        actor_id=founder_id,
        action_type="ROLE_PROMOTED",
        target_id=new_founder_id,
        event_metadata={"new_role": "founder", "source": "ownership_transfer"},
    ))
    await session.flush()
    return True
