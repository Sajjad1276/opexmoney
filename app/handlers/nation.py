from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.states.nation import NationStates

logger = logging.getLogger(__name__)
nation_router = Router(name="nation")


@nation_router.callback_query(
    F.data == "create_nation",
    StateFilter(NationStates.IDLE, None),
)
async def start_nation_creation(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await call.answer()
    if call.message:
        await call.message.answer("🏛 ساخت ملت در مرحله بعدی فعال میشه.")
