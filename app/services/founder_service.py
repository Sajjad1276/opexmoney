from __future__ import annotations

from datetime import datetime, timedelta
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BotGroup, Nation, NationFoundingDraft, User
from app.services.nation_service import create_nation
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
        draft.status = "GROUP_READY"
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
                founder_user_id=founder_user_id,
                group_id=group_id,
                nation_name=nation_name,
                currency_code=draft.currency_code,
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

