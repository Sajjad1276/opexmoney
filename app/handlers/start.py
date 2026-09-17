from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, ReplyParameters
from sqlalchemy import func, select

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.keyboards.inline import cancel_keyboard, first_trade_keyboard, no_nation_keyboard, nation_keyboard, start_keyboard, trade_confirmation_keyboard
from app.keyboards.reply import main_menu
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.user_service import get_user, username_exists
from app.states.onboarding import OnboardingStates
from app.utils.formatting import fmt_amount, fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa

router = Router(name="start")
logger = logging.getLogger(__name__)

START_TEXT = """<b>سلام.</b>
بازارهای جهانی OPEX هر روز میلیاردها واحد ارز
جابه‌جا می‌کنند.
بعضی‌ها ثروت می‌سازند.  بعضی‌ها ملت می‌سازند.
<b>تو چی می‌خوای؟</b>"""
USERNAME_TEXT = """<b>خوبه.</b>
بازارها با یک اسم می‌شناسنت.  اسمی که روی تابلوی معاملات نمایش داده میشه.
<b>اسم معامله‌گرت رو بنویس:</b>"""


def _valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z\u0600-\u06FF]{3,15}", value))


async def _safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Message edit failed: %s", exc)
        return False


async def show_dashboard(message: Message, user: User) -> None:
    async with async_session() as session:
        nation = await session.get(Nation, user.home_nation_id) if user.home_nation_id else None
        if nation is None:
            await message.answer("<b>OPEX MONEY</b>\nحسابت آماده است، اما هنوز ملت اصلی نداری.", reply_markup=main_menu(), parse_mode="HTML")
            return
        rank = nation.nation_rank or await get_nation_rank(session, nation.nation_id)
        total_nations = await session.scalar(select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))) or 0
        active = nation.active_members_24h
        change = get_rate_change(nation)
        minutes = max(0, int((datetime.utcnow() - nation.last_rate_update).total_seconds() // 60)) if nation.last_rate_update else 0
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user.user_id, CurrencyHolding.nation_id == nation.nation_id))
        balance = holding.amount if holding else user.balance
        user.balance = balance
        text = (
            "🌐 <b>OPEX MONEY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 معامله‌گر: <b>{html.escape(user.username)}</b>\n"
            f"🏛 ملت: <b>{html.escape(nation.name)}</b>\n"
            f"💰 <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_amount(balance)}</b> | <code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>\n"
            f"{get_rate_emoji(change)} <b>نرخ {html.escape(nation.currency_code)}:</b> <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> ({fmt_pct(change)} امروز)\n"
            f"🏆 رتبه ملت: <b>#{to_fa(rank)}</b> از {to_fa(total_nations)} | 👥 فعال: <b>{to_fa(active)}</b>\n"
            f"⏱ <i>آخرین آپدیت نرخ: {to_fa(minutes)} دقیقه پیش</i>"
        )
    await message.answer(text, reply_markup=main_menu(), parse_mode="HTML")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        user = await get_user(session, message.from_user.id)
    if user is not None:
        async with async_session() as session:
            session.add(UserActivity(user_id=user.user_id, nation_id=user.home_nation_id, activity_type="login")) if user.home_nation_id else None
            await session.commit()
        await show_dashboard(message, user)
        return
    await message.answer(START_TEXT, reply_markup=start_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "start_player")
async def start_player(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear(); await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await _safe_edit(call, USERNAME_TEXT, cancel_keyboard()); await call.answer()


@router.message(OnboardingStates.SET_USERNAME_PLAYER)
async def receive_username(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not _valid_username(username):
        await message.answer("اسم معتبر نیست.\n۳ تا ۱۵ کاراکتر و فقط حروف فارسی یا انگلیسی بنویس.", reply_parameters=ReplyParameters(message_id=message.message_id), parse_mode="HTML"); return
    async with async_session() as session:
        if await username_exists(session, username):
            await message.answer("این اسم قبلاً ثبت شده. یه اسم دیگه امتحان کن:", reply_parameters=ReplyParameters(message_id=message.message_id), parse_mode="HTML"); return
        await state.update_data(username=username)
        nations = await get_active_nations(session, limit=3)
    if not nations:
        await message.answer(f"<b>«{html.escape(username)}»</b> ثبت شد.\nهنوز هیچ ملتی ساخته نشده.\nاولین نفر باش و ملت بساز!", reply_markup=no_nation_keyboard(), parse_mode="HTML"); return
    lines = [f"«<b>{html.escape(username)}</b>» ثبت شد.", "<b>سه ملت فعال:</b>"]
    for nation in nations:
        lines.append(f"🏛 <b>{html.escape(nation.name)}</b> · {html.escape(nation.currency_code)} · {fmt_rate(nation.exchange_rate)} ΩXR · {to_fa(nation.member_count)} عضو · #{to_fa(nation.nation_rank or 0)}")
    await message.answer("\n".join(lines), reply_markup=nation_keyboard(nations), parse_mode="HTML")


@router.callback_query(F.data.startswith("join_nation_"))
async def join_nation(call: CallbackQuery, state: FSMContext) -> None:
    try: nation_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, AttributeError): await call.answer("انتخاب ملت معتبر نیست.", show_alert=True); return
    data = await state.get_data(); username = data.get("username")
    if not username: await call.answer("فرآیند ثبت‌نام منقضی شده. دوباره /start بزن.", show_alert=True); return
    async with async_session() as session:
        existing = await get_user(session, call.from_user.id)
        if existing is not None:
            await state.clear(); await call.answer();
            if call.message: await show_dashboard(call.message, existing)
            return
        result = await session.execute(select(Nation).where(Nation.nation_id == nation_id, Nation.is_active.is_(True)).with_for_update()); nation = result.scalar_one_or_none()
        if nation is None: await call.answer("این ملت دیگر در دسترس نیست.", show_alert=True); return
        user = User(user_id=call.from_user.id, username=username, home_nation_id=nation.nation_id, balance=Decimal("500.00"), xr_balance=Decimal("0.00"), role="player")
        holding = CurrencyHolding(user_id=user.user_id, nation_id=nation.nation_id, amount=Decimal("500.00"))
        nation.member_count += 1
        session.add_all([user, holding, UserActivity(user_id=user.user_id, nation_id=nation.nation_id, activity_type="login")]); await session.flush()
        rank = await get_nation_rank(session, nation.nation_id); await session.commit()
        text = f"🏛 به <b>«{html.escape(nation.name)}»</b> خوش اومدی.\nموجودی اولیه: <b>۵۰۰ {html.escape(nation.currency_code)}</b> ≈ {fmt_amount(Decimal('500')*nation.exchange_rate)} ΩXR\nملتت الان رتبه <b>#{to_fa(rank)}</b> در جهانه."
    await state.update_data(first_trade_available=True); await _safe_edit(call, text, first_trade_keyboard()); await call.answer()


@router.callback_query(F.data == "first_trade_tutorial")
async def first_trade_tutorial(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"): await call.answer(); return
    async with async_session() as session:
        user = await get_user(session, call.from_user.id); nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    if not user or not nation: await call.answer("اطلاعات معامله پیدا نشد.", show_alert=True); return
    received = Decimal("50") * nation.exchange_rate
    text = f"<b>راهنمای اولین معامله:</b>\nالان داری <b>۵۰ {html.escape(nation.currency_code)}</b> میفروشی و به جاش <b>ΩXR</b> می‌خری.\nنرخ فعلی: ۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR\nدریافت می‌کنی: <b>{fmt_amount(received)} ΩXR</b>"
    await _safe_edit(call, text, trade_confirmation_keyboard()); await call.answer()


@router.callback_query(F.data == "confirm_first_trade")
async def confirm_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"): await call.answer(); return
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.user_id == call.from_user.id).with_for_update())).scalar_one_or_none()
        nation = await session.get(Nation, user.home_nation_id, with_for_update=True) if user and user.home_nation_id else None
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == call.from_user.id, CurrencyHolding.nation_id == user.home_nation_id).with_for_update()) if user and user.home_nation_id else None
        if not user or not nation or not holding: await call.answer("حساب معامله پیدا نشد.", show_alert=True); return
        if holding.amount < 50: await call.answer("موجودی برای این معامله کافی نیست.", show_alert=True); return
        received = Decimal("50") * nation.exchange_rate; holding.amount -= 50; user.balance = holding.amount; user.xr_balance += received; nation.trade_volume_24h += received
        session.add(Transaction(user_id=user.user_id, nation_id=nation.nation_id, transaction_type="sell", spend_xr=received, amount=50, fee_xr=Decimal("0"), rate=nation.exchange_rate)); session.add(UserActivity(user_id=user.user_id, nation_id=nation.nation_id, activity_type="trade")); await session.commit()
        text = f"✅ <b>اولین معامله انجام شد!</b>\n💰 موجودی: <b>{fmt_amount(user.balance)} {html.escape(nation.currency_code)}</b> + <b>{fmt_amount(user.xr_balance)} ΩXR</b>\nاین معامله روی نرخ ملتت اثر گذاشت."
    await state.clear(); await _safe_edit(call, text, None)
    if call.message: await call.message.answer("<b>منوی اصلیت آماده‌ست.</b>", reply_markup=main_menu(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear(); await _safe_edit(call, "باشه، هر وقت خواستی از منوی بازار اقدام کن.", None)
    if call.message: await call.message.answer("<b>منوی اصلیت آماده‌ست.</b>", reply_markup=main_menu(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "start_founder")
async def founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear(); await _safe_edit(call, "این بخش به زودی فعال میشه."); await call.answer()


@router.callback_query(F.data == "cancel_start")
async def cancel_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear(); await _safe_edit(call, "هر وقت آماده شدی /start بزن.")
    if call.message: await call.message.answer("هر وقت آماده شدی /start بزن.", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    await call.answer()
