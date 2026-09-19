from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

router = Router(name="sections")

@router.message(F.text == "⚙️ تنظیمات")
async def settings(message: Message) -> None:
    await message.answer(
        "⚙️ <b>تنظیمات</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "این بخش هنوز در نسخه فعلی فعال نشده.",
        parse_mode="HTML",
    )
