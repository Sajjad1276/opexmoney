from __future__ import annotations

import html
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai import get_ai_reply
from app.database.models import (
    CurrencyHolding,
    Nation,
    NationJoinRequest,
    NationLog,
    NationMember,
    NationMemberRole,
    NationWar,
    User,
)
from app.database.session import async_session

logger = logging.getLogger(__name__)

nation_management_router = Router(name="nation_management")

ROLE_LIMITS = {
    NationMemberRole.FOUNDER: 1,
    NationMemberRole.MINISTER: 3,
    NationMemberRole.TRADER: 10,
}

ROLE_TITLE = {
    NationMemberRole.FOUNDER: "👑 بنیان‌گذار",
    NationMemberRole.MINISTER: "🛡 وزیر",
    NationMemberRole.TRADER: "💼 تاجر",
    NationMemberRole.CITIZEN: "👤 شهروند",
}

ROLE_ORDER = {
    NationMemberRole.FOUNDER: 0,
    NationMemberRole.MINISTER: 1,
    NationMemberRole.TRADER: 2,
    NationMemberRole.CITIZEN: 3,
}

JOIN_POLICIES = {
    "OPEN": "🌐 آزاد",
    "APPROVAL": "📝 با تأیید",
    "INVITE_ONLY": "🔐 فقط دعوت",
}

PERSONALITIES = {
    "aggressive": "⚔️ تهاجمی",
    "neutral": "⚖️ متعادل",
    "economic": "📈 اقتصادی",
}

EVENT_EMOJI = {
    "MEMBER_JOIN": "👋",
    "MEMBER_LEAVE": "🚪",
    "MEMBER_KICKED": "⛔",
    "ROLE_PROMOTED": "⬆️",
    "ROLE_DEMOTED": "⬇️",
    "WAR_DECLARED": "⚔️",
    "WAR_ENDED": "🏳️",
    "SANCTION_APPLIED": "🚫",
    "SANCTION_LIFTED": "✅",
    "TRADE_LARGE": "💰",
    "POLICY_CHANGED": "⚙️",
    "TREASURY_DEPOSIT": "💵",
    "TREASURY_WITHDRAW": "💸",
}

EVENT_TITLE = {
    "MEMBER_JOIN": "عضویت جدید",
    "MEMBER_LEAVE": "خروج عضو",
    "MEMBER_KICKED": "اخراج عضو",
    "ROLE_PROMOTED": "ارتقای نقش",
    "ROLE_DEMOTED": "تنزل نقش",
    "WAR_DECLARED": "اعلام جنگ",
    "WAR_ENDED": "پایان جنگ",
    "SANCTION_APPLIED": "اعمال تحریم",
    "SANCTION_LIFTED": "رفع تحریم",
    "TRADE_LARGE": "معامله بزرگ",
    "POLICY_CHANGED": "تغییر سیاست",
    "TREASURY_DEPOSIT": "واریز به خزانه",
    "TREASURY_WITHDRAW": "برداشت از خزانه",
}


class NationManagementStates(StatesGroup):
    announcement = State()


def _now() -> datetime:
    return datetime.utcnow()


def _role_value(role: NationMemberRole | str) -> str:
    return role.value if isinstance(role, NationMemberRole) else str(role)


def _role_enum(role: NationMemberRole | str) -> NationMemberRole:
    return role if isinstance(role, NationMemberRole) else NationMemberRole(str(role))


def _fmt_amount(value: Decimal | int | float | str) -> str:
    try:
        number = Decimal(str(value))
    except Exception:
        number = Decimal("0")
    return f"{number:,.2f}"


def _safe_name(user: User | None, user_id: int | None = None) -> str:
    if user is not None:
        username = (user.username or "").strip()
        if username:
            return f"@{html.escape(username)}"
    return f"کاربر {user_id}" if user_id is not None else "سیستم"


async def _get_member(
    session: AsyncSession,
    nation_id: int,
    user_id: int,
) -> NationMember | None:
    return await session.scalar(
        select(NationMember)
        .where(
            NationMember.nation_id == nation_id,
            NationMember.user_id == user_id,
            NationMember.is_active.is_(True),
        )
        .limit(1)
    )


async def _require_admin(
    session: AsyncSession,
    nation_id: int,
    user_id: int,
) -> NationMember:
    member = await _get_member(session, nation_id, user_id)
    if member is None or _role_enum(member.role) not in {
        NationMemberRole.FOUNDER,
        NationMemberRole.MINISTER,
    }:
        raise ValueError("⛔ فقط بنیان‌گذار و وزیر به پنل مدیریت دسترسی دارند.")
    return member


async def _require_founder(
    session: AsyncSession,
    nation_id: int,
    user_id: int,
) -> NationMember:
    member = await _get_member(session, nation_id, user_id)
    if member is None or _role_enum(member.role) != NationMemberRole.FOUNDER:
        raise ValueError("👑 فقط بنیان‌گذار این عملیات را انجام می‌دهد.")
    return member


async def _append_log(
    session: AsyncSession,
    *,
    nation_id: int,
    actor_id: int | None,
    action_type: str,
    target_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> NationLog:
    log = NationLog(
        nation_id=nation_id,
        actor_id=actor_id,
        action_type=action_type,
        target_id=target_id,
        event_metadata=metadata,
    )
    session.add(log)
    await session.flush()
    return log


async def log_nation_event(
    session: AsyncSession,
    *,
    nation_id: int,
    actor_id: int | None,
    action_type: str,
    target_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> NationLog:
    """Public helper for war/sanction/trade systems to write the canonical nation log."""
    return await _append_log(
        session,
        nation_id=nation_id,
        actor_id=actor_id,
        action_type=action_type,
        target_id=target_id,
        metadata=metadata,
    )


async def get_nation_log(
    nation_id: int,
    limit: int = 20,
) -> list[str]:
    """Return the newest nation events as a readable feed."""
    limit = max(1, min(limit, 20))

    async with async_session() as session:
        rows = (
            await session.execute(
                select(NationLog)
                .where(NationLog.nation_id == nation_id)
                .order_by(NationLog.created_at.desc(), NationLog.id.desc())
                .limit(limit)
            )
        ).scalars().all()

        user_ids = {
            user_id
            for row in rows
            for user_id in (row.actor_id, row.target_id)
            if user_id is not None
        }
        users = {}
        if user_ids:
            users = {
                user.user_id: user
                for user in (
                    await session.execute(
                        select(User).where(User.user_id.in_(user_ids))
                    )
                ).scalars().all()
            }

    feed: list[str] = []
    for row in rows:
        emoji = EVENT_EMOJI.get(row.action_type, "📌")
        title = EVENT_TITLE.get(row.action_type, row.action_type)
        actor = _safe_name(users.get(row.actor_id), row.actor_id)
        target = (
            _safe_name(users.get(row.target_id), row.target_id)
            if row.target_id is not None
            else None
        )
        timestamp = row.created_at.strftime("%m/%d %H:%M") if row.created_at else "--/-- --:--"
        meta = row.event_metadata or {}

        detail = ""
        if row.action_type in {"MEMBER_JOIN", "MEMBER_KICKED", "MEMBER_LEAVE"}:
            detail = target or actor
        elif row.action_type in {"ROLE_PROMOTED", "ROLE_DEMOTED"}:
            detail = f"{target or actor} → {meta.get('role', '?')}"
        elif row.action_type == "TRADE_LARGE":
            detail = f"{meta.get('amount', '?')} {meta.get('currency', '')}".strip()
        elif row.action_type in {"WAR_DECLARED", "WAR_ENDED"}:
            detail = str(meta.get("opponent_name", ""))
        elif row.action_type in {"SANCTION_APPLIED", "SANCTION_LIFTED"}:
            detail = str(meta.get("target_nation", ""))
        elif row.action_type == "POLICY_CHANGED":
            detail = str(meta.get("change", ""))
        elif row.action_type in {"TREASURY_DEPOSIT", "TREASURY_WITHDRAW"}:
            detail = f"{meta.get('amount', '?')} ΩXR"

        actor_part = f" · {actor}" if actor and actor != "سیستم" else ""
        detail_part = f" · {html.escape(detail)}" if detail else ""
        feed.append(
            f"{emoji} <b>{html.escape(title)}</b>{detail_part}{actor_part}\n"
            f"   <code>{timestamp}</code>"
        )

    return feed


async def _member_count(
    session: AsyncSession,
    nation_id: int,
    role: NationMemberRole | None = None,
) -> int:
    stmt = select(func.count(NationMember.id)).where(
        NationMember.nation_id == nation_id,
        NationMember.is_active.is_(True),
    )
    if role is not None:
        stmt = stmt.where(NationMember.role == role)
    return int(await session.scalar(stmt) or 0)


async def nation_admin_panel(
    user_id: int,
    nation_id: int,
) -> InlineKeyboardMarkup:
    """Build the management keyboard. Only founder/minister callers are accepted."""
    async with async_session() as session:
        async with session.begin():
            await _require_admin(session, nation_id, user_id)
            nation = await session.get(Nation, nation_id)
            if nation is None or not nation.is_active:
                raise ValueError("ملت فعال پیدا نشد.")

            count = await _member_count(session, nation_id)
            active_wars = int(
                await session.scalar(
                    select(func.count(NationWar.id)).where(
                        NationWar.nation_id == nation_id,
                        NationWar.status == "active",
                    )
                )
                or 0
            )
            treasury = _fmt_amount(nation.treasury)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"👥 اعضا ({count})",
                    callback_data=f"nm:members:{nation_id}",
                ),
                InlineKeyboardButton(
                    text="📋 لاگ فعالیت",
                    callback_data=f"nm:logs:{nation_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"💰 خزانه ({treasury})",
                    callback_data=f"nm:treasury:{nation_id}",
                ),
                InlineKeyboardButton(
                    text=f"⚔️ جنگ‌های فعال ({active_wars})",
                    callback_data=f"nm:wars:{nation_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 اطلاعیه",
                    callback_data=f"nm:announce:{nation_id}",
                ),
                InlineKeyboardButton(
                    text="⚙️ تنظیمات ملت",
                    callback_data=f"nm:settings:{nation_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 برگشت",
                    callback_data="back_to_dashboard",
                )
            ],
        ]
    )


async def _panel_text(
    session: AsyncSession,
    nation: Nation,
) -> str:
    ministers = await _member_count(session, nation.nation_id, NationMemberRole.MINISTER)
    traders = await _member_count(session, nation.nation_id, NationMemberRole.TRADER)
    citizens = await _member_count(session, nation.nation_id, NationMemberRole.CITIZEN)
    return (
        f"👑 <b>مدیریت {html.escape(nation.name)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 اعضا: <b>{nation.member_count}</b>\n"
        f"🛡 وزیر: <b>{ministers}/3</b>\n"
        f"💼 تاجر: <b>{traders}/10</b>\n"
        f"👤 شهروند: <b>{citizens}</b>\n"
        f"💰 خزانه: <b>{_fmt_amount(nation.treasury)}</b> ΩXR\n"
        f"🧭 سیاست ورود: <b>{html.escape(JOIN_POLICIES.get(nation.join_policy, nation.join_policy))}</b>\n"
        f"🎭 شخصیت: <b>{html.escape(PERSONALITIES.get(nation.personality, nation.personality))}</b>"
    )


async def _notify_founder(
    bot: Bot,
    nation: Nation,
    text: str,
) -> None:
    if nation.founder_user_id is None:
        return
    try:
        await bot.send_message(nation.founder_user_id, text, parse_mode="HTML")
    except Exception:
        logger.exception("Could not notify founder %s", nation.founder_user_id)


async def _ai_nation_text(
    user_id: int,
    nation: Nation,
    task: str,
) -> str:
    prompt = (
        f"تو نویسنده رسمی ملت «{nation.name}» در بازی OPEX MONEY هستی. "
        f"شخصیت ملت: {nation.personality}. "
        f"لحن باید دقیقاً با این شخصیت هماهنگ باشد. "
        f"رویداد/درخواست: {task}. "
        "پاسخ کوتاه، طبیعی، فارسی و مناسب تلگرام تولید کن."
    )
    async with async_session() as session:
        result = await get_ai_reply(user_id, prompt, session)
    return result


async def publish_nation_event_analysis(
    bot: Bot,
    *,
    nation_id: int,
    event_type: str,
    actor_id: int,
    target_id: int | None = None,
    context: str = "",
) -> None:
    """Generate and publish the AI analysis for a major nation event."""
    async with async_session() as session:
        nation = await session.get(Nation, nation_id)

    if nation is None or not nation.is_active:
        return

    analysis = await _ai_nation_text(
        actor_id,
        nation,
        (
            f"یک رویداد مهم {event_type} در ملت رخ داده است. "
            f"زمینه: {context or 'اطلاعات تکمیلی موجود نیست'}. "
            "یک تحلیل یک‌تا‌دو جمله‌ای ارائه کن و اثر احتمالی آن بر اقتصاد/نرخ ارز را "
            "به‌صورت مشروط بیان کن؛ عدد قطعی نساز مگر اینکه در داده ورودی آمده باشد."
        ),
    )

    if nation.group_id is not None:
        try:
            await bot.send_message(
                nation.group_id,
                f"📊 <b>تحلیل اوپکس</b>\n{html.escape(analysis)}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not send nation AI analysis")


async def _welcome_member(
    bot: Bot,
    *,
    nation: Nation,
    user: User,
    actor_id: int,
) -> None:
    welcome = await _ai_nation_text(
        actor_id,
        nation,
        (
            f"کاربر {user.username} به‌تازگی به ملت پیوسته است. "
            "یک پیام خوشامد کوتاه و شخصی‌سازی‌شده از طرف ملت بنویس."
        ),
    )
    try:
        await bot.send_message(
            user.user_id,
            f"🎉 <b>به {html.escape(nation.name)} خوش اومدی!</b>\n{html.escape(welcome)}",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not send welcome message to %s", user.user_id)


async def _join_user(
    *,
    bot: Bot,
    user_id: int,
    nation_id: int,
    invite_code: str | None = None,
) -> tuple[str, Nation | None]:
    pending_admin_ids: list[int] = []
    joined_user: User | None = None
    nation: Nation | None = None
    result_message = ""

    async with async_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User).where(User.user_id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            nation = (
                await session.execute(
                    select(Nation).where(Nation.nation_id == nation_id).with_for_update()
                )
            ).scalar_one_or_none()

            if user is None:
                raise ValueError("⚠️ اول با /start وارد بازی شو.")
            if nation is None or not nation.is_active:
                raise ValueError("⚠️ این ملت فعال نیست.")
            if user.home_nation_id is not None:
                raise ValueError("⚠️ تو همین الان عضو یک ملت هستی.")

            existing = await _get_member(session, nation_id, user_id)
            if existing is not None:
                raise ValueError("⚠️ تو از قبل عضو این ملتی.")

            policy = (nation.join_policy or "OPEN").upper()

            if policy == "INVITE_ONLY" and invite_code != nation.invite_code:
                raise ValueError(
                    "🔐 این ملت فقط با دعوت‌نامه قابل پیوستن است.\n"
                    f"کد دعوت را با این قالب بفرست: /join_nation {nation_id} CODE"
                )

            if policy == "APPROVAL":
                pending = await session.scalar(
                    select(NationJoinRequest).where(
                        NationJoinRequest.nation_id == nation_id,
                        NationJoinRequest.user_id == user_id,
                        NationJoinRequest.status == "pending",
                    ).limit(1)
                )
                if pending is not None:
                    result_message = "📝 درخواستت قبلاً ثبت شده و هنوز در حال بررسیه."
                else:
                    request = NationJoinRequest(
                        nation_id=nation_id,
                        user_id=user_id,
                        status="pending",
                        expires_at=_now() + timedelta(hours=48),
                    )
                    session.add(request)
                    await session.flush()

                    admin_rows = (
                        await session.execute(
                            select(NationMember.user_id).where(
                                NationMember.nation_id == nation_id,
                                NationMember.is_active.is_(True),
                                NationMember.role.in_(
                                    [NationMemberRole.FOUNDER, NationMemberRole.MINISTER]
                                ),
                            )
                        )
                    ).scalars().all()
                    pending_admin_ids = list(admin_rows)
                    result_message = "✅ درخواست عضویت ارسال شد. تا 48 ساعت فرصت بررسی دارد."
                    joined_user = user
            else:
                user.home_nation_id = nation_id
                user.role = "citizen"
                nation.member_count += 1
                holding = await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == user_id,
                        CurrencyHolding.nation_id == nation_id,
                    ).with_for_update()
                )
                if holding is None:
                    user.balance = Decimal("500.00")
                    session.add(
                        CurrencyHolding(
                            user_id=user_id,
                            nation_id=nation_id,
                            amount=Decimal("500.00"),
                        )
                    )
                else:
                    user.balance = holding.amount
                member = NationMember(
                    nation_id=nation_id,
                    user_id=user_id,
                    role=NationMemberRole.CITIZEN,
                    is_active=True,
                )
                session.add(member)
                await session.flush()
                await _append_log(
                    session,
                    nation_id=nation_id,
                    actor_id=user_id,
                    action_type="MEMBER_JOIN",
                    target_id=user_id,
                    metadata={
                        "role": NationMemberRole.CITIZEN.value,
                        "policy": policy,
                        "invite": policy == "INVITE_ONLY",
                    },
                )
                joined_user = user
                result_message = f"🎉 به «{nation.name}» پیوستی!"
    
    if pending_admin_ids and nation is not None and joined_user is not None:
        for admin_id in pending_admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    (
                        f"📝 <b>درخواست عضویت جدید</b>\n"
                        f"🏛 ملت: <b>{html.escape(nation.name)}</b>\n"
                        f"👤 کاربر: <b>{_safe_name(joined_user, joined_user.user_id)}</b>\n"
                        "⏳ اعتبار درخواست: 48 ساعت"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✅ تأیید",
                                    callback_data=f"nm:approve:{nation_id}:{joined_user.user_id}",
                                ),
                                InlineKeyboardButton(
                                    text="❌ رد",
                                    callback_data=f"nm:reject:{nation_id}:{joined_user.user_id}",
                                ),
                            ]
                        ]
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Could not notify join approver %s", admin_id)
        return result_message, nation

    if nation is not None and joined_user is not None:
        await _welcome_member(
            bot,
            nation=nation,
            user=joined_user,
            actor_id=user_id,
        )
        await _notify_founder(
            bot,
            nation,
            (
                f"👋 <b>عضو جدید وارد شد</b>\n"
                f"👤 {_safe_name(joined_user, joined_user.user_id)}\n"
                f"🏛 {html.escape(nation.name)}"
            ),
        )
        await publish_nation_event_analysis(
            bot,
            nation_id=nation.nation_id,
            event_type="MEMBER_JOIN",
            actor_id=user_id,
            target_id=user_id,
            context=f"{_safe_name(joined_user, joined_user.user_id)} به ملت {nation.name} پیوست.",
        )

    return result_message, nation


@nation_management_router.callback_query(F.data.regexp(r"^join_nation:\d+$"))
async def join_from_explore(call: CallbackQuery, bot: Bot) -> None:
    nation_id = int(call.data.split(":")[1])
    try:
        message, _ = await _join_user(
            bot=bot,
            user_id=call.from_user.id,
            nation_id=nation_id,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    if call.message:
        await call.message.answer(message)
    await call.answer()


@nation_management_router.message(Command("join_nation"))
async def join_by_invite_command(message: Message, bot: Bot) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("🔐 قالب درست:\n/join_nation NATION_ID INVITE_CODE")
        return
    try:
        nation_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ شناسه ملت نامعتبره.")
        return

    try:
        text, _ = await _join_user(
            bot=bot,
            user_id=message.from_user.id,
            nation_id=nation_id,
            invite_code=parts[2].strip(),
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await message.answer(text)


@nation_management_router.callback_query(F.data.regexp(r"^(?:nation_admin|nm:panel):(\d+)$"))
async def open_admin_panel(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[1]) if call.data.startswith("nation_admin:") else int(call.data.split(":")[2])
    await open_admin_panel_for_nation(call, nation_id)


@nation_management_router.callback_query(F.data == "founder_panel")
async def founder_panel_entry(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            member = await session.scalar(
                select(NationMember)
                .where(
                    NationMember.user_id == call.from_user.id,