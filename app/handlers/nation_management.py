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
from sqlalchemy import and_, func, or_, select
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
from app.services.mission_service import increment_mission
from app.services.nation_service import convert_holding_to_xr
from app.services.war_service import declare_war
from app.services.user_service import sync_user_balance

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
    "DRAW": "🤝",
    "SANCTION_APPLIED": "🚫",
    "SANCTION_LIFTED": "✅",
    "TRADE_LARGE": "💰",
    "POLICY_CHANGED": "⚙️",
    "TREASURY_DEPOSIT": "💵",
    "TREASURY_WITHDRAW": "💸",
    "HOLDING_LIQUIDATED": "💱",
}

EVENT_TITLE = {
    "MEMBER_JOIN": "عضویت جدید",
    "MEMBER_LEAVE": "خروج عضو",
    "MEMBER_KICKED": "اخراج عضو",
    "ROLE_PROMOTED": "ارتقای نقش",
    "ROLE_DEMOTED": "تنزل نقش",
    "WAR_DECLARED": "اعلام جنگ",
    "WAR_ENDED": "پایان جنگ",
    "DRAW": "تساوی جنگ",
    "SANCTION_APPLIED": "اعمال تحریم",
    "SANCTION_LIFTED": "رفع تحریم",
    "TRADE_LARGE": "معامله بزرگ",
    "POLICY_CHANGED": "تغییر سیاست",
    "TREASURY_DEPOSIT": "واریز به خزانه",
    "TREASURY_WITHDRAW": "برداشت از خزانه",
    "HOLDING_LIQUIDATED": "تصفیه دارایی ارزی",
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
        elif row.action_type in {"WAR_DECLARED", "WAR_ENDED", "DRAW"}:
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
                        NationWar.status == "active",
                        or_(
                            NationWar.nation_id == nation_id,
                            NationWar.opponent_nation_id == nation_id,
                        ),
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
                    callback_data=f"treasury:show:{nation_id}",
                ),
                InlineKeyboardButton(
                    text=f"⚔️ جنگ‌های فعال ({active_wars})",
                    callback_data=f"nm:wars:{nation_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚔️ اعلام جنگ",
                    callback_data=f"nm:war_targets:{nation_id}",
                )
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
    joined_confirmed = False
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
                    "🔐 این ملت فقط با کد دعوت عضو جدید می‌پذیره.\n"
                    f"کد رو با این قالب بفرست: /join_nation {nation_id} CODE"
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
                # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                user.home_nation_id = nation_id
                await sync_user_balance(session, user_id)
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
                joined_confirmed = True
                result_message = f"🎉 به «{nation.name}» پیوستی!"
    
    if joined_confirmed and joined_user is not None:
        try:
            async with async_session() as session:
                async with session.begin():
                    await increment_mission(
                        session,
                        joined_user.user_id,
                        "JOIN_NATION",
                    )
        except Exception:
            logger.exception(
                "Mission trigger failed after nation join for user %s",
                joined_user.user_id,
            )

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
                    NationMember.is_active.is_(True),
                    NationMember.role == NationMemberRole.FOUNDER,
                )
                .limit(1)
            )
    if member is None:
        await call.answer("⛔ تو بنیان‌گذار هیچ ملتی نیستی.", show_alert=True)
        return

    await open_admin_panel_for_nation(call, member.nation_id)


async def open_admin_panel_for_nation(call: CallbackQuery, nation_id: int) -> None:
    try:
        markup = await nation_admin_panel(call.from_user.id, nation_id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)
            if nation is None:
                await call.answer("⚠️ ملت پیدا نشد.", show_alert=True)
                return
            text = await _panel_text(session, nation)

    if call.message:
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:members:\d+$"))
async def show_members(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
                rows = (
                    await session.execute(
                        select(NationMember, User)
                        .join(User, User.user_id == NationMember.user_id)
                        .where(
                            NationMember.nation_id == nation_id,
                            NationMember.is_active.is_(True),
                        )
                        .order_by(NationMember.role.asc(), NationMember.joined_at.asc())
                        .limit(30)
                    )
                ).all()
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    text_lines = ["👥 <b>اعضای ملت</b>"]
    buttons: list[list[InlineKeyboardButton]] = []
    for member, user in rows:
        role = _role_enum(member.role)
        text_lines.append(
            f"{ROLE_TITLE[role]} · {_safe_name(user, user.user_id)}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"مدیریت {_safe_name(user, user.user_id)}",
                    callback_data=f"nm:member:{nation_id}:{user.user_id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")])

    if call.message:
        await call.message.edit_text(
            "\n".join(text_lines) if len(text_lines) > 1 else "👥 هنوز عضوی ثبت نشده.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:member:\d+:\d+$"))
async def member_management(call: CallbackQuery) -> None:
    _, _, nation_id, user_id = call.data.split(":")
    nation_id = int(nation_id)
    user_id = int(user_id)

    try:
        async with async_session() as session:
            async with session.begin():
                actor = await _require_admin(session, nation_id, call.from_user.id)
                member = await _get_member(session, nation_id, user_id)
                user = await session.get(User, user_id)
                if member is None or user is None:
                    raise ValueError("عضو پیدا نشد.")
                role = _role_enum(member.role)
                actor_role = _role_enum(actor.role)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    rows: list[list[InlineKeyboardButton]] = []
    if actor_role == NationMemberRole.FOUNDER and role != NationMemberRole.FOUNDER:
        if role == NationMemberRole.MINISTER:
            rows.append([
                InlineKeyboardButton(
                    text="⬇️ تبدیل به شهروند",
                    callback_data=f"nm:setrole:{nation_id}:{user_id}:citizen",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="⬆️ وزیر",
                    callback_data=f"nm:setrole:{nation_id}:{user_id}:minister",
                )
            ])
        if role != NationMemberRole.CITIZEN:
            rows.append([
                InlineKeyboardButton(
                    text="⬇️ شهروند",
                    callback_data=f"nm:setrole:{nation_id}:{user_id}:citizen",
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="⛔ اخراج",
                callback_data=f"nm:kick:{nation_id}:{user_id}",
            )
        ])
    elif actor_role == NationMemberRole.MINISTER and role == NationMemberRole.CITIZEN:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="⬆️ تاجر",
                        callback_data=f"nm:setrole:{nation_id}:{user_id}:trader",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⛔ اخراج",
                        callback_data=f"nm:kick:{nation_id}:{user_id}",
                    )
                ],
            ]
        )

    rows.append([InlineKeyboardButton(text="↩️ اعضا", callback_data=f"nm:members:{nation_id}")])
    text = (
        f"👤 <b>{_safe_name(user, user_id)}</b>\n"
        f"{ROLE_TITLE[role]}\n"
        f"📅 عضویت: {member.joined_at.strftime('%Y-%m-%d') if member.joined_at else '---'}"
    )
    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await call.answer()


async def _set_member_role(
    *,
    bot: Bot,
    actor_id: int,
    nation_id: int,
    target_id: int,
    new_role: NationMemberRole,
) -> None:
    nation: Nation | None = None
    user: User | None = None

    async with async_session() as session:
        async with session.begin():
            actor = await _require_admin(session, nation_id, actor_id)
            nation = await session.get(Nation, nation_id, with_for_update=True)
            member = await _get_member(session, nation_id, target_id)
            user = await session.get(User, target_id, with_for_update=True)

            if nation is None or not nation.is_active or member is None or user is None:
                raise ValueError("عضو یا ملت پیدا نشد.")

            actor_role = _role_enum(actor.role)
            old_role = _role_enum(member.role)

            if old_role == NationMemberRole.FOUNDER:
                raise ValueError("👑 بنیان‌گذار قابل تغییر نیست.")
            if target_id == actor_id:
                raise ValueError("⚠️ نقش خودت رو از این پنل تغییر نده.")
            if actor_role == NationMemberRole.MINISTER and new_role != NationMemberRole.TRADER:
                raise ValueError("⛔ وزیر فقط می‌تونه شهروند رو به تاجر ارتقا بده.")
            if actor_role == NationMemberRole.MINISTER and old_role != NationMemberRole.CITIZEN:
                raise ValueError("⛔ وزیر فقط روی شهروندها اختیار انتصاب داره.")
            if actor_role == NationMemberRole.FOUNDER and new_role == NationMemberRole.FOUNDER:
                raise ValueError("⚠️ فقط یک بنیان‌گذار مجاز است.")

            if old_role == new_role:
                raise ValueError("⚠️ این عضو همین حالا این نقشه را دارد.")

            current_count = await _member_count(session, nation_id, new_role)
            limit = ROLE_LIMITS.get(new_role)
            if limit is not None and current_count >= limit:
                raise ValueError(
                    f"⚠️ ظرفیت {ROLE_TITLE[new_role]} پر شده ({limit} نفر)."
                )

            member.role = new_role
            user.role = (
                "founder"
                if new_role == NationMemberRole.FOUNDER
                else "minister"
                if new_role == NationMemberRole.MINISTER
                else "trader"
                if new_role == NationMemberRole.TRADER
                else "citizen"
            )

            action = (
                "ROLE_PROMOTED"
                if ROLE_ORDER[new_role] < ROLE_ORDER[old_role]
                else "ROLE_DEMOTED"
            )
            await _append_log(
                session,
                nation_id=nation_id,
                actor_id=actor_id,
                action_type=action,
                target_id=target_id,
                metadata={
                    "from_role": old_role.value,
                    "role": new_role.value,
                },
            )

    if nation is not None and user is not None:
        try:
            await bot.send_message(
                target_id,
                (
                    f"📌 <b>نقشت در {html.escape(nation.name)} تغییر کرد.</b>\n"
                    f"{ROLE_TITLE[new_role]}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify role change")
        await publish_nation_event_analysis(
            bot,
            nation_id=nation_id,
            event_type="ROLE_CHANGE",
            actor_id=actor_id,
            target_id=target_id,
            context=f"نقش {user.username} از {new_role.value} به‌روزرسانی شد.",
        )


@nation_management_router.callback_query(
    F.data.regexp(r"^nm:setrole:\d+:\d+:(minister|trader|citizen)$")
)
async def set_member_role(call: CallbackQuery, bot: Bot) -> None:
    _, _, nation_id, user_id, role = call.data.split(":")
    try:
        await _set_member_role(
            bot=bot,
            actor_id=call.from_user.id,
            nation_id=int(nation_id),
            target_id=int(user_id),
            new_role=NationMemberRole(role),
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.answer("✅ نقش عضو تغییر کرد.")


@nation_management_router.callback_query(F.data.regexp(r"^nm:kick:\d+:\d+$"))
async def kick_member(call: CallbackQuery, bot: Bot) -> None:
    _, _, nation_id, user_id = call.data.split(":")
    nation_id = int(nation_id)
    user_id = int(user_id)

    nation: Nation | None = None
    target: User | None = None

    async with async_session() as session:
        async with session.begin():
            actor = await _require_admin(session, nation_id, call.from_user.id)
            nation = await session.get(Nation, nation_id, with_for_update=True)
            member = await session.scalar(
                select(NationMember)
                .where(
                    NationMember.nation_id == nation_id,
                    NationMember.user_id == user_id,
                    NationMember.is_active.is_(True),
                )
                .with_for_update()
            )
            target = await session.get(User, user_id, with_for_update=True)
            if nation is None or member is None or target is None:
                raise ValueError("عضو پیدا نشد.")

            actor_role = _role_enum(actor.role)
            target_role = _role_enum(member.role)
            if target_role == NationMemberRole.FOUNDER:
                raise ValueError("👑 بنیان‌گذار اخراج نمی‌شود.")
            if actor_role == NationMemberRole.MINISTER and target_role != NationMemberRole.CITIZEN:
                raise ValueError("⛔ وزیر فقط می‌تواند شهروند اخراج کند.")

            # ECONOMIC RULE: holdings liquidate to XR on kick/dissolve wherever you touch this logic
            await convert_holding_to_xr(target, nation, session)
            member.is_active = False
            # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
            target.home_nation_id = None
            target.role = "player"
            nation.member_count = max(0, nation.member_count - 1)

            await _append_log(
                session,
                nation_id=nation_id,
                actor_id=call.from_user.id,
                action_type="MEMBER_KICKED",
                target_id=user_id,
                metadata={"previous_role": target_role.value},
            )

    await call.answer("⛔ عضو اخراج شد.")
    if target is not None and nation is not None:
        try:
            await bot.send_message(
                user_id,
                f"⛔ از ملت «{html.escape(nation.name)}» اخراج شدی.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify kicked user")
        await publish_nation_event_analysis(
            bot,
            nation_id=nation_id,
            event_type="MEMBER_KICKED",
            actor_id=call.from_user.id,
            target_id=user_id,
            context=f"عضو با نقش قبلی {target_role.value} اخراج شد.",
        )


@nation_management_router.callback_query(F.data.regexp(r"^nm:logs:\d+$"))
async def show_logs(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
        feed = await get_nation_log(nation_id, 20)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    text = "📋 <b>لاگ فعالیت ملت · 20 رویداد آخر</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "\n\n".join(feed) if feed else "هنوز رویدادی ثبت نشده."
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")]]
    )
    if call.message:
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:wars:\d+$"))
async def show_active_wars(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
                wars = (
                    await session.execute(
                        select(NationWar)
                        .where(
                            NationWar.status == "active",
                            or_(
                                NationWar.nation_id == nation_id,
                                NationWar.opponent_nation_id == nation_id,
                            ),
                        )
                        .order_by(NationWar.declared_at.desc())
                        .limit(20)
                    )
                ).scalars().all()

                opponent_ids = [
                    war.opponent_nation_id
                    if war.nation_id == nation_id
                    else war.nation_id
                    for war in wars
                ]
                names = {}
                if opponent_ids:
                    names = {
                        nation.nation_id: nation.name
                        for nation in (
                            await session.execute(
                                select(Nation).where(Nation.nation_id.in_(opponent_ids))
                            )
                        ).scalars().all()
                    }
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    lines = ["⚔️ <b>جنگ‌های فعال</b>"]
    if wars:
        for war in wars:
            opponent_id = (
                war.opponent_nation_id
                if war.nation_id == nation_id
                else war.nation_id
            )
            opponent_name = names.get(opponent_id, "ملت ناشناخته")
            declared = war.declared_at.strftime("%Y-%m-%d %H:%M") if war.declared_at else "---"
            ends = war.ends_at.strftime("%Y-%m-%d %H:%M") if war.ends_at else "---"
            lines.append(
                f"⚔️ <b>{html.escape(opponent_name)}</b> · "
                f"شروع: {declared} · پایان: {ends} UTC"
            )
    else:
        lines.append("🕊 در حال حاضر جنگ فعالی ثبت نشده.")

    if call.message:
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")]
                ]
            ),
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:war_targets:\d+$"))
async def show_war_targets(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
                active_war = select(NationWar.id).where(
                    NationWar.status == "active",
                    or_(
                        and_(
                            NationWar.nation_id == nation_id,
                            NationWar.opponent_nation_id == Nation.nation_id,
                        ),
                        and_(
                            NationWar.nation_id == Nation.nation_id,
                            NationWar.opponent_nation_id == nation_id,
                        ),
                    ),
                ).exists()

                targets = (
                    await session.execute(
                        select(Nation)
                        .where(
                            Nation.is_active.is_(True),
                            Nation.nation_id != nation_id,
                            ~active_war,
                        )
                        .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
                        .limit(30)
                    )
                ).scalars().all()
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    if not targets:
        if call.message:
            await call.message.edit_text(
                "⚔️ <b>اعلام جنگ</b>\n\nهیچ ملت فعال دیگری برای شروع جنگ در دسترس نیست.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")]
                    ]
                ),
                parse_mode="HTML",
            )
        await call.answer()
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"⚔️ {html.escape(target.name)} · {target.currency_code}",
            callback_data=f"nm:war_target:{nation_id}:{target.nation_id}",
        )]
        for target in targets
    ]
    buttons.append([InlineKeyboardButton(text="❌ لغو", callback_data=f"nm:panel:{nation_id}")])

    if call.message:
        await call.message.edit_text(
            "⚔️ <b>انتخاب هدف</b>\n\nیک ملت را برای اعلام جنگ انتخاب کن.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:war_target:\d+:\d+$"))
async def confirm_war_target(call: CallbackQuery) -> None:
    _, _, nation_id, target_id = call.data.split(":")
    nation_id = int(nation_id)
    target_id = int(target_id)

    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
                nation = await session.get(Nation, nation_id)
                target = await session.get(Nation, target_id)
                if nation is None or target is None or not nation.is_active or not target.is_active:
                    raise ValueError("⚠️ ملت هدف دیگر فعال نیست.")

                active_war = await session.scalar(
                    select(NationWar.id).where(
                        NationWar.status == "active",
                        or_(
                            and_(NationWar.nation_id == nation_id, NationWar.opponent_nation_id == target_id),
                            and_(NationWar.nation_id == target_id, NationWar.opponent_nation_id == nation_id),
                        ),
                    ).limit(1)
                )
                if active_war is not None:
                    raise ValueError("⚔️ این دو ملت همین حالا در حال جنگ هستند.")
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    if call.message:
        await call.message.edit_text(
            "⚠️ <b>تأیید اعلام جنگ</b>\n\n"
            f"🏴 ملت تو: <b>{html.escape(nation.name)}</b>\n"
            f"🎯 هدف: <b>{html.escape(target.name)}</b>\n\n"
            "⏳ جنگ 48 ساعت طول می‌کشد.\n"
            "📈 در پایان، نرخ ارز بالاتر برنده است.\n"
            "💸 ملت بازنده 10٪ از خزانه خود را، تا سقف موجودی، به برنده می‌دهد.\n\n"
            "این عملیات را تأیید می‌کنی؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="⚔️ تأیید جنگ", callback_data=f"nm:war_confirm:{nation_id}:{target_id}"),
                        InlineKeyboardButton(text="❌ لغو", callback_data=f"nm:war_targets:{nation_id}"),
                    ]
                ]
            ),
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:war_confirm:\d+:\d+$"))
async def execute_war_declaration(call: CallbackQuery, bot: Bot) -> None:
    _, _, nation_id, target_id = call.data.split(":")
    nation_id = int(nation_id)
    target_id = int(target_id)

    async with async_session() as session:
        try:
            result = await declare_war(
                declaring_nation_id=nation_id,
                target_nation_id=target_id,
                session=session,
                actor_user_id=call.from_user.id,
                bot=bot,
            )
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return

    await call.answer("⚔️ جنگ اعلام شد.")
    if call.message:
        await call.message.edit_text(
            "⚔️ <b>جنگ اعلام شد</b>\n\n"
            f"🏴 {html.escape(result['declaring_name'])}\n"
            f"🎯 هدف: {html.escape(result['target_name'])}\n"
            "⏳ مدت: 48 ساعت",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⚔️ جنگ‌های فعال", callback_data=f"nm:wars:{nation_id}")],
                    [InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")],
                ]
            ),
            parse_mode="HTML",
        )
    await call.answer("⚔️ جنگ اعلام شد.")


@nation_management_router.callback_query(F.data.regexp(r"^nm:announce:\d+$"))
async def start_announcement(call: CallbackQuery, state: FSMContext) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                await _require_admin(session, nation_id, call.from_user.id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await state.update_data(nation_id=nation_id)
    await state.set_state(NationManagementStates.announcement)
    await call.answer()
    if call.message:
        await call.message.answer(
            "📢 متن اطلاعیه را بفرست. این پیام در گروه/پایتخت ملت منتشر می‌شود.\n"
            "برای لغو: /cancel"
        )


@nation_management_router.message(
    StateFilter(NationManagementStates.announcement),
    F.text,
)
async def publish_announcement(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    nation_id = data.get("nation_id")
    if not nation_id:
        await state.clear()
        return

    async with async_session() as session:
        async with session.begin():
            await _require_admin(session, int(nation_id), message.from_user.id)
            nation = await session.get(Nation, int(nation_id))
            if nation is None or nation.group_id is None:
                raise ValueError("پایتخت ملت در دسترس نیست.")

            text = html.escape(message.text or "").strip()
            if not text:
                raise ValueError("متن اطلاعیه خالی است.")

    await bot.send_message(
        nation.group_id,
        f"📢 <b>اطلاعیه رسمی {html.escape(nation.name)}</b>\n\n{text}",
        parse_mode="HTML",
    )
    await state.clear()
    await message.answer("✅ اطلاعیه منتشر شد.")


@nation_management_router.message(Command("cancel"), StateFilter(NationManagementStates.announcement))
async def cancel_announcement(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ لغو شد.")


@nation_management_router.callback_query(F.data.regexp(r"^nm:settings:\d+$"))
async def nation_settings(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    try:
        async with async_session() as session:
            async with session.begin():
                actor = await _require_admin(session, nation_id, call.from_user.id)
                nation = await session.get(Nation, nation_id)
                if nation is None:
                    raise ValueError("ملت پیدا نشد.")
                founder = _role_enum(actor.role) == NationMemberRole.FOUNDER
                text = (
                    f"⚙️ <b>تنظیمات {html.escape(nation.name)}</b>\n"
                    f"🚪 سیاست ورود: <b>{html.escape(JOIN_POLICIES.get(nation.join_policy, nation.join_policy))}</b>\n"
                    f"🎭 شخصیت: <b>{html.escape(PERSONALITIES.get(nation.personality, nation.personality))}</b>\n"
                    f"🔐 کد دعوت: <code>{html.escape(nation.invite_code)}</code>"
                )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    rows = []
    if founder:
        rows.extend(
            [
                [
                    InlineKeyboardButton(text="🌐 ورود آزاد", callback_data=f"nm:policy:{nation_id}:OPEN"),
                    InlineKeyboardButton(text="📝 با تأیید", callback_data=f"nm:policy:{nation_id}:APPROVAL"),
                ],
                [
                    InlineKeyboardButton(text="🔐 فقط دعوت", callback_data=f"nm:policy:{nation_id}:INVITE_ONLY"),
                ],
                [
                    InlineKeyboardButton(text="⚔️ تهاجمی", callback_data=f"nm:personality:{nation_id}:aggressive"),
                    InlineKeyboardButton(text="⚖️ متعادل", callback_data=f"nm:personality:{nation_id}:neutral"),
                ],
                [
                    InlineKeyboardButton(text="📈 اقتصادی", callback_data=f"nm:personality:{nation_id}:economic"),
                ],
                [
                    InlineKeyboardButton(text="🔄 کد دعوت جدید", callback_data=f"nm:regen:{nation_id}"),
                    InlineKeyboardButton(text="☠️ انحلال ملت", callback_data=f"nm:dissolve:{nation_id}"),
                ],
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="ℹ️ تغییر تنظیمات فقط دست بنیان‌گذار است.", callback_data=f"nm:noop:{nation_id}")]
        )
    rows.append([InlineKeyboardButton(text="↩️ مدیریت ملت", callback_data=f"nm:panel:{nation_id}")])

    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:noop:\d+$"))
async def settings_read_only_notice(call: CallbackQuery) -> None:
    await call.answer("ℹ️ تغییر تنظیمات فقط در اختیار بنیان‌گذار است.", show_alert=True)


@nation_management_router.callback_query(F.data.regexp(r"^nm:policy:\d+:(OPEN|APPROVAL|INVITE_ONLY)$"))
async def set_join_policy(call: CallbackQuery) -> None:
    _, _, nation_id, policy = call.data.split(":")
    nation_id = int(nation_id)
    async with async_session() as session:
        try:
            async with session.begin():
                await _require_founder(session, nation_id, call.from_user.id)
                nation = await session.get(Nation, nation_id, with_for_update=True)
                if nation is None:
                    raise ValueError("ملت پیدا نشد.")
                old = nation.join_policy
                nation.join_policy = policy
                await _append_log(
                    session,
                    nation_id=nation_id,
                    actor_id=call.from_user.id,
                    action_type="POLICY_CHANGED",
                    metadata={
                        "change": f"join_policy: {old} -> {policy}",
                    },
                )
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
    await call.answer("✅ سیاست ورود به‌روزرسانی شد.")


@nation_management_router.callback_query(F.data.regexp(r"^nm:personality:\d+:(aggressive|neutral|economic)$"))
async def set_personality(call: CallbackQuery) -> None:
    _, _, nation_id, personality = call.data.split(":")
    nation_id = int(nation_id)
    async with async_session() as session:
        try:
            async with session.begin():
                await _require_founder(session, nation_id, call.from_user.id)
                nation = await session.get(Nation, nation_id, with_for_update=True)
                if nation is None:
                    raise ValueError("ملت پیدا نشد.")
                old = nation.personality
                nation.personality = personality
                await _append_log(
                    session,
                    nation_id=nation_id,
                    actor_id=call.from_user.id,
                    action_type="POLICY_CHANGED",
                    metadata={
                        "change": f"personality: {old} -> {personality}",
                    },
                )
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
    await call.answer("✅ شخصیت ملت تغییر کرد.")


@nation_management_router.callback_query(F.data.regexp(r"^nm:regen:\d+$"))
async def regenerate_invite_code(call: CallbackQuery) -> None:
    nation_id = int(call.data.split(":")[2])
    async with async_session() as session:
        try:
            async with session.begin():
                await _require_founder(session, nation_id, call.from_user.id)
                nation = await session.get(Nation, nation_id, with_for_update=True)
                if nation is None:
                    raise ValueError("ملت پیدا نشد.")
                nation.invite_code = f"OPX-{secrets.token_urlsafe(12)}"
                await _append_log(
                    session,
                    nation_id=nation_id,
                    actor_id=call.from_user.id,
                    action_type="POLICY_CHANGED",
                    metadata={"change": "invite_code_regenerated"},
                )
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
    await call.answer("🔄 کد دعوت جدید ساخته شد.", show_alert=True)


@nation_management_router.callback_query(F.data.regexp(r"^nm:dissolve:\d+$"))
async def dissolve_nation(call: CallbackQuery, bot: Bot) -> None:
    nation_id = int(call.data.split(":")[2])

    try:
        async with async_session() as session:
            async with session.begin():
                await _require_founder(session, nation_id, call.from_user.id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="☠️ تأیید انحلال",
                    callback_data=f"nm:confirm_dissolve:{nation_id}",
                ),
                InlineKeyboardButton(
                    text="❌ لغو",
                    callback_data=f"nm:settings:{nation_id}",
                ),
            ]
        ]
    )
    if call.message:
        await call.message.edit_text(
            "☠️ <b>انحلال ملت</b>\n"
            "تمام اعضا از ملت خارج می‌شوند و عضویت جدید متوقف می‌شود.\n"
            "این عملیات برگشت‌پذیر نیست.",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:confirm_dissolve:\d+$"))
async def confirm_dissolve(call: CallbackQuery, bot: Bot) -> None:
    nation_id = int(call.data.split(":")[2])
    group_id: int | None = None
    nation_name = ""

    async with async_session() as session:
        async with session.begin():
            await _require_founder(session, nation_id, call.from_user.id)
            nation = await session.get(Nation, nation_id, with_for_update=True)
            if nation is None or not nation.is_active:
                raise ValueError("ملت پیدا نشد.")

            group_id = nation.group_id
            nation_name = nation.name
            members = (
                await session.execute(
                    select(NationMember, User)
                    .join(User, User.user_id == NationMember.user_id)
                    .where(
                        NationMember.nation_id == nation_id,
                        NationMember.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()

            for member, user in members:
                # ECONOMIC RULE: holdings liquidate to XR on kick/dissolve wherever you touch this logic
                await convert_holding_to_xr(user, nation, session)
                member.is_active = False
                # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                user.home_nation_id = None
                user.role = "player"

            nation.member_count = 0
            nation.is_active = False
            await _append_log(
                session,
                nation_id=nation_id,
                actor_id=call.from_user.id,
                action_type="POLICY_CHANGED",
                metadata={"change": "nation_dissolved"},
            )

    if group_id is not None:
        try:
            await bot.send_message(
                group_id,
                f"☠️ ملت «{html.escape(nation_name)}» توسط بنیان‌گذار منحل شد.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not announce nation dissolution")

    if call.message:
        await call.message.edit_text("☠️ ملت منحل شد.")
    await call.answer()


@nation_management_router.callback_query(F.data.regexp(r"^nm:approve:\d+:\d+$"))
async def approve_join_request(call: CallbackQuery, bot: Bot) -> None:
    _, _, nation_id, user_id = call.data.split(":")
    nation_id = int(nation_id)
    user_id = int(user_id)

    nation: Nation | None = None
    user: User | None = None
    outcome = "not_found"

    async with async_session() as session:
        async with session.begin():
            await _require_admin(session, nation_id, call.from_user.id)

            nation = await session.get(Nation, nation_id, with_for_update=True)
            actor = await _get_member(session, nation_id, call.from_user.id)
            request = await session.scalar(
                select(NationJoinRequest)
                .where(
                    NationJoinRequest.nation_id == nation_id,
                    NationJoinRequest.user_id == user_id,
                    NationJoinRequest.status == "pending",
                )
                .with_for_update()
                .limit(1)
            )
            user = await session.get(User, user_id, with_for_update=True)

            if nation is None or actor is None or request is None or user is None:
                raise ValueError("⚠️ درخواست پیدا نشد.")

            now = _now()
            if request.expires_at <= now:
                request.status = "expired"
                request.reviewed_by = call.from_user.id
                request.reviewed_at = now
                outcome = "expired"
            elif user.home_nation_id is not None:
                request.status = "rejected"
                request.reviewed_by = call.from_user.id
                request.reviewed_at = now
                outcome = "already_member"
            else:
                request.status = "approved"
                request.reviewed_by = call.from_user.id
                request.reviewed_at = now
                user.role = "citizen"
                nation.member_count += 1
                holding = await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == user_id,
                        CurrencyHolding.nation_id == nation_id,
                    ).with_for_update()
                )
                if holding is None:
                    holding = CurrencyHolding(
                        user_id=user_id,
                        nation_id=nation_id,
                        amount=Decimal("500.00"),
                    )
                    session.add(holding)
                user.balance = holding.amount

                session.add(
                    NationMember(
                        nation_id=nation_id,
                        user_id=user_id,
                        role=NationMemberRole.CITIZEN,
                        is_active=True,
                    )
                )
                await session.flush()
                # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                user.home_nation_id = nation_id
                await sync_user_balance(session, user_id)
                await _append_log(
                    session,
                    nation_id=nation_id,
                    actor_id=call.from_user.id,
                    action_type="MEMBER_JOIN",
                    target_id=user_id,
                    metadata={
                        "role": NationMemberRole.CITIZEN.value,
                        "policy": "APPROVAL",
                        "approved_by": call.from_user.id,
                    },
                )
                outcome = "approved"

    if outcome == "expired":
        await call.answer("⌛ مهلت درخواست تمام شده و درخواست منقضی شد.", show_alert=True)
        if user is not None:
            try:
                await bot.send_message(
                    user.user_id,
                    f"⌛ درخواست عضویتت در «{html.escape(nation.name if nation else 'این ملت')}» منقضی شد.",
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Could not notify expired approval request %s", user_id)
        return

    if outcome == "already_member":
        await call.answer("⚠️ این کاربر قبلاً عضو یک ملت شده؛ درخواست رد شد.", show_alert=True)
        return

    if outcome != "approved" or nation is None or user is None:
        await call.answer("⚠️ درخواست دیگر قابل تأیید نیست.", show_alert=True)
        return

    await call.answer("✅ درخواست تأیید شد.")
    await _welcome_member(bot, nation=nation, user=user, actor_id=call.from_user.id)
    await _notify_founder(
        bot,
        nation,
        f"✅ درخواست عضویت {_safe_name(user, user.user_id)} تأیید شد.",
    )
    await publish_nation_event_analysis(
        bot,
        nation_id=nation_id,
        event_type="MEMBER_JOIN",
        actor_id=call.from_user.id,
        target_id=user_id,
        context="درخواست عضویت پس از بررسی مدیران تأیید شد.",
    )


@nation_management_router.callback_query(F.data.regexp(r"^nm:reject:\d+:\d+$"))
async def reject_join_request(call: CallbackQuery, bot: Bot) -> None:
    _, _, nation_id, user_id = call.data.split(":")
    nation_id = int(nation_id)
    user_id = int(user_id)
    user: User | None = None
    nation: Nation | None = None

    async with async_session() as session:
        async with session.begin():
            await _require_admin(session, nation_id, call.from_user.id)
            request = await session.scalar(
                select(NationJoinRequest)
                .where(
                    NationJoinRequest.nation_id == nation_id,
                    NationJoinRequest.user_id == user_id,
                    NationJoinRequest.status == "pending",
                )
                .with_for_update()
                .limit(1)
            )
            nation = await session.get(Nation, nation_id)
            user = await session.get(User, user_id)
            if request is None:
                raise ValueError("⚠️ درخواست پیدا نشد.")
            request.status = "rejected"
            request.reviewed_by = call.from_user.id
            request.reviewed_at = _now()

    await call.answer("❌ درخواست رد شد.")
    if user is not None and nation is not None:
        try:
            await bot.send_message(
                user.user_id,
                f"❌ درخواست عضویتت در «{html.escape(nation.name)}» رد شد.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify rejected join request")


async def expire_join_requests(bot: Bot) -> int:
    """Auto-reject approval requests older than 48 hours and notify applicants."""
    expired: list[tuple[int, int, str, int | None]] = []
    async with async_session() as session:
        async with session.begin():
            requests = (
                await session.execute(
                    select(NationJoinRequest, Nation.name, Nation.founder_user_id)
                    .join(Nation, Nation.nation_id == NationJoinRequest.nation_id)
                    .where(
                        NationJoinRequest.status == "pending",
                        NationJoinRequest.expires_at <= _now(),
                    )
                    .with_for_update()
                    .limit(200)
                )
            ).all()

            for request, nation_name, founder_id in requests:
                request.status = "expired"
                request.reviewed_at = _now()
                await _append_log(
                    session,
                    nation_id=request.nation_id,
                    actor_id=None,
                    action_type="MEMBER_LEAVE",
                    target_id=request.user_id,
                    metadata={"reason": "approval_request_expired"},
                )
                expired.append(
                    (
                        request.user_id,
                        request.nation_id,
                        nation_name,
                        founder_id,
                    )
                )

    for user_id, nation_id, nation_name, _ in expired:
        try:
            await bot.send_message(
                user_id,
                f"⌛ درخواست عضویتت در «{html.escape(nation_name)}» به‌دلیل پایان مهلت 48 ساعته رد شد.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify expired request %s", user_id)

    return len(expired)


@nation_management_router.message(
    F.chat.type.in_({"group", "supergroup"}),
    Command("nation_announce"),
)
async def trader_public_announcement(message: Message, bot: Bot) -> None:
    if not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("📢 قالب: /nation_announce متن پیام")
        return

    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(
                select(Nation)
                .where(
                    Nation.group_id == message.chat.id,
                    Nation.is_active.is_(True),
                )
                .limit(1)
            )
            if nation is None:
                return
            member = await _get_member(session, nation.nation_id, message.from_user.id)
            if member is None:
                return
            role = _role_enum(member.role)
            if role not in {
                NationMemberRole.FOUNDER,
                NationMemberRole.MINISTER,
                NationMemberRole.TRADER,
            }:
                await message.reply("⛔ این دستور فقط برای بنیان‌گذار، وزیر و تاجر است.")
                return
            announcement = html.escape(parts[1].strip())
            if not announcement:
                await message.reply("⚠️ متن خالی است.")
                return

    await bot.send_message(
        message.chat.id,
        f"📢 <b>پیام رسمی {html.escape(nation.name)}</b>\n\n{announcement}",
        parse_mode="HTML",
    )


async def send_weekly_nation_reports(bot: Bot) -> int:
    """Generate one AI economic report per active nation and send it to its founder."""
    sent_count = 0
    since = _now() - timedelta(days=7)

    async with async_session() as session:
        nations = (
            await session.execute(
                select(Nation).where(
                    Nation.is_active.is_(True),
                    Nation.founder_user_id.is_not(None),
                )
            )
        ).scalars().all()

    for nation in nations:
        async with async_session() as session:
            total_events = int(
                await session.scalar(
                    select(func.count(NationLog.id)).where(
                        NationLog.nation_id == nation.nation_id,
                        NationLog.created_at >= since,
                    )
                )
                or 0
            )
            large_trades = int(
                await session.scalar(
                    select(func.count(NationLog.id)).where(
                        NationLog.nation_id == nation.nation_id,
                        NationLog.action_type == "TRADE_LARGE",
                        NationLog.created_at >= since,
                    )
                )
                or 0
            )
            current_members = await _member_count(session, nation.nation_id)

            report = await _ai_nation_text(
                nation.founder_user_id or 0,
                nation,
                (
                    "گزارش هفتگی مدیریتی ملت تهیه کن. "
                    f"اعضا: {current_members}; "
                    f"تعداد رویدادهای 7 روز اخیر: {total_events}; "
                    f"معاملات بزرگ ثبت‌شده: {large_trades}; "
                    f"نرخ ارز فعلی: {nation.exchange_rate}; "
                    f"حجم معاملات 24ساعته: {nation.trade_volume_24h}. "
                    "سه نکته تحلیلی کوتاه، یک ریسک احتمالی و یک اقدام پیشنهادی غیرقطعی ارائه کن."
                ),
            )

        try:
            await bot.send_message(
                nation.founder_user_id,
                (
                    f"📊 <b>گزارش هفتگی اوپکس · {html.escape(nation.name)}</b>\n"
                    f"👥 اعضای فعلی: {current_members}\n"
                    f"💱 نرخ ارز: {_fmt_amount(nation.exchange_rate)}\n\n"
                    f"{html.escape(report)}"
                ),
                parse_mode="HTML",
            )
            sent_count += 1
        except Exception:
            logger.exception("Could not send weekly report to founder %s", nation.founder_user_id)

    return sent_count


# Compatibility alias for callers that use the path requested in the feature brief.
router = nation_management_router
