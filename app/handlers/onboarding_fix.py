from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.handlers.start import _safe_edit_caption, _safe_edit_text, user_mention
from app.keyboards.inline import cancel_keyboard
from app.states.onboarding import OnboardingStates
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name

router = Router(name="onboarding_fix")

USERNAME_CAPTION = """💹 <b>اسم معامله‌گرت رو انتخاب کن</b>
─────────────────
{user_mention}، این اسم روی تابلوی
معاملات OPEX نمایش داده میشه.

بنویس:
· ۳ تا ۱۵ حرف انگلیسی
· فقط حروف A-Z
· بدون فاصله، عدد و @"""

INVALID_NAME = """🔴 این نام قابل قبول نیست.

فقط ۳ تا ۱۵ حرف انگلیسی وارد کن.
مثال: <code>Arman</code>"""

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
        if call.message:
            await call.message.answer("", reply_markup=ReplyKeyboardRemove())
        await call.answer()
    except TelegramBadRequest:
        await call.answer("ثبت‌نام لغو شد.", show_alert=False)
