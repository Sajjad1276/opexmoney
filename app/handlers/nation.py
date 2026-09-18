from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ButtonStyle
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


logger = logging.getLogger(__name__)
nation_router = Router(name="nation")


def nations_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏛 تأسیس ملت جدید",
                    callback_data="found_nation",
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
