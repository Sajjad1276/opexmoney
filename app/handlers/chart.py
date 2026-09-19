from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.database.session import async_session
from app.services.chart_service import WINDOWS, get_chart_data
from app.utils.ascii_chart import draw_ascii_chart
from app.utils.formatting import fmt_amount, to_fa


router = Router(name="chart")
logger = logging.getLogger(__name__)

CHART_ERROR = "⚠️ نمودار موقتاً در دسترس نیست. دوباره تلاش کن."


def chart_keyboard(
    nation_id: int,
    active_window: str,
) -> InlineKeyboardMarkup:
    rows = []
    for key in ("24h", "72h", "7d"):
        label = WINDOWS[key]["label"]
        if key == active_window:
            label = f"{label} ✓"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"chart:{nation_id}:{key}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="↩️ بازگشت به بازار",
            callback_data="back_to_market",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def build_chart_msg(data: dict, ascii_art: str) -> str:
    currency_code = html.escape(str(data["currency_code"]))

    if not data["enough_data"]:
        return (
            f"\u200f📈 <b>نمودار {currency_code}</b>\n"
            "\u200f━━━━━━━━━━━━━━━━━━\n"
            "\u200f⚠️ داده کافی برای نمودار وجود نداره.\n"
            "\u200f(حداقل ۳ رکورد تاریخچه نرخ لازمه)"
        )

    window_label = _window_label(int(data["window_hours"]))
    max_rate = to_fa(fmt_amount(data["max_rate"]))
    min_rate = to_fa(fmt_amount(data["min_rate"]))
    current_rate = to_fa(fmt_amount(data["current_rate"]))

    return (
        f"\u200f📈 <b>نمودار {currency_code} — {window_label}</b>\n"
        "\u200f━━━━━━━━━━━━━━━━━━\n"
        f"<pre>{ascii_art}</pre>\n"
        "\u200f━━━━━━━━━━━━━━━━━━\n"
        "\u200f📊 <b>خلاصه:</b>\n"
        f"\u200f   بالاترین: <code>{max_rate}</code> ΩXR\n"
        f"\u200f   پایین‌ترین: <code>{min_rate}</code> ΩXR\n"
        f"\u200f   الان: <code>{current_rate}</code> ΩXR\n"
        f"\u200f   تغییر: {data['rate_emoji']} {_format_change(data['change_pct'])}"
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

        ascii_art = ""
        if chart_data["enough_data"]:
            ascii_art = draw_ascii_chart(
                [float(rate) for rate in chart_data["rates"]],
                age_label="-72h",
            )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.answer(
            build_chart_msg(chart_data, ascii_art),
            reply_markup=chart_keyboard(nation_id, window),
            parse_mode="HTML",
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
            await callback.answer("نامعتبر", show_alert=True)
            return

        async with async_session() as session:
            async with session.begin():
                chart_data = await get_chart_data(session, nation_id, window)

        ascii_art = ""
        if chart_data["enough_data"]:
            age_label = "-72h"
            if window == "24h":
                age_label = "-24h"
            elif window == "7d":
                age_label = "-7d"
            ascii_art = draw_ascii_chart(
                [float(rate) for rate in chart_data["rates"]],
                age_label=age_label,
            )

        if callback.message is None:
            await callback.answer()
            return

        try:
            await callback.message.edit_text(
                build_chart_msg(chart_data, ascii_art),
                reply_markup=chart_keyboard(nation_id, window),
                parse_mode="HTML",
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
        await callback.answer("⚠️ بازگشت به بازار انجام نشد.", show_alert=True)


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
