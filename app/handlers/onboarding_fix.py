from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.handlers.start import USERNAME_CAPTION, _safe_edit_text, user_mention, current_date_fa
from app.keyboards.inline import cancel_keyboard
from app.states.onboarding import OnboardingStates

router = Router(name="onboarding_fix")


@router.callback_query(F.data == "start_player")
async def start_player_fix(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    text = USERNAME_CAPTION.format(
        bot_name="OPEX MONEY",
        user_mention=user_mention(call.from_user),
        current_date=current_date_fa(),
    )
    try:
        if call.message is None:
            await call.answer("صفحه ثبت‌نام باز نشد.", show_alert=True)
            return
        if getattr(call.message, "photo", None):
            await call.message.edit_caption(caption=text, reply_markup=cancel_keyboard(), parse_mode="HTML")
        else:
            await _safe_edit_text(call, text, cancel_keyboard())
        await call.answer()
    except TelegramBadRequest as exc:
        if "there is no text in the message" in str(exc).lower() or "message to edit not found" in str(exc).lower():
            await call.answer("صفحه ثبت‌نام باز نشد. دوباره /start بزن.", show_alert=True)
        else:
            await call.answer("صفحه ثبت‌نام باز نشد.", show_alert=True)
