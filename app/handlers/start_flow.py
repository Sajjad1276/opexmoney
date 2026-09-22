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
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.handlers.nation_management import _join_user
from app.handlers.onboarding import _begin_registration
from app.filters.profanity import profanity_filter
from app.utils.name_filter import TRADER_NAME_RE, is_blocked_trader_name, is_valid_trader_name
from app.keyboards.inline import (
    cancel_keyboard,
    first_trade_keyboard,
    suggested_name_keyboard,
    nation_selection_keyboard,
    trade_confirmation_keyboard,
    welcome_keyboard,
)
from app.keyboards.reply import main_menu_keyboard, new_player_menu_keyboard
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.dashboard_service import build_live_dashboard, render_live_dashboard
from app.services.mission_service import check_permanent_missions, increment_mission
from app.services.founder_service import cancel_draft
from app.services.user_service import get_registration_status, get_user, is_fully_registered, sync_user_balance, username_exists
from app.states.founder import FounderStates
from config import settings
from app.utils.formatting import (
    fmt_amount,
    fmt_pct,
    fmt_rate,
    get_rate_change,
    get_rate_emoji,
    imperial_datetime,
    to_fa,
)
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="start_flow")
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
        "\n",
        f"{user_mention(user)}، حالا باید به یه ملت بپیوندی.",
        "",
        "ارز اون ملت، پول اصلی حسابت میشه.",
        "هر معامله‌ات مستقیم روی نرخ اون ارز اثر میذاره.",
        "",
        "<b>🌍 ملت‌های فعال:</b>",
        "\n",
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
            lines.append("\n")
    lines.extend(
        [
            "\n",
            "نرخ‌ها هر 15 دقیقه آپدیت میشن.",
        ]
    )
    return "\n".join(lines)





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
        "❓ <b>راهنمای OPEX MONEY</b>\n\n"
        "<b>شروع بازی</b>\n"
        "۱) یک نام معامله‌گر ۳ تا ۲۰ کاراکتری انتخاب کن.\n"
        "۲) یک ملت فعال را انتخاب کن یا، در صورت داشتن شرایط، ملت خودت را تأسیس کن.\n"
        "۳) بعد از ورود به ملت، ۵۰۰ واحد از ارز همان ملت را داری و اولین معامله‌ات آماده است.\n"
        "۴) در اولین معامله، بخشی از ارز ملتت را به دلار تبدیل می‌کنی تا وارد بازار گسترده‌تر شوی.\n\n"
        "<b>بازار</b>\n"
        "در بازار می‌توانی با دلار ارز ملت‌ها را بخری یا بفروشی، فهرست ارزهای فعال را ببینی، نمودار قیمت بگیری، هشدار قیمت ثبت کنی و تاریخچه معاملاتت را بررسی کنی.\n"
        "نرخ‌ها هر ۱۵ دقیقه به‌روزرسانی می‌شوند و فعالیت اقتصادی، معاملات و فشار بازار روی آن‌ها اثر می‌گذارد.\n\n"
        "<b>ملت</b>\n"
        "بخش «ملت من» برای مشاهده و مدیریت ملت، بررسی آمار، خزانه و بخش‌های حکمرانی است.\n"
        "خزانه امکان واریز، برداشت و گزارش‌گیری دارد. در قانون اساسی می‌توان قوانین فعال، طرح‌های جدید، رأی‌گیری‌ها و تاریخ قوانین را دید.\n"
        "دسترسی‌های مدیریتی، بخش اعضا و لاگ فعالیت و امکانات مدیریتی ملت را باز می‌کند.\n\n"
        "<b>جنگ</b>\n"
        "جنگ بین ملت‌ها ۴۸ ساعت ادامه دارد. در پایان، ملت با نرخ ارز بالاتر برنده می‌شود و ۱۰٪ خزانه ملت بازنده به‌عنوان خسارت به برنده می‌رسد.\n\n"
        "<b>پیشرفت</b>\n"
        "مأموریت‌های روزانه و هفتگی برایت هدف و جایزه می‌سازند.\n"
        "در آکادمی، ماژول‌ها و درس‌ها را جلو می‌بری، کوئیز حل می‌کنی، XP و پاداش دلار می‌گیری و می‌توانی از اوپکس سؤال بپرسی.\n"
        "رتبه‌بندی هم سه نمای اصلی دارد: ملت‌ها، ثروتمندان و معامله‌گران.\n\n"
        "<b>قانون مهم</b>\n"
        "تصمیم‌ها روی اقتصاد بازی اثر می‌گذارند. بازار، ملت، معاملات، خزانه و رقابت بین ملت‌ها به هم متصل‌اند."
    )

    if replace_inline:
        await message.edit_text(
            rtl_html(text),
            reply_markup=welcome_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        rtl_html(text),
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
        "\n"
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


@router.message(F.text == "💰 پول من")
async def new_player_money(message: Message) -> None:
    from app.handlers.portfolio import show_portfolio
    await show_portfolio(message)




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
    text = ("⚡ <b>اولین معامله</b>\n\n" f"📤 می‌فروشی:   <b>50 <code>{html.escape(nation.currency_code)}</code></b>\n" f"📥 دریافت می‌کنی: <b>{fmt_amount(receive_omx)} <code>دلار</code></b>\n\n\n" f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} دلار</code>\n" f"{get_rate_emoji(change)} تغییر 24h: <b>{fmt_pct(change)}</b>\n")
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
        "✅ <b>اولین معامله انجام شد!</b>\n"
        "\n"
        f"📤 فروختی: <s>۵۰ {html.escape(nation.currency_code)}</s>\n"
        f"📥 دریافتی: <b>{fmt_amount(receive_omx)} دلار</b>\n\n"
        f"💰 حالا داری: <b>{fmt_amount(holding.amount)} {html.escape(nation.currency_code)}</b> + <b>{fmt_amount(user.xr_balance)} دلار</b>\n\n"
        "🎯 <b>قدم بعدی:</b>\n"
        "یک ارز دیگه از بازار بخر و ببین با تغییر نرخ، دارایی‌ات چطور بالا و پایین می‌شه."
    )
    await state.clear()
    await _safe_edit_text(
        call,
        text,
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💹 رفتن به بازار", callback_data="market_main", style=ButtonStyle.SUCCESS)],
                [InlineKeyboardButton(text="🏠 دیدن داشبورد", callback_data="back_to_dashboard")],
            ]
        ),
    )
    await call.answer("✅ اولین معامله‌ات ثبت شد")


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    currency_code = nation.currency_code if nation else "ارز"
    balance = fmt_amount(nation and (await _get_holding_amount(call.from_user.id, nation.nation_id)) or Decimal("500")) if nation else "500"
    text = f"{html.escape(call.from_user.first_name or 'معامله‌گر')}، فعلاً از معامله رد شدی.\n\n💰 موجودی: {balance} <code>{html.escape(currency_code)}</code>\n\nهر وقت آماده شدی، از بازار شروع کن."
    await _safe_edit_text(
        call,
        text,
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💹 رفتن به بازار", callback_data="market_main", style=ButtonStyle.SUCCESS)],
                [InlineKeyboardButton(text="🏠 دیدن داشبورد", callback_data="back_to_dashboard")],
            ]
        ),
    )
    await call.answer()


async def _get_holding_amount(user_id: int, nation_id: int) -> Decimal:
    async with async_session() as session:
        async with session.begin():
            holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user_id, CurrencyHolding.nation_id == nation_id))
        return holding.amount if holding else Decimal("500")