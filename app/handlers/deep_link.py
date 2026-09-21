from __future__ import annotations

import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database.session import async_session
from app.database.models import Nation, User
from app.handlers.dashboard import show_dashboard
from app.keyboards.inline import welcome_keyboard
from app.core.redis import get_redis
from app.services.nation.nation_invite_service import consume_invite_link
from app.utils.formatting import rtl_html

logger = logging.getLogger(__name__)


def extract_start_param(message: Message) -> str:
    text = (message.text or "").strip()
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


async def _handle_invite_link(
    message: Message,
    token: str,
    state: FSMContext,
) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                redis = get_redis()
                if redis is None:
                    raise ValueError("لینک دعوت در این لحظه در دسترس نیست.")
                result = await consume_invite_link(
                    session,
                    redis,
                    token,
                    message.from_user.id,
                )
                user = await session.get(User, message.from_user.id)
        await state.clear()
        await message.answer(
            rtl_html(result.message),
            parse_mode="HTML",
        )
        if user is not None:
            await show_dashboard(message, user)
    except ValueError as exc:
        await state.clear()
        await message.answer(rtl_html(str(exc)), parse_mode="HTML")
    except Exception:
        logger.exception("Deep-link invite handling failed for user=%s", message.from_user.id)
        await state.clear()
        await message.answer(
            rtl_html("⚠️ پردازش لینک دعوت انجام نشد. دوباره امتحان کن."),
            parse_mode="HTML",
        )


async def handle_invite_link(
    message: Message,
    token: str,
    state: FSMContext,
) -> None:
    await _handle_invite_link(message, token, state)


async def handle_deep_link(
    message: Message,
    param: str,
    state: FSMContext,
) -> bool:
    if param.startswith("inv_"):
        await handle_invite_link(message, param[4:], state)
        return True

    if param.startswith("nation_"):
        try:
            nation_id = int(param.split("_", 1)[1])
        except (TypeError, ValueError):
            return False

        async with async_session() as session:
            nation = await session.get(Nation, nation_id)
            if nation is None or not nation.is_active:
                await message.answer(
                    rtl_html("⚠️ این ملت دیگر فعال نیست."),
                    parse_mode="HTML",
                )
                return True

        await state.update_data(preferred_nation_id=nation_id)
        await message.answer(
            rtl_html(
                f"🌍 <b>ملت «{nation.name}» انتخاب شد.</b>\n\n"
                "برای ادامه، «شروع بازی» را بزن."
            ),
            reply_markup=welcome_keyboard(),
            parse_mode="HTML",
        )
        return True

    return False


__all__ = ["extract_start_param", "handle_deep_link", "handle_invite_link"]
