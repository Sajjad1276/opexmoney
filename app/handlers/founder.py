from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.database.session import async_session
from app.services.nation_service import create_nation

founder_router = Router(name="founder")

class FounderStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_currency = State()
    waiting_for_flag = State()

@founder_router.message(F.text == "🏛 تأسیس ملت")
async def start_founder(message: Message, state: FSMContext) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ شروع", callback_data="found_nation_start")]
        ]
    )
    await message.answer(
        "برای تأسیس ملت جدید، باید نام، کد ارز و پرچم انتخاب کنی.\n" 
        "آماده‌ای؟",
        reply_markup=keyboard
    )
