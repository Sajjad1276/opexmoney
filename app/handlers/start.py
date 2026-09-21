from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database.session import async_session
from app.handlers.dashboard import show_dashboard
from app.handlers.deep_link import extract_start_param, handle_deep_link
from app.keyboards.inline import welcome_keyboard
from app.services.user_service import get_user, is_fully_registered
from app.utils.formatting import imperial_datetime, rtl_html

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    param = extract_start_param(message)
    if param and await handle_deep_link(message, param, state):
        return

    await state.clear()

    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    if user is not None:
        await show_dashboard(message, user)
        return

    imperial_date, imperial_time = imperial_datetime()
    text = (
        "🌐 <b>به OPEX MONEY خوش اومدی</b>\n\n"
        "اقتصاد زنده است؛ تصمیم‌های تو مهم‌اند.\n\n"
        "🎯 <b>تصمیم بگیر، معامله کن، رشد کن.</b>\n\n"
        f"📅 {imperial_date}\n"
        f"🕐 {imperial_time}"
    )
    await message.answer(
        rtl_html(text),
        reply_markup=welcome_keyboard(),
        parse_mode=ParseMode.HTML,
    )


__all__ = ["router", "cmd_start"]
