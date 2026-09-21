from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message

from app.database.models import User
from app.database.session import async_session
from app.services.dashboard_service import build_live_dashboard, render_live_dashboard
from app.keyboards.reply import main_menu_keyboard
from app.utils.formatting import imperial_datetime
from app.utils.formatting import rtl_html

logger = logging.getLogger(__name__)


async def show_dashboard(
    message: Message,
    user: User,
    *,
    replace_inline: bool = False,
    bot: Bot | None = None,
    display_user=None,
) -> None:
    if replace_inline:
        try:
            await message.delete()
        except Exception:
            logger.debug("Could not delete previous inline panel", exc_info=True)

    async with async_session() as session:
        async with session.begin():
            dashboard = await build_live_dashboard(session, user.user_id)

    text = render_live_dashboard(dashboard)
    imperial_date, imperial_time = imperial_datetime()
    text = f"{text}\n\n<i>{imperial_date} · {imperial_time}</i>"

    await message.answer(
        rtl_html(text),
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )
