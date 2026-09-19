from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

from app.database.session import async_session
from app.services.chart_service import (
    VISIBLE_WINDOWS,
    WINDOWS,
    get_chart_data,
)
from app.utils.price_chart import generate_currency_chart


router = Router(name="chart")
logger = logging.getLogger(__name__)

CHART_ERROR = "⚠️ نمودار موقتاً در دسترس نیست. دوباره تلاش کن."


def chart_keyboard(
    nation_id: int,
    active_window: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    visible_buttons: list[InlineKeyboardButton] = []
    for key in VISIBLE_WINDOWS:
        label = str(WINDOWS[key]["label"])
        if key == active_window:
            label = f"{label} ✓"
        visible_buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"chart:{nation_id}:{key}",
            )
        )

    rows.append(visible_buttons[:2])
    rows.append(visible_buttons[2:])
    rows.append(
        [
            InlineKeyboardButton(
                text="↩️ بازگشت به بازار",
                callback_data="back_to_market",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_chart_caption(data: dict) -> str:
    currency_code = html.escape(str(data["currency_code"]))
    if not data["enough_data"]:
        return (
            f"📈 <b>نمودار {currency_code}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ داده کافی برای نمودار وجود نداره.\n"
            "حداقل ۳ رکورد تاریخچه نرخ لازمه."
        )

    window_label = html.escape(
        str(WINDOWS.get(
            data.get("window", ""),
            {"label": data.get("window_hours", 0)},
        )["label"])
    )

    return (
        f"📊 <b>{currency_code}/OPX</b> · {window_label}\n"
        "جزئیات روند و ریسک داخل تصویر نمایش داده شده."
    )


async def _build_photo(chart_data: dict) -> BufferedInputFile:
    image = await generate_currency_chart(
        currency_code=str(chart_data["currency_code"]),
        nation_name=str(chart_data["nation_name"]),
        nation_flag=str(chart_data.get("nation_flag") or ""),
        price_history=[
            float(rate)
            for rate in chart_data["rates"]
        ],
        timestamps=list(chart_data["timestamps"]),
        window=str(
            chart_data.get("window")
            or next(
                (
                    key
                    for key, value in WINDOWS.items()
                    if value["hours"] == int(chart_data["window_hours"])
                ),
                "24h",
            )
        ),
        base_currency="OPX",
        current_rate=float(chart_data["current_rate"]),
        market_status=chart_data.get("market_status"),
        change_7d=(
            float(chart_data["change_7d"])
            if chart_data.get("change_7d") is not None
            else None
        ),
        three_day_downtrend=bool(
            chart_data.get("three_day_downtrend")
        ),
    )

    return BufferedInputFile(
        image.getvalue(),
        filename="opex-market-chart.png",
    )


async def _send_chart_message(
    callback: CallbackQuery,
    chart_data: dict,
    nation_id: int,
    window: str,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    caption = build_chart_caption(chart_data)

    try:
        await callback.message.delete()
    except Exception:
        logger.debug("Could not delete market panel before chart", exc_info=True)

    if not chart_data["enough_data"]:
        await callback.bot.send_message(
            callback.from_user.id,
            caption,
            reply_markup=chart_keyboard(nation_id, window),
            parse_mode="HTML",
        )
        return

    await callback.message.answer_photo(
        photo=await _build_photo(chart_data),
        caption=caption,
        reply_markup=chart_keyboard(nation_id, window),
        parse_mode="HTML",
    )


async def _edit_chart_message(
    callback: CallbackQuery,
    chart_data: dict,
    nation_id: int,
    window: str,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    caption = build_chart_caption(chart_data)

    if not chart_data["enough_data"]:
        await callback.message.edit_text(
            caption,
            reply_markup=chart_keyboard(nation_id, window),
            parse_mode="HTML",
        )
        return

    media = InputMediaPhoto(
        media=await _build_photo(chart_data),
        caption=caption,
        parse_mode="HTML",
    )
    await callback.message.edit_media(
        media=media,
        reply_markup=chart_keyboard(nation_id, window),
    )


async def show_chart(callback: CallbackQuery) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 2:
            await callback.answer(
                "⚠️ اطلاعات نمودار نامعتبر است.",
                show_alert=True,
            )
            return

        nation_id = int(parts[1])
        window = "24h"

        async with async_session() as session:
            async with session.begin():
                chart_data = await get_chart_data(
                    session,
                    nation_id,
                    window,
                )
                chart_data["window"] = window

        await _send_chart_message(
            callback,
            chart_data,
            nation_id,
            window,
        )
        await callback.answer()
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Invalid chart callback data: %r (%s)",
            callback.data,
            exc,
        )
        await callback.answer(
            "⚠️ اطلاعات نمودار نامعتبر است.",
            show_alert=True,
        )
    except Exception:
        logger.exception(
            "Failed to show chart for user %s",
            callback.from_user.id,
        )
        await callback.answer(
            CHART_ERROR,
            show_alert=True,
        )


async def switch_chart_window(callback: CallbackQuery) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 3:
            await callback.answer(
                "⚠️ اطلاعات نمودار نامعتبر است.",
                show_alert=True,
            )
            return

        nation_id = int(parts[1])
        window = parts[2]

        if window not in WINDOWS:
            await callback.answer(
                "⚠️ بازه نمودار نامعتبر است.",
                show_alert=True,
            )
            return

        async with async_session() as session:
            async with session.begin():
                chart_data = await get_chart_data(
                    session,
                    nation_id,
                    window,
                )
                chart_data["window"] = window

        try:
            await _edit_chart_message(
                callback,
                chart_data,
                nation_id,
                window,
            )
            await callback.answer()
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                await callback.answer()
                return
            raise
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Invalid chart window callback: %r (%s)",
            callback.data,
            exc,
        )
        await callback.answer(
            "⚠️ اطلاعات نمودار نامعتبر است.",
            show_alert=True,
        )
    except Exception:
        logger.exception(
            "Failed to switch chart window for user %s",
            callback.from_user.id,
        )
        await callback.answer(
            CHART_ERROR,
            show_alert=True,
        )


async def back_to_market(callback: CallbackQuery) -> None:
    try:
        if callback.message is not None:
            await callback.message.delete()
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to delete chart message for user %s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ بازگشت به بازار انجام نشد.",
            show_alert=True,
        )


router.callback_query.register(
    show_chart,
    F.data.startswith("market_chart:"),
)
router.callback_query.register(
    switch_chart_window,
    F.data.startswith("chart:"),
)
router.callback_query.register(
    back_to_market,
    F.data == "back_to_market",
)
