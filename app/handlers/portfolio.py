from __future__ import annotations

import html
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
from app.services.portfolio_service import get_portfolio_data
from app.utils.formatting import fmt_amount, to_fa

router = Router(name="portfolio")
logger = logging.getLogger(__name__)

RLM = "\u200f"
PORTFOLIO_ERROR = "خطا در بارگذاری پورتفولیو. دوباره تلاش کن."


def portfolio_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 بروزرسانی",
                    callback_data="portfolio_refresh",
                ),
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_to_dashboard",
                ),
            ]
        ]
    )


def _format_amount(value) -> str:
    return to_fa(fmt_amount(value))


def _format_rate_change(value) -> str:
    formatted = _format_amount(value)
    if value > 0:
        formatted = f"+{formatted}"
    return f"{formatted}٪"


def build_portfolio_text(data: dict) -> str:
    lines = [
        f"📊 <b>پورتفولیو {html.escape(data['username'])}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "💎 <b>ΩXR (ذخیره جهانی)</b>",
        f"   موجودی: <code>{_format_amount(data['xr_balance'])}</code> ΩXR",
        "",
    ]

    holdings = data["holdings"]
    if holdings:
        for holding in holdings:
            star = " ⭐" if holding["is_home_nation"] else ""
            lines.extend(
                [
                    (
                        f"🏛 <b>{html.escape(holding['nation_name'])} "
                        f"({html.escape(holding['currency_code'])}){star}</b>"
                    ),
                    (
                        f"   موجودی: <code>{_format_amount(holding['amount'])}</code> "
                        f"{html.escape(holding['currency_code'])}"
                    ),
                    (
                        f"   ارزش: <code>{_format_amount(holding['value_in_xr'])}</code> "
                        "ΩXR"
                    ),
                    (
                        f"   {holding['rate_emoji']} "
                        f"{_format_rate_change(holding['rate_change_pct'])} امروز"
                    ),
                    "",
                ]
            )
    else:
        lines.append("⚠️ هنوز هیچ ارزی نداری. از بازار خرید کن.")

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━",
            (
                f"💰 <b>ارزش کل: <code>{_format_amount(data['total_xr'])}</code> "
                "ΩXR</b>"
            ),
            f"📅 {data['today_jalali']}",
        ]
    )

    return "\n".join(f"{RLM}{line}" for line in lines)


@router.message(F.text == "📊 پورتفولیو")
async def show_portfolio(message: Message) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                data = await get_portfolio_data(session, message.from_user.id)
    except Exception:
        logger.exception(
            "Failed to load portfolio for user %s",
            message.from_user.id,
        )
        await message.answer(
            PORTFOLIO_ERROR,
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        build_portfolio_text(data),
        reply_markup=portfolio_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "portfolio_refresh")
async def refresh_portfolio(callback: CallbackQuery) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                data = await get_portfolio_data(session, callback.from_user.id)
    except Exception:
        logger.exception(
            "Failed to refresh portfolio for user %s",
            callback.from_user.id,
        )
        await callback.answer(PORTFOLIO_ERROR, show_alert=True)
        return

    new_text = build_portfolio_text(data)
    if callback.message is None:
        await callback.answer()
        return

    if callback.message.text == new_text:
        await callback.answer("همین لحظه به‌روز است ✓")
        return

    try:
        await callback.message.edit_text(
            new_text,
            reply_markup=portfolio_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            await callback.answer("همین لحظه به‌روز است ✓")
            return
        logger.exception(
            "Failed to edit portfolio message for user %s",
            callback.from_user.id,
        )
        await callback.answer(PORTFOLIO_ERROR, show_alert=True)

