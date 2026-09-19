from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database.session import async_session
from app.services.ranking_service import (
    build_nation_msg,
    build_trader_msg,
    build_wealth_msg,
    get_nation_ranking,
    get_trader_ranking,
    get_wealth_ranking,
)

router = Router(name="ranking")
logger = logging.getLogger(__name__)

RANKING_ERROR = "خطا در بارگذاری رتبه‌بندی"


def ranking_keyboard(active_tab: str) -> InlineKeyboardMarkup:
    labels = {
        "nations": "🏛 ملت‌ها",
        "rich": "💰 ثروتمندان",
        "traders": "📈 معامله‌گران",
    }
    tabs = ("nations", "rich", "traders")

    first_row = []
    for tab in tabs:
        label = labels[tab]
        if tab == active_tab:
            label = f"· {label} ·"
        first_row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"ranking:{tab}",
            )
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            first_row,
            [
                InlineKeyboardButton(
                    text="🔄 بروزرسانی",
                    callback_data=f"ranking:refresh:{active_tab}",
                ),
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_to_dashboard",
                ),
            ],
        ]
    )


async def _load_ranking(
    active_tab: str,
    user_id: int,
    redis,
) -> str:
    async with async_session() as session:
        async with session.begin():
            if active_tab == "nations":
                data = await get_nation_ranking(
                    session,
                    redis,
                    user_id,
                )
                return build_nation_msg(data)

            if active_tab == "rich":
                data = await get_wealth_ranking(
                    session,
                    redis,
                    user_id,
                )
                return build_wealth_msg(data)

            data = await get_trader_ranking(
                session,
                redis,
                user_id,
            )
            return build_trader_msg(data)


async def show_ranking(message: Message, redis=None) -> None:
    try:
        text = await _load_ranking(
            "nations",
            message.from_user.id,
            redis,
        )
    except Exception:
        logger.exception(
            "Failed to load ranking for user %s",
            message.from_user.id,
        )
        await message.answer(
            RANKING_ERROR,
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        text,
        reply_markup=ranking_keyboard("nations"),
        parse_mode=ParseMode.HTML,
    )


async def switch_ranking_tab(
    callback: CallbackQuery,
    redis=None,
) -> None:
    tab = (callback.data or "").split(":", 1)[-1]
    if tab not in {"nations", "rich", "traders"}:
        await callback.answer(RANKING_ERROR, show_alert=True)
        return

    try:
        text = await _load_ranking(
            tab,
            callback.from_user.id,
            redis,
        )
    except Exception:
        logger.exception(
            "Failed to switch ranking tab for user %s: %s",
            callback.from_user.id,
            tab,
        )
        await callback.answer(RANKING_ERROR, show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=ranking_keyboard(tab),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            await callback.answer()
            return

        logger.exception(
            "Failed to edit ranking for user %s",
            callback.from_user.id,
        )
        await callback.answer(RANKING_ERROR, show_alert=True)


async def refresh_ranking(
    callback: CallbackQuery,
    redis=None,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer(RANKING_ERROR, show_alert=True)
        return

    tab = parts[-1]
    if tab not in {"nations", "rich", "traders"}:
        await callback.answer(RANKING_ERROR, show_alert=True)
        return

    try:
        if redis is not None:
            try:
                await redis.delete(
                    f"ranking:{tab}:{callback.from_user.id}"
                )
            except Exception:
                logger.warning(
                    "Ranking Redis delete failed for user %s tab %s",
                    callback.from_user.id,
                    tab,
                    exc_info=True,
                )

        text = await _load_ranking(
            tab,
            callback.from_user.id,
            redis,
        )
    except Exception:
        logger.exception(
            "Failed to refresh ranking for user %s: %s",
            callback.from_user.id,
            tab,
        )
        await callback.answer(RANKING_ERROR, show_alert=True)
        return

    if callback.message is None:
        await callback.answer("بروزرسانی شد ✓")
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=ranking_keyboard(tab),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.exception(
                "Failed to edit refreshed ranking for user %s tab %s",
                callback.from_user.id,
                tab,
            )
            await callback.answer(RANKING_ERROR, show_alert=True)
            return

    await callback.answer("بروزرسانی شد ✓")


router.message.register(show_ranking, F.text == "🏆 رتبه‌بندی")
router.callback_query.register(
    switch_ranking_tab,
    F.data.in_({"ranking:nations", "ranking:rich", "ranking:traders"}),
)
router.callback_query.register(
    refresh_ranking,
    F.data.startswith("ranking:refresh:"),
)
