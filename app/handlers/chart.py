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
from app.services.chart_service import WINDOWS, get_chart_data
from app.utils.formatting import fmt_amount, to_fa
from app.utils.price_chart import render_price_chart


router = Router(name="chart")
logger = logging.getLogger(__name__)

CHART_ERROR = "⚠️ نمودار موقتاً در دسترس نیست. دوباره تلاش کن."


def chart_keyboard(
    nation_id: int,
    active_window: str,
) -> InlineKeyboardMarkup:
    window_row = []
    for key in ("24h", "72h", "7d"):
        label = WINDOWS[key]["label"]
        if key == active_window:
            label = f"{label} ✓"
        window_row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"chart:{nation_id}:{key}",
            )
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            window_row,
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت به بازار",
                    callback_data="back_to_market",
                )
            ],
        ]
    )


def _format_change(change_pct) -> str:
    absolute = to_fa(fmt_amount(abs(change_pct)))
    if change_pct > 0:
        return f"+{absolute}٪"
    if change_pct < 0:
        return f"-{absolute}٪"
    return f"{absolute}٪"


def _window_label(window_hours: int) -> str:
    for value in WINDOWS.values():
        if value["hours"] == window_hours:
            return str(value["label"])
    return f"{to_fa(window_hours)} ساعت"


def build_chart_caption(data: dict) -> str:
    currency_code = html.escape(str(data["currency_code"]))

    if not data["enough_data"]:
        return (
            f"📈 <b>نمودار {currency_code}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ داده کافی برای نمودار وجود نداره.\n"
            "حداقل ۳ رکورد تاریخچه نرخ لازمه."
        )

    window_label = _window_label(int(data["window_hours"]))
    max_rate = to_fa(fmt_amount(data["max_rate"]))
    min_rate = to_fa(fmt_amount(data["min_rate"]))
    current_rate = to_fa(fmt_amount(data["current_rate"]))

    return (
        f"📈 <b>{currency_code} · {window_label}</b>\n"
        f"بیشترین <code>{max_rate}</code> ΩXR  ·  "
        f"کمترین <code>{min_rate}</code> ΩXR\n"
        f"الان <code>{current_rate}</code> ΩXR  ·  "
        f"{data['rate_emoji']} <b>{_format_change(data['change_pct'])}</b>"
    )


def _build_photo(chart_data: dict) -> BufferedInputFile:
    image = render_price_chart(
        [float(rate) for rate in chart_data["rates"]],
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

    if not chart_data["enough_data"]:
        await callback.message.answer(
            caption,
            reply_markup=chart_keyboard(nation_id, window),
            parse_mode="HTML",
        )
        return

    await callback.message.answer_photo(
        photo=_build_photo(chart_data),
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
        media=_build_photo(chart_data),
        caption=caption,
        parse_mode="HTML",
    )
    await callback.message.edit_media(
        media=media,
        reply_markup=chart_keyboard(nation_id, window),
    )


async def show_chart(callback: CallbackQuery) -> None:
    try:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 2:
            await callback.answer("⚠️ اطلاعات نمودار نامعتبر است.", show_alert=True)
            return

        nation_id = int(parts[1])
        window = "72h"

        async with async_session() as session:
            async with session.begin():
                chart_data = await get_chart_data(session, nation_id, window)

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
        await callback.answer("⚠️ اطلاعات نمودار نامعتبر است.", show_alert=True)
    except Exception:
        logger.exception(
            "Failed to show chart for user %s",
            callback.from_user.id,
        )
        await callback.answer(CHART_ERROR, show_alert=True)


async def switch_chart_window(callback: CallbackQuery) -> None:
    try:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 3:
            await callback.answer("⚠️ اطلاعات نمودار نامعتبر است.", show_alert=True)
            return

        nation_id = int(parts[1])
        window = parts[2]
        if window not in WINDOWS:
            await callback.answer("⚠️ بازه نمودار نامعتبر است.", show_alert=True)
            return

        async with async_session() as session:
            async with session.begin():
                chart_data = await get_chart_data(session, nation_id, window)

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
        await callback.answer("⚠️ اطلاعات نمودار نامعتبر است.", show_alert=True)
    except Exception:
        logger.exception(
            "Failed to switch chart window for user %s",
            callback.from_user.id,
        )
        await callback.answer(CHART_ERROR, show_alert=True)


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
