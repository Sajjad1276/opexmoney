from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

router = Router(name="sections")

@router.message(F.text == "⚡ مأموریت")
async def missions(message: Message) -> None:
    await message.answer(
        "⚡ <b>مأموریت</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "این بخش هنوز در نسخه فعلی فعال نشده.",
        parse_mode="HTML",
    )

@router.message(F.text == "🏆 رتبه‌بندی")
async def ranking(message: Message) -> None:
    await message.answer(
        "🏆 <b>رتبه‌بندی</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "این بخش هنوز در نسخه فعلی فعال نشده.",
        parse_mode="HTML",
    )

@router.message(F.text == "⚙️ تنظیمات")
async def settings(message: Message) -> None:
    await message.answer(
        "⚙️ <b>تنظیمات</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "این بخش هنوز در نسخه فعلی فعال نشده.",
        parse_mode="HTML",
    )
