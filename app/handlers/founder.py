from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.database.session import async_session
from app.database.models import User
from app.services.user_service import is_fully_registered

founder_router = Router(name="founder")

@founder_router.message(F.text == "🏛 تأسیس ملت")
async def start_founder(message: Message, state: FSMContext) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id)
            if not await is_fully_registered(session, message.from_user.id):
                await message.answer("⚠️ اول باید وارد بازی بشی.")
                return
            if user.role == "founder":
                await message.answer("⚠️ تو قبلاً یک ملت تأسیس کردی.")
                return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="شروع تأسیس", callback_data="found_nation_start")]
        ]
    )
    await message.answer(
        "🏛 <b>تأسیس ملت</b>\n\n"
        "برای شروع تأسیس ملت جدید، دکمه زیر رو بزن.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )