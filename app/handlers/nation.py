from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ButtonStyle
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.states.onboarding import OnboardingStates

logger = logging.getLogger(__name__)
nation_router = Router(name="nation")


def nations_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏛 تأسیس ملت جدید",
                    callback_data="create_nation",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        ]
    )


@nation_router.message(F.text == "🌍 ملت‌ها")
async def open_nations(message: Message) -> None:
    await message.answer(
        "🌍 <b>ملت‌ها</b>\n"
        "از این بخش می‌تونی وارد امکانات ملت‌ها بشی.\n"
        "🏛 تأسیس ملت جدید از اینجا انجام میشه.",
        reply_markup=nations_keyboard(),
        parse_mode="HTML",
    )
@nation_router.callback_query(
    F.data == "create_nation",
    StateFilter(None, OnboardingStates.SELECT_NATION),
)
async def start_nation_creation(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await call.answer()
    if call.message:
        await call.message.answer("🏛 ساخت ملت در مرحله بعدی فعال میشه.")
