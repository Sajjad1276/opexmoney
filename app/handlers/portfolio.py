from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database.models import User
from app.database.session import async_session
from app.services.portfolio_service import get_portfolio_data
from app.utils.formatting import fmt_amount, to_fa
from app.services.portfolio_live import register_portfolio_panel, unregister_portfolio_panel
from app.utils.ui import send_submenu_panel

router = Router(name="portfolio")
logger = logging.getLogger(__name__)

RLM = "\u200f"
PORTFOLIO_ERROR = "خطا در بارگذاری پورتفولیو. دوباره تلاش کن."


def portfolio_keyboard(data: dict) -> InlineKeyboardMarkup:
    """Build a non-navigational information grid plus the real actions."""
    xr_balance = _format_amount(data["xr_balance"])
    total_xr = _format_amount(data["total_xr"])

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"💰 ارزش کل  {total_xr} دلار",
                callback_data="portfolio_info:total",
            ),
            InlineKeyboardButton(
                text=f"💎 نقدینگی  {xr_balance} دلار",
                callback_data="portfolio_info:xr",
            ),
        ]
    ]

    for holding in data["holdings"]:
        code = html.escape(holding["currency_code"])
        amount = _format_amount(holding["amount"])
        value = _format_amount(holding["value_in_xr"])
        change = _format_rate_change(holding["rate_change_pct"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏛 {code}  {amount}",
                    callback_data=f"portfolio_info:holding:{holding['nation_id']}",
                ),
                InlineKeyboardButton(
                    text=f"{holding['rate_emoji']} {change}",
                    callback_data=f"portfolio_info:change:{holding['nation_id']}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💵 ارزش  {value} دلار",
                    callback_data=f"portfolio_info:value:{holding['nation_id']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="↩️ بازگشت",
                callback_data="portfolio_back",
                style=ButtonStyle.DANGER,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_amount(value) -> str:
    return to_fa(fmt_amount(value))


def _format_rate_change(value) -> str:
    formatted = _format_amount(value)
    if value > 0:
        formatted = f"+{formatted}"
    return f"{formatted}٪"


def build_portfolio_text(data: dict) -> str:
    """Keep the message body minimal; live metrics belong to information cards."""
    lines = [
        f"💼 <b>داشبورد دارایی {html.escape(data['username'])}</b>",
        "",
        "📡 <b>ثروتت ثابت نیست؛ بازار هر لحظه ارزشش را دوباره محاسبه می‌کند.</b>",
        "",
        f"⚡ <b>بازار زنده</b> · بروزرسانی خودکار هر {data['live_update_seconds']} ثانیه",
        f"🕐 آخرین محاسبه <code>{data['current_time']}</code>",
        f"📅 {data['today_imperial']}",
    ]
    return "\n".join(f"{RLM}{line}" for line in lines)


@router.message(F.text.in_({"📊 پورتفولیو", "💼 دارایی‌های من"}))
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

    panel = await send_submenu_panel(
        message,
        build_portfolio_text(data),
        reply_markup=portfolio_keyboard(data),
        parse_mode=ParseMode.HTML,
    )
    register_portfolio_panel(
        user_id=message.from_user.id,
        chat_id=panel.chat.id,
        message_id=panel.message_id,
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
            reply_markup=portfolio_keyboard(data),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        register_portfolio_panel(
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
        )
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            await callback.answer("همین لحظه به‌روز است ✓")
            return
        logger.exception(
            "Failed to edit portfolio message for user %s",
            callback.from_user.id,
        )
        await callback.answer(PORTFOLIO_ERROR, show_alert=True)



@router.callback_query(F.data.startswith("portfolio_info:"))
async def portfolio_info_button(callback: CallbackQuery) -> None:
    """Information cards are intentionally non-navigational."""
    await callback.answer()


@router.callback_query(F.data == "portfolio_back")
async def portfolio_back(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return

    unregister_portfolio_panel(callback.from_user.id)
    await callback.answer()
    from app.handlers.start import show_dashboard
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, callback.from_user.id)
    if user is None:
        return
    await show_dashboard(
        callback.message,
        user,
        replace_inline=True,
        bot=callback.bot,
        display_user=callback.from_user,
    )
