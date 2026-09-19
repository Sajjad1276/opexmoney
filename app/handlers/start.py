from __future__ import annotations

import asyncio
import html
import json
import os
import time
import urllib.request
import logging
from datetime import datetime, time as dt_time
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.handlers.nation_management import _join_user
from app.filters.profanity import profanity_filter
from app.utils.name_filter import TRADER_NAME_RE
from app.keyboards.inline import cancel_keyboard, first_trade_keyboard, nation_selection_keyboard, trade_confirmation_keyboard, welcome_keyboard
from app.keyboards.reply import main_menu_keyboard
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.mission_service import check_permanent_missions, increment_mission
from app.services.user_service import get_registration_status, get_user, is_fully_registered, sync_user_balance, username_exists
from app.states.onboarding import OnboardingStates
from config import settings
from app.utils.formatting import fmt_amount, fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa

router = Router(name="start")
logger = logging.getLogger(__name__)

RLM = "\u200f"
ONBOARDING_TIMEOUT = 30
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

30 ثانیه برای این مرحله فرصت داشتی.
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
                    text="{0} {1} | 💰 نرخ: {2} ΩXR | 👥 {3} نفر".format(
                        html.escape(nation.flag_emoji or "🏴"),
                        html.escape(nation.name),
                        fmt_rate(nation.exchange_rate),
                        to_fa(nation.member_count),
                    ),
                    callback_data="select_nation:{0}".format(nation.nation_id),
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
    lines = [f"✅ <b>«{html.escape(trader_name)}»</b> ثبت شد.", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", f"{user_mention(user)}، حالا باید به یه ملت بپیوندی.", "", "ارز اون ملت، پول اصلی حسابت میشه.", "هر معامله‌ات مستقیم روی نرخ اون ارز اثر میذاره.", "", "<b>🌍 ملت‌های فعال:</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for index, nation in enumerate(nations, start=1):
        mention_user = display_user or message.from_user
    change = get_rate_change(nation)
        lines.extend([f"{html.escape(nation.flag_emoji or '🏴')} <b>{html.escape(nation.name)} · {html.escape(nation.currency_code)}</b>", f"{get_rate_emoji(change)} <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> · <i>{fmt_pct(change)} امروز</i>", f"👥 {to_fa(nation.active_members_24h)} عضو · 🏆 رتبه #{to_fa(nation.nation_rank or 0)}"])
        if index != len(nations):
            lines.append("─────────────────")
    lines.extend(["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "نرخ‌ها هر 15 دقیقه آپدیت میشن."])
    return "\n".join(lines)


async def show_dashboard(
    message: Message,
    user: User,
    *,
    replace_inline: bool = False,
    bot: Bot | None = None,
    display_user=None,
) -> None:
    async with async_session() as session:
        async with session.begin():
            try:
                await increment_mission(session, user.user_id, "DAILY_LOGIN")
                await check_permanent_missions(session, user.user_id)
            except Exception:
                logger.exception(
                    "Mission trigger failed while loading dashboard for user %s",
                    user.user_id,
                )

            nation = await session.get(Nation, user.home_nation_id) if user.home_nation_id else None
            if nation is None:
                dashboard_text = rtl_html(
                    """
👋 {0}

حساب تو آماده‌ست، اما هنوز ملت اصلی نداری.
""".format(user_mention(display_user or message.from_user))
                )
                if replace_inline and bot is not None:
                    try:
                        await message.delete()
                    except Exception:
                        logger.debug("Could not delete previous inline panel", exc_info=True)
                    await bot.send_message(
                        user.user_id,
                        dashboard_text,
                        reply_markup=main_menu_keyboard(),
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await message.answer(
                        dashboard_text,
                        reply_markup=main_menu_keyboard(),
                        parse_mode=ParseMode.HTML,
                    )
                return

            rank = nation.nation_rank or await get_nation_rank(session, nation.nation_id)
            total_nations = await session.scalar(
                select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))
            ) or 0
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == nation.nation_id,
                )
            )

    change = get_rate_change(nation)
    minutes = (
        max(
            0,
            int((datetime.utcnow() - nation.last_rate_update).total_seconds() // 60),
        )
        if nation.last_rate_update
        else 0
    )
    balance = holding.amount if holding else user.balance

    text = """
🌐 <b>OPEX MONEY</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{0}

{1}
💰 <code>{2}</code>: <b>{3}</b>
💎 <code>ΩXR</code>: <b>{4}</b>

{5} نرخ ارز: <b>{6} ΩXR</b>
🏆 رتبه #{7} از {8}
👥 {9} عضو
⏱ <i>{10} دقیقه پیش</i>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".format(
        user_mention(mention_user),
        f"{html.escape(nation.flag_emoji or '🏴')} <b>{html.escape(nation.name)}</b>",
        html.escape(nation.currency_code),
        fmt_amount(balance),
        fmt_amount(user.xr_balance),
        get_rate_emoji(change),
        fmt_rate(nation.exchange_rate),
        to_fa(rank),
        to_fa(total_nations),
        to_fa(nation.member_count),
        to_fa(minutes),
    )

    if replace_inline and bot is not None:
        try:
            await message.delete()
        except Exception:
            logger.debug("Could not delete previous inline panel", exc_info=True)
        await bot.send_message(
            user.user_id,
            rtl_html(text),
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            rtl_html(text),
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )


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
    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    await state.clear()

    if user is not None:
        await show_dashboard(message, user)
        return

    await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())

    text = """
👋 <b>به OPEX MONEY خوش اومدی</b>

اینجا وارد یک اقتصاد زنده می‌شی.
ملتت رو انتخاب کن، ارز خودت رو داشته باش
و با تصمیم‌های خودت مسیر ثروتت رو بساز.

آماده‌ای؟
"""

    await message.answer(
        rtl_html(text),
        reply_markup=_welcome_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def _begin_registration(message: Message, state: FSMContext) -> None:
    await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    if user is not None:
        await state.clear()
        await show_dashboard(message, user)
        return

    await state.clear()
    await _start_timed_state(state, OnboardingStates.ONBOARDING_NAME)

    text = """
👤 <b>نام معامله‌گرت رو انتخاب کن</b>

این نام به عنوان نام نمایشی تو در OPEX MONEY
به بقیه بازیکن‌ها نمایش داده می‌شه.

3 تا 20 کاراکتر وارد کن.
فقط حروف فارسی یا انگلیسی، عدد و خط تیره مجازه.
"""

    await message.answer(
        rtl_html(text),
        reply_markup=cancel_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "🎮 شروع بازی")
async def start_game_button(message: Message, state: FSMContext) -> None:
    await _begin_registration(message, state)


@router.callback_query(F.data == "start_game")
async def start_game_callback(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        await call.answer(rtl_html("⚠️ پیام شروع بازی پیدا نشد."), show_alert=True)
        return

    await call.answer()
    await _begin_registration(call.message, state)


async def _show_help(message: Message) -> None:
    await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "❓ <b>راهنمای OPEX MONEY</b>\n"
        "تو یه معامله‌گر اقتصادی هستی.\n"
        "به ملت‌ها بپیوند، ارز بخر و بفروش.\n"
        "نرخ ارز با فعالیت بازار تغییر می‌کنه.\n"
        "برای شروع، اسم معامله‌گرت رو انتخاب کن.",
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
        await _show_help(call.message)



async def render_nation_profile(nation: Nation) -> str:
    """
    خروجی HTML فرمت، راست‌چین:

    🏴 <b>نام ملت</b>
    ─────────────────
    💰 <b>واحد پول:</b> {symbol}
    📈 <b>نرخ ارز:</b> {rate} ΩXR  <u>(آپدیت 15 دقیقه پیش)</u>
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
─────────────────
💰 <b>واحد پول:</b> {1}
📈 <b>نرخ ارز:</b> {2} ΩXR  <u>(آپدیت {3} دقیقه پیش)</u>
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
🌍 <b>فعلاً ملت فعالی وجود نداره</b>

بعداً دوباره برگرد و یکی از ملت‌های فعال رو انتخاب کن.
"""
        keyboard = None
    else:
        text = """
🌍 <b>ملت خودت رو انتخاب کن</b>

5 ملت در هر صفحه نمایش داده می‌شه.
روی یک ملت بزن تا پروفایل کاملش رو ببینی.
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
نرخ ارز: {3} ΩXR
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
    await state.update_data(
        username=name,
        onboarding_started_at=time.time(),
    )
    await _render_nation_page(message, state, page=0)


@router.callback_query(
    F.data.startswith("nation_page:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def nation_page(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        page = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ صفحه معتبر نیست."), show_alert=True)
        return

    await call.answer()
    if call.message:
        await _render_nation_page(call.message, state, page=page, edit=True)


@router.callback_query(
    F.data.startswith("select_nation:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def select_nation(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        nation_id = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ ملت معتبر نیست."), show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)

    if nation is None or not nation.is_active:
        await call.answer(
            rtl_html("⚠️ این ملت دیگر فعال نیست."),
            show_alert=True,
        )
        return

    await state.update_data(
        selected_nation_id=nation_id,
        onboarding_started_at=time.time(),
    )
    await call.answer()

    if call.message:
        profile = await render_nation_profile(nation)
        await _safe_edit_text(
            call,
            profile,
            _nation_profile_keyboard(nation_id),
        )


@router.callback_query(
    F.data == "back_to_nations",
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def back_to_nations(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    await call.answer()
    data = await state.get_data()

    if call.message:
        await _render_nation_page(
            call.message,
            state,
            page=int(data.get("nation_page", 0)),
            edit=True,
        )


@router.callback_query(
    F.data.startswith("confirm_nation:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def confirm_nation(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        nation_id = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ ملت معتبر نیست."), show_alert=True)
        return

    data = await state.get_data()
    username = (data.get("username") or "").strip()
    if not username:
        await call.answer(
            rtl_html("⏱ ثبت‌نام منقضی شد. /start بزن."),
            show_alert=True,
        )
        await state.clear()
        return

    try:
        # The canonical join flow owns membership and home_nation_id assignment.
        async with async_session() as session:
            async with session.begin():
                user = await session.get(
                    User,
                    call.from_user.id,
                    with_for_update=True,
                )
                if user is None:
                    user = User(
                        user_id=call.from_user.id,
                        username=username,
                        # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                        home_nation_id=None,
                        balance=Decimal("0.00"),
                        xr_balance=Decimal("0.00"),
                        role="player",
                    )
                    session.add(user)
                    await session.flush()
                else:
                    user.username = username
    except IntegrityError:
        logger.exception(
            "Onboarding registration transaction failed | user_id=%s nation_id=%s",
            call.from_user.id,
            nation_id,
        )
        await call.answer(
            rtl_html(
                """
🔴 <b>این نام در همین لحظه ثبت شد</b>

یک نام دیگر برای معامله‌گرت انتخاب کن.
"""
            ),
            show_alert=True,
        )
        return

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

    if "درخواست عضویت ارسال شد" in result_message or "قبلاً ثبت شده" in result_message:
        if call.message:
            await call.message.edit_text(
                result_message,
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await call.answer("📝 درخواست عضویت ثبت شد.")
        return

    if nation is None:
        await call.answer("⚠️ اطلاعات ملت در دسترس نیست.", show_alert=True)
        return

    await state.update_data(first_trade_available=True)

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
            rank = await get_nation_rank(session, nation_id)
            total_nations = await session.scalar(
                select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))
            ) or 0

    if user is None or nation is None or holding is None:
        await call.answer("⚠️ حساب ملت ناقص است. /start بزن.", show_alert=True)
        return

    initial_omx = Decimal("500") * nation.exchange_rate
    text = f"""{html.escape(nation.flag_emoji or "🏴")} <b>{html.escape(nation.name)}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{user_mention(call.from_user)}، شهروند رسمی این ملت شدی.

💰 موجودی اولیه:
<b>500 <code>{html.escape(nation.currency_code)}</code> ≈ {fmt_amount(initial_omx)} ΩXR</b>

─────────────────
{get_rate_emoji(get_rate_change(nation))} نرخ <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_rate(nation.exchange_rate)} ΩXR</b>
<i>{fmt_pct(get_rate_change(nation))} نسبت به دیروز</i>

🏆 رتبه #{to_fa(rank)} از {to_fa(total_nations)}
👥 {to_fa(nation.member_count)} عضو
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
هر معامله‌ات روی نرخ <code>{html.escape(nation.currency_code)}</code> اثر میذاره."""
    await _safe_edit_text(call, text, first_trade_keyboard())
    await call.answer()

@router.callback_query(F.data == "first_trade_tutorial")
async def first_trade_tutorial(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"):
        await call.answer()
        return
    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    if not user or not nation:
        await call.answer("⚠️ اطلاعات معامله پیدا نشد.", show_alert=True)
        return
    receive_omx = Decimal("50") * nation.exchange_rate
    change = get_rate_change(nation)
    text = ("⚡ <b>اولین معامله</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" f"📤 می‌فروشی:   <b>50 <code>{html.escape(nation.currency_code)}</code></b>\n" f"📥 دریافت می‌کنی: <b>{fmt_amount(receive_omx)} <code>ΩXR</code></b>\n\n─────────────────\n" f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\n" f"{get_rate_emoji(change)} تغییر 24h: <b>{fmt_pct(change)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
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
                    select(User).where(
                        User.user_id == call.from_user.id
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if not user or not user.home_nation_id:
                await call.answer("⚠️ حساب پیدا نشد. /start بزن.", show_alert=True)
                return
            nation = await session.get(Nation, user.home_nation_id, with_for_update=True)
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == user.home_nation_id,
                ).with_for_update()
            )
            if not nation or not holding:
                await call.answer("⚠️ حساب پیدا نشد. /start بزن.", show_alert=True)
                return
            if holding.amount < Decimal("50"):
                await call.answer("🔴 موجودی کافی نیست.", show_alert=True)
                return
            rate = nation.exchange_rate
            receive_omx = Decimal("50") * rate
            holding.amount -= Decimal("50")
            user.balance = holding.amount
            user.xr_balance += receive_omx
            nation.trade_volume_24h += receive_omx
            session.add(Transaction(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                transaction_type="sell",
                spend_xr=receive_omx,
                amount=Decimal("50"),
                fee_xr=Decimal("0"),
                rate=rate,
            ))
            session.add(UserActivity(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                activity_type="trade",
            ))
            await sync_user_balance(session, user.user_id)

    text = (
        "✅ <b>معامله انجام شد.</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 فروختی:   <s>50 {html.escape(nation.currency_code)}</s>\n"
        f"📥 دریافتی:  <b>{fmt_amount(receive_omx)} ΩXR</b>\n\n"
        "─────────────────\n"
        f"💰 موجودی:\n<code>{html.escape(nation.currency_code)}</code>: <b>{fmt_amount(holding.amount)}</b>\n"
        f"<code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>\n\n"
        "─────────────────\n✨ <b>«اولین قدم در بازارهای OPEX» باز شد.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await state.clear()
    await _safe_edit_text(call, text)
    await call.answer()
    if call.message:
        await call.message.answer(
            "🌐 <b>منوی اصلی آماده‌ست.</b>",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    currency_code = nation.currency_code if nation else "ارز"
    balance = fmt_amount(nation and (await _get_holding_amount(call.from_user.id, nation.nation_id)) or Decimal("500")) if nation else "500"
    text = f"{html.escape(call.from_user.first_name or 'معامله‌گر')}، هر وقت آماده شدی\nاز 💹 بازار شروع کن.\n\n💰 موجودی: {balance} <code>{html.escape(currency_code)}</code>"
    await _safe_edit_text(call, text)
    await call.answer()
    if call.message:
        await call.message.answer("🌐 <b>منوی اصلی آماده‌ست.</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")


async def _get_holding_amount(user_id: int, nation_id: int) -> Decimal:
    async with async_session() as session:
        async with session.begin():
            holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user_id, CurrencyHolding.nation_id == nation_id))
        return holding.amount if holding else Decimal("500")
