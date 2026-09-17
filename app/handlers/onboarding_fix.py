from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database.models import Nation
from app.database.session import async_session
from app.handlers.start import _safe_edit_caption, _safe_edit_text, nation_list_text, user_mention
from app.keyboards.inline import cancel_keyboard, nation_keyboard, no_nation_keyboard
from app.services.nation_service import get_active_nations
from app.services.user_service import username_exists
from app.states.onboarding import OnboardingStates
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name

router = Router(name="onboarding_fix")

USERNAME_CAPTION = """💹 <b>اسم معامله‌گرت رو انتخاب کن</b>
─────────────────
{user_mention}، این اسم روی تابلوی
معاملات OPEX نمایش داده میشه.

بنویس:
· ۳ تا ۱۵ کاراکتر
· فقط حروف انگلیسی و عدد
· بدون فاصله، @ و علامت خاص"""

INVALID_NAME = """🔴 این نام قابل قبول نیست.

فقط ۳ تا ۱۵ کاراکتر انگلیسی وارد کن.
حروف و عدد مجازه، اما فاصله و علامت خاص نه.
مثال: <code>Arman7</code>"""

BLOCKED_NAME = """🔴 این نام قابل قبول نیست.

این نام شامل عبارت نامناسب یا مستهجن است.
یک نام صحیح و مناسب برای معامله‌گر انتخاب کن."""

CANCEL_TEXT = """{user_name}، ثبت‌نام لغو شد.

هر وقت خواستی، /start بزن."""


@router.callback_query(F.data == "start_player")
async def start_player_fix(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    text = USERNAME_CAPTION.format(user_mention=user_mention(call.from_user))
    try:
        if call.message is None:
            await call.answer("صفحه ثبت‌نام باز نشد.", show_alert=True)
            return
        if getattr(call.message, "photo", None):
            await call.message.edit_caption(caption=text, reply_markup=cancel_keyboard(), parse_mode="HTML")
        else:
            await _safe_edit_text(call, text, cancel_keyboard())
        await call.answer()
    except TelegramBadRequest:
        await call.answer("صفحه ثبت‌نام باز نشد. دوباره /start بزن.", show_alert=True)


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(is_blocked_trader_name))
async def reject_blocked_name(message: Message, state: FSMContext) -> None:
    await message.answer(BLOCKED_NAME, parse_mode="HTML")


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(lambda value: not is_valid_trader_name(value or "")))
async def reject_non_english_name(message: Message, state: FSMContext) -> None:
    await message.answer(INVALID_NAME, parse_mode="HTML")


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(is_valid_trader_name))
async def accept_valid_name(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()

    # Profanity is checked before the database lookup as a defense-in-depth
    # layer. The blocked-name handler normally catches this first.
    if is_blocked_trader_name(username):
        await message.answer(BLOCKED_NAME, parse_mode="HTML")
        return

    async with async_session() as session:
        if await username_exists(session, username):
            await message.answer(
                f"🔴 «{html.escape(username)}» قبلاً ثبت شده.\n\nیه اسم دیگه انتخاب کن:",
                parse_mode="HTML",
            )
            return

        await state.update_data(username=username)
        await state.set_state(OnboardingStates.SELECT_NATION)
        nations = await get_active_nations(session, limit=3)

    if not nations:
        text = (
            f"✅ <b>«{html.escape(username)}»</b> ثبت شد.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏳ هنوز هیچ ملتی وجود نداره.\n\n"
            f"{user_mention(message.from_user)}، تو اولین نفری هستی\n"
            "که وارد OPEX میشی.\n\n"
            "اولین ملت تاریخ رو بساز.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await message.answer(text, reply_markup=no_nation_keyboard(), parse_mode="HTML")
        return

    await message.answer(
        nation_list_text(message.from_user, username, nations),
        reply_markup=nation_keyboard(nations),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_start")
async def cancel_start_fix(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_name = html.escape(call.from_user.first_name or "معامله‌گر")
    text = CANCEL_TEXT.format(user_name=user_name)
    try:
        if call.message and getattr(call.message, "photo", None):
            await _safe_edit_caption(call, text)
        else:
            await _safe_edit_text(call, text)
        await call.answer()
    except TelegramBadRequest:
        await call.answer("ثبت‌نام لغو شد.", show_alert=False)
