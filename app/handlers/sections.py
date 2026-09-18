from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app.keyboards.reply import main_menu_keyboard
from app.services.keyboard_state import keyboard_manager

router = Router(name="sections")


async def _unavailable(message: Message, title: str, emoji: str) -> None:
    await keyboard_manager.send(
        message,
        f"{emoji} <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\nاین بخش در این نسخه فعال نیست. گزینه‌های فعال را از منوی اصلی انتخاب کن.",
        kind="reply:main",
        markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "📊 پورتفولیو")
async def portfolio(message: Message) -> None:
    await _unavailable(message, "پورتفولیو", "📊")


@router.message(F.text == "⚡ مأموریت")
async def missions(message: Message) -> None:
    await _unavailable(message, "مأموریت", "⚡")


@router.message(F.text == "🏆 رتبه‌بندی")
async def ranking(message: Message) -> None:
    await _unavailable(message, "رتبه‌بندی", "🏆")


@router.message(F.text == "⚙️ تنظیمات")
async def settings(message: Message) -> None:
    await _unavailable(message, "تنظیمات", "⚙️")
