from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database.session import async_session
from app.keyboards.inline import restart_confirmation_keyboard
from app.keyboards.reply import main_menu_keyboard
from app.services.keyboard_state import KeyboardKind, keyboard_manager
from app.services.onboarding_draft import clear_draft
from app.services.user_service import is_fully_registered
from app.handlers.start import _show_help

interaction_router = Router(name="interaction")


async def _send_home_after_cancel(message: Message) -> None:
    async with async_session() as session:
        registered = await is_fully_registered(session, message.from_user.id)
    if registered:
        await keyboard_manager.send_message(
            message.bot,
            chat_id=message.chat.id,
            text="✅ ویزارد لغو شد. منوی اصلی آماده‌ست.",
            kind=KeyboardKind.REPLY,
            name="main_menu",
            markup=main_menu_keyboard(),
            parse_mode="HTML",
        )
    else:
        await keyboard_manager.send_message(
            message.bot,
            chat_id=message.chat.id,
            text="✅ ویزارد لغو شد. برای شروع دوباره، /start بزن.",
            kind=KeyboardKind.NONE,
            name="none",
            markup=None,
            parse_mode="HTML",
        )


@interaction_router.message(Command("start"))
async def system_start(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        from app.handlers.start import start
        await start(message, state)
        return

    await message.answer(
        "⚠️ هنوز داخل یک مرحله هستی. اگر /start را اجرا کنی، مرحله فعلی متوقف می‌شود.",
        reply_markup=restart_confirmation_keyboard(),
        parse_mode="HTML",
    )


@interaction_router.callback_query(F.data == "confirm_restart")
async def confirm_restart(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        async with session.begin():
            await clear_draft(session, call.from_user.id)
    await call.answer()
    if call.message:
        from app.handlers.start import start
        await start(call.message, state)


@interaction_router.callback_query(F.data == "keep_wizard")
async def keep_wizard(call: CallbackQuery) -> None:
    await call.answer("✅ مرحله فعلی حفظ شد.")


@interaction_router.message(Command("cancel"))
async def system_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        async with session.begin():
            await clear_draft(session, message.from_user.id)
    await _send_home_after_cancel(message)


@interaction_router.message(Command("help"))
async def system_help(message: Message) -> None:
    await _show_help(message)
