from __future__ import annotations

import html
import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, ReplyParameters
from sqlalchemy import select

from app.database.models import Nation, User
from app.database.session import async_session
from app.keyboards.inline import (
    cancel_keyboard,
    first_trade_keyboard,
    no_nation_keyboard,
    nation_keyboard,
    start_keyboard,
    trade_confirmation_keyboard,
)
from app.keyboards.reply import main_menu
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.user_service import get_user, username_exists
from app.states.onboarding import OnboardingStates

router = Router(name="start")
logger = logging.getLogger(__name__)

START_TEXT = """<b>سلام.</b>

بازارهای جهانی OPEX هر روز میلیاردها واحد ارز
جابه‌جا می‌کنند.
بعضی‌ها ثروت می‌سازند.  بعضی‌ها ملت می‌سازند.
<b>تو چی می‌خوای؟</b>"""

USERNAME_TEXT = """<b>خوبه.</b>
بازارها با یک اسم می‌شناسنت. اسمی که روی تابلوی معاملات نمایش داده میشه.
<b>اسم معامله‌گرت رو بنویس:</b>
<small>۳ تا ۱۵ کاراکتر، فقط حروف فارسی یا انگلیسی.</small>"""


def _valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z\u0600-\u06FF]{3,15}", value))


async def _safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> bool:
    try:
        if call.message is None:
            return False
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Onboarding message edit failed: %s", exc)
        if call.message is not None:
            try:
                await call.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
                return True
            except TelegramBadRequest:
                return False
    return False


async def show_dashboard(message: Message, user: User) -> None:
    nation = None
    if user.home_nation_id is not None:
        async with async_session() as session:
            nation = await session.get(Nation, user.home_nation_id)

    if nation is None:
        await message.answer(
            "<b>OPEX MONEY</b>\n\nحسابت آماده است، اما هنوز ملتت مشخص نیست.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        return

    async with async_session() as session:
        rank = await get_nation_rank(session, nation.nation_id)

    await message.answer(
        f"<b>مرکز فرماندهی</b>\n"
        f"🏛 <b>{html.escape(nation.name)}</b> · {html.escape(nation.currency_code)}\n"
        f"💰 موجودی: <b>{user.balance:.2f}</b> {html.escape(nation.currency_code)}\n"
        f"ΩXR: <b>{user.xr_balance:.2f}</b> · رتبه ملت: <b>#{rank}</b>",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)

    if user is not None:
        await show_dashboard(message, user)
        return

    await message.answer(START_TEXT, reply_markup=start_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "start_player")
async def start_player(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await _safe_edit(call, USERNAME_TEXT, cancel_keyboard())


@router.message(OnboardingStates.SET_USERNAME_PLAYER)
async def receive_username(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not _valid_username(username):
        await message.answer(
            "<b>اسم معتبر نیست.</b>\n۳ تا ۱۵ کاراکتر و فقط حروف فارسی یا انگلیسی بنویس.",
            reply_parameters=ReplyParameters(message_id=message.message_id),
            parse_mode="HTML",
        )
        return

    async with async_session() as session:
        if await username_exists(session, username):
            await message.answer(
                "این اسم قبلاً ثبت شده. یه اسم دیگه امتحان کن:",
                reply_parameters=ReplyParameters(message_id=message.message_id),
                parse_mode="HTML",
            )
            return

        await state.update_data(username=username)
        nations = await get_active_nations(session, limit=3)

    safe_username = html.escape(username)
    if not nations:
        await message.answer(
            f"<b>«{safe_username}»</b> ثبت شد.\n\nهنوز هیچ ملتی ساخته نشده.\nاولین نفر باش و ملت بساز!",
            reply_markup=no_nation_keyboard(),
            parse_mode="HTML",
        )
        return

    lines = [
        f"✅ «<b>{safe_username}</b>» ثبت شد.",
        "حالا باید به یه ملت بپیوندی. ارز اون ملت، پایه حسابت میشه.",
        "<b>سه ملت فعال الان:</b>",
    ]
    for nation in nations:
        async with async_session() as session:
            rank = await get_nation_rank(session, nation.nation_id)
        lines.append(
            f"🏛 <b>{html.escape(nation.name)}</b> · ارز: {html.escape(nation.currency_code)} · "
            f"نرخ: {nation.exchange_rate:.2f} ΩXR · اعضا: {nation.member_count} · رتبه: #{rank}"
        )

    await message.answer("\n".join(lines), reply_markup=nation_keyboard(nations), parse_mode="HTML")


@router.callback_query(F.data.startswith("join_nation_"))
async def join_nation(call: CallbackQuery, state: FSMContext) -> None:
    try:
        nation_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, AttributeError):
        await call.answer("انتخاب ملت معتبر نیست.", show_alert=True)
        return
    await call.answer()

    data = await state.get_data()
    username = data.get("username")
    if not username:
        await call.answer("فرآیند ثبت‌نام منقضی شده. دوباره /start بزن.", show_alert=True)
        return

    async with async_session() as session:
        existing = await get_user(session, call.from_user.id)
        if existing is not None:
            await state.clear()
            if call.message:
                await show_dashboard(call.message, existing)
            return

        result = await session.execute(
            select(Nation).where(Nation.nation_id == nation_id).with_for_update()
        )
        nation = result.scalar_one_or_none()
        if nation is None:
            await call.answer("این ملت دیگر در دسترس نیست.", show_alert=True)
            return

        user = User(
            user_id=call.from_user.id,
            username=username,
            home_nation_id=nation.nation_id,
            balance=Decimal("500.00"),
            xr_balance=Decimal("0.00"),
            role="player",
        )
        nation.member_count += 1
        session.add(user)
        await session.flush()
        rank = await get_nation_rank(session, nation.nation_id)
        await session.commit()

        text = (
            f"🏛 به <b>«{html.escape(nation.name)}»</b> خوش اومدی.\n"
            f"موجودی اولیه: <b>500 {html.escape(nation.currency_code)}</b> ≈ {Decimal('500') * nation.exchange_rate:.2f} ΩXR\n"
            f"ملتت الان رتبه <b>#{rank}</b> در جهانه. هر معامله‌ای که میکنی روی این رتبه تأثیر میذاره."
        )

    await state.update_data(first_trade_available=True)
    await _safe_edit(call, text, first_trade_keyboard())


@router.callback_query(F.data == "first_trade_tutorial")
async def first_trade_tutorial(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    if not data.get("first_trade_available"):
        return

    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        if user is None or user.home_nation_id is None:
            return
        nation = await session.get(Nation, user.home_nation_id)
        if nation is None:
            return

    rate = nation.exchange_rate
    received = Decimal("50") * rate
    text = (
        f"<b>راهنمای اولین معامله:</b>\n"
        f"الان داری <b>50 {html.escape(nation.currency_code)}</b> میفروشی و به جاش <b>ΩXR</b> می‌خری.\n"
        f"نرخ فعلی: 1 {html.escape(nation.currency_code)} = {rate:.2f} ΩXR\n"
        f"دریافت می‌کنی: <b>{received:.2f} ΩXR</b>"
    )
    await _safe_edit(call, text, trade_confirmation_keyboard())


@router.callback_query(F.data == "confirm_first_trade")
async def confirm_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    if not data.get("first_trade_available"):
        return

    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        if user is None or user.home_nation_id is None:
            return

        result = await session.execute(
            select(Nation).where(Nation.nation_id == user.home_nation_id).with_for_update()
        )
        nation = result.scalar_one_or_none()
        if nation is None:
            return

        if user.balance < Decimal("50"):
            await call.answer("موجودی برای این معامله کافی نیست.", show_alert=True)
            return

        rate = nation.exchange_rate
        received = Decimal("50") * rate
        user.balance -= Decimal("50")
        user.xr_balance += received
        await session.commit()

        currency = html.escape(nation.currency_code)
        result_text = (
            "✅ <b>اولین معامله انجام شد!</b>\n"
            f"موجودی جدید: {user.balance:.0f} {currency} + {user.xr_balance:.2f} ΩXR\n\n"
            "<b>منوی اصلیت آماده‌ست.</b>"
        )

    await state.update_data(first_trade_available=False)
    await _safe_edit(call, result_text, None)
    if call.message:
        await call.message.answer("<b>منوی اصلیت آماده‌ست.</b>", reply_markup=main_menu(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    if not data.get("first_trade_available"):
        return

    await state.update_data(first_trade_available=False)
    await _safe_edit(call, "باشه، هر وقت خواستی از منوی بازار اقدام کن.\n\n<b>منوی اصلیت آماده‌ست.</b>", None)
    if call.message:
        await call.message.answer("<b>منوی اصلیت آماده‌ست.</b>", reply_markup=main_menu(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data == "start_founder")
async def founder(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _safe_edit(call, "این بخش به زودی فعال میشه.")


@router.callback_query(F.data == "cancel_start")
async def cancel_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _safe_edit(call, "هر وقت آماده شدی /start بزن.")
    if call.message:
        await call.message.answer("هر وقت آماده شدی /start بزن.", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
