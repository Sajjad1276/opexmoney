from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_session
from app.services.nation_service import create_nation

logger = logging.getLogger(__name__)

founder_router = Router(name="founder")

class FounderStates(StatesGroup):
    name = State()
    currency = State()
    flag = State()

@founder_router.callback_query(F.data == "found_nation")
async def start_founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FounderStates.name)
    await call.message.edit_text(
        "🏛 <b>تأسیس ملت</b>\n\n"
        "نام ملت خود را وارد کنید (مثلاً: ایران):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="↩️ انصراف", callback_data="back_to_nations_panel")]
            ]
        ),
        parse_mode="HTML",
    )
    await call.answer()