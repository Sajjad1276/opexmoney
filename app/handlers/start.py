from __future__ import annotations

import asyncio
import html
import json
import os
import time
import urllib.request
import logging
import re
from datetime import datetime, time as dt_time
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.handlers.nation_management import _join_user
from app.filters.profanity import profanity_filter
from app.utils.name_filter import TRADER_NAME_RE, is_blocked_trader_name, is_valid_trader_name
from app.keyboards.inline import (
    cancel_keyboard,
    first_trade_keyboard,
    more_menu_keyboard,
    suggested_name_keyboard,
    nation_selection_keyboard,
    trade_confirmation_keyboard,
    welcome_keyboard,
)
from app.keyboards.reply import main_menu_keyboard, new_player_menu_keyboard
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.mission_service import check_permanent_missions, increment_mission
from app.services.founder_service import cancel_draft
from app.services.economy_events import enqueue_event
from app.services.user_service import get_registration_status, get_user, is_fully_registered, sync_user_balance, username_exists
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from config import settings
from app.utils.formatting import fmt_amount, fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="start")
logger = logging.getLogger(__name__)

RLM = "\u200f"
ONBOARDING_TIMEOUT = 300
NATIONS_PER_PAGE = 5
INITIAL_BALANCE = Decimal("500.00")


def rtl_html(text: str) -> str:
    text = text.strip("\n")
    if not text.startswith(RLM):
        text = RLM + text
    return text.replace("\n", "\n" + RLM)


async def _start_timed_state(state: FSMContext, target_state) -> None:
    await state.set_state(target_state)
    await state.update_data(onboarding_started_at=time.time())


async def _state_is_alive(state: FSMContext, target_state) -> bool:
    if await state.get_state() != target_state.state:
        return False

    data = await state.get_data()
    started_at = data.get("onboarding_started_at")
    if not started_at:
        await state.update_data(onboarding_started_at=time.time())
        return True

    if time.time() - float(started_at) <= ONBOARDING_TIMEOUT:
        return True

    await state.clear()
    return False


async def _send_expired(message: Message) -> None:
    await message.answer(
        rtl_html(
            """
⏱ <b>فرآیند منقضی شد</b>

۵ دقیقه برای این مرحله فرصت داری.
برای شروع دوباره، /start رو بزن.
"""
        ),
        parse_mode=ParseMode.HTML,
    )


def _welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="شروع بازی 🎮",
                    callback_data="start_game",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        ]
    )


def _nation_profile_keyboard(nation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="همین ملت رو می‌خوام ✅",
                    callback_data="confirm_nation:{0}".format(nation_id),
                    style=ButtonStyle.SUCCESS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="برگشت 🔙",
                    callback_data="back_to_nations",
                )
            ],
        ]
    )


def _nation_page_keyboard(
    nations: list[Nation],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for nation in nations:
        rows.append(
            [
                InlineKeyboardButton(
                    text="{0} {1} · {2} · {3} نفر".format(
                        html.escape(nation.flag_emoji or "🏴"),
                        html.escape(nation.name),
                        html.escape(nation.currency_code),
                        to_fa(nation.member_count),
                    ),
                    callback_data="confirm_nation:{0}".format(nation.nation_id),
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ قبلی",
                callback_data="nation_page:{0}".format(page - 1),
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="بعدی ➡️",
                callback_data="nation_page:{0}".format(page + 1),
            )
        )
    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            text="🏛 تأسیس ملت",
            callback_data="start_founder",
            style=ButtonStyle.SUCCESS,
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_mention(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "معامله‌گر")}</a>'


def current_date_fa() -> str:
    now = datetime.now()
    return to_fa(now.strftime("%Y/%m/%d"))



async def _safe_edit_text(call: CallbackQuery, text: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Message edit failed: %s", exc)
        return False


async def _safe_edit_caption(call: CallbackQuery, caption: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_caption"):
            return False
        await call.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Caption edit failed: %s", exc)
        return False


START_CAPTION = """🌐 <b>{bot_name}</b>
سلام {user_name}.

بازارهای OPEX هر روز
میلیاردها واحد ارز جابه‌جا می‌کنن.
تو کجا می‌ایستی؟"""

USERNAME_CAPTION = """💹 <b>اسم معامله‌گرت رو انتخاب کن</b>
{user_mention}، این اسم روی تابلوی معاملات OPEX نمایش داده میشه.

بنویس:
· 3 تا 20 کاراکتر
· فارسی، انگلیسی، عدد و خط تیره مجاز؛ فاصله و علامت دیگر مجاز نیست"""

def nation_list_text(user, trader_name: str, nations: list[Nation]) -> str:
    lines = [
        f"✅ <b>«{html.escape(trader_name)}»</b> ثبت شد.",
        "<blockquote>⁠</blockquote>",
        f"{user_mention(user)}، حالا باید به یه ملت بپیوندی.",
        "",
        "ارز اون ملت، پول اصلی حسابت میشه.",
        "هر معامله‌ات مستقیم روی نرخ اون ارز اثر میذاره.",
        "",
        "<b>🌍 ملت‌های فعال:</b>",
        "<blockquote>⁠</blockquote>",
    ]
    for index, nation in enumerate(nations, start=1):
        change = get_rate_change(nation)
        lines.extend(
            [
                f"{html.escape(nation.flag_emoji or '🏴')} <b>{html.escape(nation.name)} · {html.escape(nation.currency_code)}</b>",
                f"{get_rate_emoji(change)} <b>{fmt_rate(nation.exchange_rate)} دلار</b> · <i>{fmt_pct(change)} امروز</i>",
                f"👥 {to_fa(nation.active_members_24h)} عضو · 🏆 رتبه #{to_fa(nation.nation_rank or 0)}",
            ]
        )
        if index != len(nations):
            lines.append("<blockquote>⁠</blockquote>")
    lines.extend(
        [
            "<blockquote>⁠</blockquote>",
            "نرخ‌ها هر 15 دقیقه آپدیت میشن.",
        ]
    )
    return "\n".join(lines)


async def show_dashboard(
    message: Message,
    user: User,
    *,
    replace_inline: bool = False,
    bot: Bot | None = None,
    display_user=None,
) -> None:
    """Return to the existing main menu without sending the old dashboard card.

    The detailed player dashboard is intentionally not rendered here.
    Balances, assets, rates, rank, and similar data belong to their
    dedicated menu sections. Telegram's ReplyKeyboard is already persistent,
    so returning from an inline panel does not require another text message.
    """
    if replace_inline:
        try:
            await message.delete()
        except Exception:
            logger.debug("Could not delete previous inline panel", exc_info=True)
    return


async def continue_registration(
    message: Message,
    state: FSMContext,
    user: User,
    missing: list[str],
) -> None:
    if "username" in missing or not (user.username or "").strip():
        await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
        await message.answer(
            USERNAME_CAPTION.format(
                user_mention=user_mention(message.from_user),
            ),
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(
        user_id=user.user_id,
        username=user.username,
    )

    if "holding" not in missing:
        await state.clear()
        await show_dashboard(message, user)
        return

    # A persisted home nation means the user is not really at the nation
    # selection step. Repair the missing holding from the existing balance.
    if user.home_nation_id is not None:
        async with async_session() as session:
            async with session.begin():
                nation = await session.get(Nation, user.home_nation_id)
                holding = await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == user.user_id,
                        CurrencyHolding.nation_id == user.home_nation_id,
                    )
                )
                if nation is not None and holding is None:
                    session.add(
                        CurrencyHolding(
                            user_id=user.user_id,
                            nation_id=user.home_nation_id,
                            amount=user.balance,
                        )
                    )
        await state.clear()
        await show_dashboard(message, user)
        return

    async with async_session() as session:
        async with session.begin():
            nations = await get_active_nations(session, limit=3)

    await state.set_state(OnboardingStates.SELECT_NATION)

    if not nations:
        await message.answer(
            "🌍 <b>هنوز هیچ ملتی تأسیس نشده!</b>\n\n"
            "تو می‌تونی اولین بنیان‌گذار تاریخ باشی\n"
            "و اولین ملت OPEX MONEY رو بسازی.",
            reply_markup=nation_selection_keyboard([]),
            parse_mode="HTML",
        )
        return

    await message.answer(
        nation_list_text(message.from_user, user.username, nations),
        reply_markup=nation_selection_keyboard(nations),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) == 2 else ""
    preferred_nation_id = None
    if payload.startswith("nation_"):
        try:
            preferred_nation_id = int(payload.split("_", 1)[1])
        except (TypeError, ValueError):
            preferred_nation_id = None

    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    await state.clear()
    if preferred_nation_id is not None:
        await state.update_data(preferred_nation_id=preferred_nation_id)

    now = datetime.now()
    date_text = to_fa(now.strftime("%Y/%m/%d"))
    time_text = to_fa(now.strftime("%H:%M"))

    text = f"""🌐 <b>به OPEX MONEY خوش اومدی</b>

اقتصاد زنده است؛ تصمیم‌های تو مهم‌اند.

🎯 <b>تصمیم بگیر، معامله کن، رشد کن.</b>

📅 {date_text}
🕐 {time_text}"""

    # /start is a clean entry point for every player.
    # Detailed balances and assets remain inside their dedicated sections.
    await message.answer(
        rtl_html(text),
        reply_markup=main_menu_keyboard() if user is not None else _welcome_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def _begin_registration(
    message: Message,
    state: FSMContext,
    *,
    replace_inline: bool = False,
) -> None:
    data_before_clear = await state.get_data()
    preferred_nation_id = data_before_clear.get("preferred_nation_id")

    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    if user is not None:
        await state.clear()
        if replace_inline:
            await show_dashboard(
                message,
                user,
                replace_inline=True,
                bot=message.bot,
                display_user=message.from_user,
            )
        else:
            await show_dashboard(message, user)
        return

    await state.clear()
    if preferred_nation_id is not None:
        await state.update_data(preferred_nation_id=preferred_nation_id)
    await _start_timed_state(state, OnboardingStates.ONBOARDING_NAME)

    suggested = (message.from_user.first_name or "").strip()[:20]
    if not suggested or not is_valid_trader_name(suggested) or is_blocked_trader_name(suggested):
        suggested = ""

    if suggested:
        await state.update_data(suggested_username=suggested)
        text = (
            "👤 <b>هویت معامله‌گرت</b>\n\n"
            f"برای شروع، می‌تونیم از اسم «{html.escape(suggested)}» استفاده کنیم.\n"
            "اسم بعداً از تنظیمات هم قابل تغییره.\n\n"
            "یا خودت یک نام ۳ تا ۲۰ کاراکتری انتخاب کن."
        )
        markup = suggested_name_keyboard(suggested)
    else:
        text = """
👤 <b>هویت معامله‌گرت رو بساز</b>

یک نام ۳ تا ۲۰ کاراکتری انتخاب کن.
این اسم روی تابلوی معاملات دیده می‌شه.

فارسی، انگلیسی، عدد و خط تیره مجازه.
"""
        markup = cancel_keyboard()

    if replace_inline:
        await message.edit_text(
            rtl_html(text),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        panel = message
    else:
        panel = await message.answer(
            rtl_html(text),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    await remember_inline_panel(state, panel)


@router.callback_query(
    F.data == "cancel_start",
    StateFilter(
        OnboardingStates.ONBOARDING_NAME,
        OnboardingStates.SET_USERNAME_PLAYER,
        OnboardingStates.SELECT_NATION,
        FounderStates.SET_USERNAME_FOUNDER,
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_NATION_NAME,
        FounderStates.SET_CURRENCY_CODE,
        FounderStates.SELECT_FLAG,
        FounderStates.CONFIRM,
    ),
)
async def cancel_onboarding_panel(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    current_state = await state.get_state()
    founder_cancel = current_state in {
        FounderStates.SET_USERNAME_FOUNDER.state,
        FounderStates.WAITING_GROUP_ADMIN.state,
        FounderStates.SET_NATION_NAME.state,
        FounderStates.SET_CURRENCY_CODE.state,
        FounderStates.SELECT_FLAG.state,
        FounderStates.CONFIRM.state,
    }

    if founder_cancel:
        async with async_session() as session:
            try:
                await cancel_draft(session, call.from_user.id)
            except Exception:
                logger.exception(
                    "Could not cancel founding draft | founder=%s",
                    call.from_user.id,
                )
        try:
            if call.message is not None:
                await call.message.delete()
        except Exception:
            logger.debug("Could not delete cancelled founder panel", exc_info=True)
    else:
        await close_inline_panel(state, call.bot)

    await state.clear()

    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id) if await is_fully_registered(
                session,
                call.from_user.id,
            ) else None

    if user is not None and call.message is not None:
        await show_dashboard(
            call.message,
            user,
            replace_inline=False,
        )
    elif call.message is not None:
        await call.message.answer(
            "👋 <b>شروع OPEX MONEY</b>\n\n"
            "برای ورود به بازی، روی «شروع بازی» بزن.",
            reply_markup=welcome_keyboard(),
            parse_mode=ParseMode.HTML,
        )

    await call.answer("❌ لغو شد.")


@router.message(F.text == "🎮 شروع بازی")
async def start_game_button(message: Message, state: FSMContext) -> None:
    await _begin_registration(message, state)


@router.callback_query(F.data == "start_game")
async def start_game_callback(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        await call.answer(rtl_html("⚠️ پیام شروع بازی پیدا نشد."), show_alert=True)
        return

    await call.answer()
    await _begin_registration(call.message, state, replace_inline=True)


async def _show_help(
    message: Message,
    *,
    replace_inline: bool = False,
) -> None:
    text = (
        "❓ <b>راهنمای OPEX MONEY</b>\n"
        "تو یه معامله‌گر اقتصادی هستی.\n"
        "به ملت‌ها بپیوند، ارز بخر و بفروش.\n"
        "نرخ ارز با فعالیت بازار تغییر می‌کنه.\n"
        "برای شروع، اسم معامله‌گرت رو انتخاب کن."
    )

    if replace_inline:
        await message.edit_text(
            text,
            reply_markup=welcome_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        text,
        reply_markup=welcome_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "❓ راهنما")
async def start_help(message: Message) -> None:
    await _show_help(message)


@router.callback_query(F.data == "show_help", StateFilter(None))
async def start_help_callback(call: CallbackQuery) -> None:
    await call.answer()
    if call.message:
        await _show_help(call.message, replace_inline=True)



async def render_nation_profile(nation: Nation) -> str:
    """
    خروجی HTML فرمت، راست‌چین:

    🏴 <b>نام ملت</b>
    <blockquote>⁠</blockquote>
    💰 <b>واحد پول:</b> {symbol}
    📈 <b>نرخ ارز:</b> {rate} دلار  <u>(آپدیت 15 دقیقه پیش)</u>
    👥 <b>اعضا:</b> {member_count} نفر
    🏆 <b>رتبه جهانی:</b> #{rank}
    ⚔️ <b>وضعیت:</b> {status_emoji} {status_text}

    📊 <b>وضعیت اقتصادی:</b>
    ▸ فعالیت: {activity_score}/100
    ▸ معاملات امروز: {today_trades}
    ▸ روند: {trend_emoji} {trend_text}
    """
    async with async_session() as session:
        async with session.begin():
            rank = nation.nation_rank or await get_nation_rank(session, nation.nation_id)
            today_start = datetime.combine(datetime.utcnow().date(), dt_time.min)
            today_trades = await session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.nation_id == nation.nation_id,
                    Transaction.created_at >= today_start,
                )
            ) or 0

    update_minutes = 0
    if nation.last_rate_update:
        update_minutes = max(
            0,
            int((datetime.utcnow() - nation.last_rate_update).total_seconds() // 60),
        )

    member_count = max(int(nation.member_count or 0), 0)
    active_members = max(int(nation.active_members_24h or 0), 0)
    activity_score = (
        0
        if member_count == 0
        else min(100, round((active_members / member_count) * 100))
    )

    current_rate = Decimal(str(nation.exchange_rate or Decimal("0")))
    previous_rate = Decimal(str(nation.rate_prev or current_rate))
    delta = (
        Decimal("0")
        if previous_rate == 0
        else (current_rate - previous_rate) / previous_rate * Decimal("100")
    )

    if delta > Decimal("0.10"):
        trend_emoji, trend_text = "📈", "صعودی"
    elif delta < Decimal("-0.10"):
        trend_emoji, trend_text = "📉", "نزولی"
    else:
        trend_emoji, trend_text = "➡️", "باثبات"

    change_24h = get_rate_change(nation)
    if change_24h > 1:
        status_emoji, status_text = "🟢", "قوی"
    elif change_24h < -1:
        status_emoji, status_text = "🔴", "تحت فشار"
    else:
        status_emoji, status_text = "🟡", "پایدار"

    text = """
{0}
<blockquote>⁠</blockquote>
💰 <b>واحد پول:</b> {1}
📈 <b>نرخ ارز:</b> {2} دلار  <u>(آپدیت {3} دقیقه پیش)</u>
👥 <b>اعضا:</b> {4} نفر
🏆 <b>رتبه جهانی:</b> #{5}
⚔️ <b>وضعیت:</b> {6} {7}

📊 <b>وضعیت اقتصادی:</b>
▸ فعالیت: {8}/100
▸ معاملات امروز: {9}
▸ روند: {10} {11}
""".format(
        f"{html.escape(nation.flag_emoji or '🏴')} <b>{html.escape(nation.name)}</b>",
        html.escape(nation.currency_code),
        fmt_rate(nation.exchange_rate),
        to_fa(update_minutes),
        to_fa(member_count),
        to_fa(rank),
        status_emoji,
        status_text,
        to_fa(activity_score),
        to_fa(today_trades),
        trend_emoji,
        trend_text,
    )
    return rtl_html(text)


async def _load_nation_page(page: int) -> tuple[list[Nation], bool]:
    page = max(0, page)
    offset = page * NATIONS_PER_PAGE

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(Nation)
                .where(Nation.is_active.is_(True))
                .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
                .offset(offset)
                .limit(NATIONS_PER_PAGE + 1)
            )
            rows = list(result.scalars().all())

    return rows[:NATIONS_PER_PAGE], len(rows) > NATIONS_PER_PAGE


async def _render_nation_page(
    message: Message,
    state: FSMContext,
    page: int = 0,
    *,
    edit: bool = False,
) -> None:
    nations, has_next = await _load_nation_page(page)
    await _start_timed_state(state, OnboardingStates.SELECT_NATION)
    await state.update_data(nation_page=max(0, page))

    if not nations:
        text = """
🌍 <b>هنوز ملتی برای ورود وجود نداره</b>

می‌تونی بعداً برگردی و یک ملت فعال انتخاب کنی.
"""
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏛 تأسیس ملت",
                        callback_data="start_founder",
                        style=ButtonStyle.SUCCESS,
                    )
                ]
            ]
        )
    else:
        text = """
🌍 <b>ملتت رو انتخاب کن</b>

یک گزینه رو بزن و مستقیم وارد بازی شو.
💰 سرمایه شروع: ۵۰۰ واحد از ارز همان ملت
⚡ بعد از ورود، اولین معامله‌ات آماده‌ست.
"""
        keyboard = _nation_page_keyboard(nations, max(0, page), has_next)

    text = rtl_html(text)

    if edit:
        try:
            await message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return
        except TelegramBadRequest:
            pass

    await message.answer(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def _generate_personalized_welcome(
    username: str,
    nation: Nation,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return (
            "به <b>{0}</b> خوش اومدی. از حالا <b>{1}</b> شهروند این ملتی."
            " سرمایه اولیه‌ات آماده‌ست."
        ).format(
            html.escape(nation.name),
            html.escape(username),
        )

    model = settings.ai_model
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent"
    )
    prompt = """
تو نویسنده پیام خوشامد یک بازی اقتصادی تلگرامی به نام OPEX MONEY هستی.
برای بازیکن جدید یک پیام کوتاه، فارسی، هیجان‌انگیز و حرفه‌ای بنویس.

نام بازیکن: {0}
نام ملت: {1}
واحد پول: {2}
نرخ ارز: {3} دلار
تعداد اعضا: {4}

قواعد:
- حداکثر 4 جمله.
- فقط متن ساده برگردان.
- HTML و Markdown تولید نکن.
- فقط از اطلاعات داده‌شده استفاده کن.
- حس ورود رسمی به یک اقتصاد زنده را منتقل کن.
""".format(
        username,
        nation.name,
        nation.currency_code,
        fmt_rate(nation.exchange_rate),
        nation.member_count,
    ).strip()

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 180,
        },
    }

    def _request() -> str:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))

        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        result = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict)
        ).strip()

        if not result:
            raise ValueError("Gemini returned empty text")

        return result

    try:
        return html.escape(await asyncio.to_thread(_request))
    except Exception:
        logger.exception("Gemini personalized welcome failed")
        return (
            "به <b>{0}</b> خوش اومدی. <b>{1}</b>، "
            "از حالا بخشی از اقتصاد این ملتی."
        ).format(
            html.escape(nation.name),
            html.escape(username),
        )


@router.callback_query(
    F.data == "use_suggested_name",
    StateFilter(OnboardingStates.ONBOARDING_NAME),
)
async def use_suggested_name(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    name = (data.get("suggested_username") or "").strip()
    if not name or not is_valid_trader_name(name) or is_blocked_trader_name(name):
        await call.answer("⚠️ این نام قابل استفاده نیست.", show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, name):
                await call.answer("⚠️ این نام قبلاً گرفته شده.", show_alert=True)
                return

    data = await state.get_data()
    await state.update_data(username=name, onboarding_started_at=time.time())
    await call.answer("✅ نام انتخاب شد")
    preferred_id = data.get("preferred_nation_id")
    if call.message and preferred_id is not None:
        async with async_session() as session:
            async with session.begin():
                preferred_nation = await session.get(Nation, int(preferred_id))
        if preferred_nation is not None and preferred_nation.is_active:
            await state.set_state(OnboardingStates.SELECT_NATION)
            await state.update_data(selected_nation_id=preferred_nation.nation_id)
            await _safe_edit_text(
                call,
                await render_nation_profile(preferred_nation),
                _nation_profile_keyboard(preferred_nation.nation_id),
            )
            return
    if call.message:
        await _render_nation_page(call.message, state, page=0, edit=True)


@router.callback_query(
    F.data == "choose_custom_name",
    StateFilter(OnboardingStates.ONBOARDING_NAME),
)
async def choose_custom_name(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.message:
        await call.message.edit_text(
            rtl_html(
                "👤 <b>نام معامله‌گرت رو بنویس</b>\n\n"
                "۳ تا ۲۰ کاراکتر. فارسی، انگلیسی، عدد و خط تیره."
            ),
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )


@router.message(F.text == "☰ بیشتر")
async def open_more_menu(message: Message) -> None:
    await message.answer(
        rtl_html("☰ <b>بخش‌های بیشتر</b>\n\nقابلیت‌های مدیریتی و جزئی‌تر اینجا قرار دارن."),
        reply_markup=more_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "missions_open")
async def open_missions_from_more(call: CallbackQuery) -> None:
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            logger.debug("Could not delete more-menu panel", exc_info=True)
        from app.handlers.missions import show_missions
        await show_missions(call.message)
    await call.answer()


@router.callback_query(F.data == "treasury_open")
async def open_treasury_from_more(call: CallbackQuery, state: FSMContext) -> None:
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            logger.debug("Could not delete more-menu panel", exc_info=True)
        from app.handlers.treasury import open_treasury_from_main_menu
        await open_treasury_from_main_menu(call.message, state)
    await call.answer()


@router.message(F.text == "🎯 قدم بعدی")
async def new_player_next_step(message: Message, state: FSMContext) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id)
            if user is None or user.home_nation_id is None:
                await message.answer("⚠️ هنوز ملتت مشخص نشده. /start بزن.")
                return
            nation = await session.get(Nation, user.home_nation_id)
            trade_count = int(
                await session.scalar(
                    select(func.count(Transaction.id)).where(
                        Transaction.user_id == user.user_id,
                    )
                ) or 0
            )

    if nation is None:
        await message.answer("⚠️ ملت فعال پیدا نشد. /start بزن.")
        return

    if trade_count > 0:
        from app.handlers.market import render_market
        await render_market(message)
        return

    await state.update_data(
        first_trade_available=True,
        onboarding_started_at=time.time(),
    )
    receive_omx = Decimal("50") * nation.exchange_rate
    text = (
        f"{html.escape(nation.flag_emoji or '🏴')} <b>قدم بعدی تو</b>\n"
        "<blockquote>⁠</blockquote>"
        "🎯 این اولین تصمیم اقتصادی توست.\n\n"
        f"📤 می‌فروشی: <b>۵۰ {html.escape(nation.currency_code)}</b>\n"
        f"📥 می‌گیری: <b>{fmt_amount(receive_omx)} دلار</b>\n\n"
        "🧠 چرا؟ ارز ملتت را به دلار تبدیل می‌کنی تا بعداً بتوانی ارزهای دیگر را معامله کنی.\n"
        "این فقط آموزش نیست؛ یک معامله واقعی در اقتصاد بازی است."
    )
    panel = await send_submenu_panel(
        message,
        text,
        reply_markup=first_trade_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, panel)


async def confirm_nation(call: CallbackQuery, state: FSMContext, bot) -> None:
    try:
        nation_id = int((call.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer("⚠️ ملت معتبر نیست.", show_alert=True)
        return

    data = await state.get_data()
    username = (data.get("username") or "").strip()
    if not username:
        await call.answer("⏱ ثبت‌نام منقضی شد. /start بزن.", show_alert=True)
        await state.clear()
        return

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id, with_for_update=True)
            if user is None:
                user = User(
                    user_id=call.from_user.id,
                    username=username,
                    home_nation_id=None,
                    balance=Decimal("0"),
                    xr_balance=Decimal("0"),
                    role="player",
                )
                session.add(user)
                await session.flush()
            else:
                user.username = username

    try:
        result_message, nation = await _join_user(
            bot=bot,
            user_id=call.from_user.id,
            nation_id=nation_id,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.clear()
    if nation is None:
        await call.answer("⚠️ اطلاعات ملت در دسترس نیست.", show_alert=True)
        return

    if "درخواست عضویت ارسال شد" in result_message or "قبلاً ثبت شده" in result_message:
        await state.update_data(first_trade_available=False)
        await _safe_edit_text(call, result_message)
        await call.answer("📝 درخواست عضویت ثبت شد.")
        return

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            nation = await session.get(Nation, nation_id)
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == call.from_user.id,
                    CurrencyHolding.nation_id == nation_id,
                )
            )

    if user is None or nation is None or holding is None:
        await call.answer("⚠️ حساب ملت ناقص است. /start بزن.", show_alert=True)
        return

    await state.update_data(first_trade_available=True)
    text = (
        f"{html.escape(nation.flag_emoji or '🏴')} <b>به {html.escape(nation.name)} خوش اومدی</b>\n"
        "<blockquote>⁠</blockquote>"
        f"💰 سرمایه شروع: <b>۵۰۰ <code>{html.escape(nation.currency_code)}</code></b>\n"
        "💎 موجودی دلار: <b>۰</b>\n\n"
        "⚡ <b>اولین حرکتت:</b> ۵۰ واحد از ارزت رو بفروش و نتیجه رو ببین."
    )
    await _safe_edit_text(call, text, first_trade_keyboard())
    await call.answer()


async def first_trade_tutorial(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"):
        await call.answer()
        return

    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            nation = (
                await session.get(Nation, user.home_nation_id)
                if user and user.home_nation_id
                else None
            )
    if user is None or nation is None:
        await call.answer("⚠️ اطلاعات معامله پیدا نشد.", show_alert=True)
        return

    receive_xr = Decimal("50") * Decimal(str(nation.exchange_rate))
    text = (
        "⚡ <b>اولین معامله</b>\n"
        "<blockquote>⁠</blockquote>"
        f"📤 می‌فروشی: <b>۵۰ <code>{html.escape(nation.currency_code)}</code></b>\n"
        f"📥 دریافت می‌کنی: <b>{fmt_amount(receive_xr)} دلار</b>\n\n"
        f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} دلار</code>"
    )
    await _safe_edit_text(call, text, trade_confirmation_keyboard())
    await call.answer()


@router.callback_query(F.data == "confirm_first_trade")
async def confirm_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"):
        await call.answer()
        return

    async with async_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User)
                    .where(User.user_id == call.from_user.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.home_nation_id is None:
                await call.answer("⚠️ حساب ملت پیدا نشد. /start بزن.", show_alert=True)
                return

            nation = await session.get(
                Nation,
                user.home_nation_id,
                with_for_update=True,
            )
            holding = await session.scalar(
                select(CurrencyHolding)
                .where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == user.home_nation_id,
                )
                .with_for_update()
            )

            if nation is None or not nation.is_active or holding is None:
                await call.answer("⚠️ موجودی معامله پیدا نشد.", show_alert=True)
                return

            amount = Decimal("50")
            if holding.amount < amount:
                await call.answer("🔴 موجودی کافی نیست.", show_alert=True)
                return

            rate = Decimal(str(nation.exchange_rate))
            receive_xr = amount * rate
            holding.amount -= amount
            user.xr_balance += receive_xr
            user.balance = holding.amount
            nation.trade_volume_24h += receive_xr

            transaction = Transaction(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                transaction_type="sell",
                spend_xr=receive_xr,
                amount=amount,
                fee_xr=Decimal("0"),
                rate=rate,
            )
            session.add(transaction)
            await session.flush()
            await enqueue_event(
                session,
                event_key=f"transaction:{transaction.id}",
                event_type="trade.sell",
                aggregate_type="transaction",
                aggregate_id=transaction.id,
                payload={
                    "user_id": user.user_id,
                    "nation_id": nation.nation_id,
                    "side": "sell",
                    "spend_xr": str(receive_xr),
                    "amount": str(amount),
                    "fee_xr": "0",
                    "rate": str(rate),
                    "source": "first_trade_tutorial",
                },
            )
            session.add(
                UserActivity(
                    user_id=user.user_id,
                    nation_id=nation.nation_id,
                    activity_type="trade",
                )
            )
            await sync_user_balance(session, user.user_id)

            local_balance = holding.amount
            xr_balance = user.xr_balance
            currency_code = nation.currency_code

    await state.clear()
    await _safe_edit_text(
        call,
        (
            "✅ <b>اولین معامله انجام شد!</b>\n"
            "<blockquote>⁠</blockquote>"
            f"📤 فروختی: <b>۵۰ {html.escape(currency_code)}</b>\n"
            f"📥 دریافتی: <b>{fmt_amount(receive_xr)} دلار</b>\n\n"
            f"💰 موجودی: <b>{fmt_amount(local_balance)} {html.escape(currency_code)}</b> "
            f"+ <b>{fmt_amount(xr_balance)} دلار</b>\n\n"
            "🎯 قدم بعدی: بازار رو باز کن و یک ارز دیگه رو بررسی کن."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💹 رفتن به بازار",
                        callback_data="market_main",
                        style=ButtonStyle.SUCCESS,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 دیدن داشبورد",
                        callback_data="back_to_dashboard",
                    )
                ],
            ]
        ),
    )
    await call.answer("✅ اولین معامله ثبت شد")


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()

    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            nation = (
                await session.get(Nation, user.home_nation_id)
                if user and user.home_nation_id
                else None
            )

    code = nation.currency_code if nation else "ارز"
    balance = (
        fmt_amount(
            await _get_holding_amount(
                call.from_user.id,
                nation.nation_id,
            )
        )
        if nation
        else "500"
    )
    await _safe_edit_text(
        call,
        (
            f"{html.escape(call.from_user.first_name or 'معامله‌گر')}، فعلاً از معامله رد شدی.\n\n"
            f"💰 موجودی: {balance} <code>{html.escape(code)}</code>\n\n"
            "هر وقت آماده شدی، از بازار شروع کن."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💹 رفتن به بازار",
                        callback_data="market_main",
                        style=ButtonStyle.SUCCESS,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 دیدن داشبورد",
                        callback_data="back_to_dashboard",
                    )
                ],
            ]
        ),
    )
    await call.answer()


async def _get_holding_amount(user_id: int, nation_id: int) -> Decimal:
    async with async_session() as session:
        async with session.begin():
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user_id,
                    CurrencyHolding.nation_id == nation_id,
                )
            )
            return holding.amount if holding else Decimal("500")


@router.message(F.text == "💰 پول من")
async def new_player_money(message: Message) -> None:
    from app.handlers.portfolio import show_portfolio
    await show_portfolio(message)


@router.message(OnboardingStates.ONBOARDING_NAME, F.text)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.ONBOARDING_NAME):
        await _send_expired(message)
        return

    name = (message.text or "").strip()

    # 1. length_filter
    if not 3 <= len(name) <= 20:
        await message.answer(
            rtl_html(
                """
🔴 <b>طول نام قابل قبول نیست</b>

نام معامله‌گر باید بین 3 تا 20 کاراکتر باشه.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. profanity_filter
    if profanity_filter(name):
        await message.answer(
            rtl_html(
                """
🔴 <b>این نام قابل قبول نیست</b>

این نام شامل عبارت نامناسبه.
یک نام مناسب برای معامله‌گر انتخاب کن.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # 3. duplicate_filter
    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, name):
                await message.answer(
                    rtl_html(
                        """
🔴 <b>این نام قبلاً ثبت شده</b>

یک نام دیگر برای معامله‌گرت انتخاب کن.
"""
                    ),
                    parse_mode=ParseMode.HTML,
                )
                return

    # 4. pattern_filter
    if not TRADER_NAME_RE.fullmatch(name):
        await message.answer(
            rtl_html(
                """
🔴 <b>فرمت نام قابل قبول نیست</b>

فقط حروف فارسی، حروف انگلیسی، عدد و خط تیره مجازه.
فاصله و سایر علامت‌ها مجاز نیستند.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # FSMStorage uses Redis when REDIS_URL is configured in main.py.