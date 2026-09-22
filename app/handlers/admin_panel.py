from __future__ import annotations

import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

router = Router(name="admin_panel")


def _admin_ids() -> list[int]:
    raw = os.getenv("ADMIN_USER_IDS", "")
    result: list[int] = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            user_id = int(value)
        except ValueError:
            continue
        if user_id > 0:
            result.append(user_id)
    return result


ADMIN_USER_IDS = _admin_ids()
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://localhost:8000").strip() or "https://localhost:8000"


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    if user.id not in ADMIN_USER_IDS:
        await message.answer("⛔️ دسترسی ندارید.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎛 باز کردن پنل فرماندهی",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )
            ]
        ]
    )

    text = (
        "🏦 <b>OPEX MONEY — پنل فرماندهی</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>ادمین:</b> {user.first_name}\n"
        f"🕐 <b>زمان:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "از دکمه زیر پنل مدیریت را باز کنید:"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


__all__ = ["router", "cmd_admin_panel"]
