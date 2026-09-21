from __future__ import annotations

import html
import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.keyboards.inline import (
    buy_amount_keyboard,
    market_buy_keyboard,
    market_keyboard,
    listed_currencies_keyboard,
    sell_amount_keyboard,
    sell_currency_keyboard,
    trade_preview_keyboard,
)
from app.services.alert_service import parse_direction
from app.services.market.market_service import (
    create_user_price_alert,
    delete_user_price_alert,
    get_active_currency,
    get_buy_currency_data,
    get_buy_options,
    get_chart_data_for_nation,
    get_holding_amount,
    get_listed_currencies_page_data,
    get_market_history,
    get_market_page_data,
    get_sell_currency_data,
    get_sell_market_data,
    get_user_xr_balance,
    list_user_price_alerts,
)
from app.services.market.trade_service import (
    execute_buy_for_user,
    execute_sell_for_user,
    parse_trade_amount,
    prepare_buy_preview_for_user,
    prepare_sell_preview_for_user,
)
from app.states.market import MarketStates
from app.utils.formatting import (
    format_alert_created,
    format_alert_currency_prompt,
    format_alert_list,
    format_alert_target_prompt,
    format_buy_completed,
    format_buy_currency,
    format_buy_market,
    format_buy_preview,
    format_chart_currency_prompt,
    format_listed_currencies,
    format_market_history,
    format_market_page,
    format_sell_completed,
    format_sell_currency,
    format_sell_market,
    format_sell_preview,
)
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="market")
logger = logging.getLogger(__name__)


def market_keyboard_for_nation(nation_id: int):
    return market_keyboard()


async def safe_edit(call: CallbackQuery, text: str, markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )
        return True
    except TelegramBadRequest:
        return False


def _cancel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="market_main")]
        ]
    )


async def render_market(
    message: Message,
    edit_call: CallbackQuery | None = None,
    *,
    request_user_id: int | None = None,
):
    user_id = (
        edit_call.from_user.id
        if edit_call is not None
        else request_user_id if request_user_id is not None else message.from_user.id
    )
    data = await get_market_page_data(user_id)
    if data.user is None or data.user.home_nation_id is None:
        text = "🔴 حساب پیدا نشد. /start بزن."
        if edit_call:
            await edit_call.answer(text, show_alert=True)
        else:
            await message.answer(text, parse_mode="HTML")
        return

    text = format_market_page(
        data.user,
        data.overview,
        data.active_members,
        data.trade_count,
    )
    markup = market_keyboard()
    if edit_call:
        await safe_edit(edit_call, text, markup)
    else:
        await send_submenu_panel(
            message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


@router.message(F.text == "💹 بازار")
async def market_button(message: Message):
    await render_market(message)


@router.callback_query(F.data == "market_main")
async def market_main(call: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await render_market(call.message, call)
        await call.answer()
    except Exception:
        await call.answer(
            "⚠️ بازار موقتاً در دسترس نیست.\nکمی صبر کن و دوباره امتحان کن.",
            show_alert=True,
        )


@router.callback_query(F.data == "market_refresh")
async def market_refresh(call: CallbackQuery):
    try:
        await render_market(call.message, call)
        await call.answer("✅ بازار بروزرسانی شد")
    except Exception:
        logger.exception(
            "Market refresh failed for user=%s",
            call.from_user.id,
        )
        await call.answer("⚠️ بازار موقتاً در دسترس نیست.", show_alert=True)


async def render_listed_currencies(call: CallbackQuery, page: int = 0) -> None:
    listed = await get_listed_currencies_page_data(page)
    await safe_edit(
        call,
        format_listed_currencies(listed),
        listed_currencies_keyboard(listed.page, listed.total_pages),
    )


@router.callback_query(F.data == "market_listed_currencies")
async def market_listed_currencies_callback(
    call: CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    try:
        await render_listed_currencies(call, 0)
        await call.answer()
    except Exception:
        logger.exception(
            "Could not render listed currencies user=%s",
            call.from_user.id,
        )
        await call.answer(
            "⚠️ فهرست ارزها موقتاً در دسترس نیست.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("market_listed:"))
async def market_listed_currencies_page_callback(
    call: CallbackQuery,
    state: FSMContext,
):
    try:
        page = int((call.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("⚠️ صفحه نامعتبر است.", show_alert=True)
        return

    await state.clear()
    try:
        await render_listed_currencies(call, page)
        await call.answer()
    except Exception:
        logger.exception(
            "Could not render listed currencies page=%s user=%s",
            page,
            call.from_user.id,
        )
        await call.answer(
            "⚠️ فهرست ارزها موقتاً در دسترس نیست.",
            show_alert=True,
        )


@router.callback_query(F.data == "alert_create")
async def alert_create_callback(
    call: CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    await state.set_state(MarketStates.WAITING_ALERT_CURRENCY)
    await remember_inline_panel(state, call.message)
    if call.message:
        await call.message.edit_text(
            format_alert_currency_prompt(),
            reply_markup=_cancel_markup(),
            parse_mode="HTML",
        )
    await call.answer()


@router.message(MarketStates.WAITING_ALERT_CURRENCY, F.text)
async def alert_currency_message(
    message: Message,
    state: FSMContext,
):
    nation = await get_active_currency(message.text or "")
    if nation is None:
        await message.answer(
            "⚠️ این ارز در بازار فعال نیست. کد ارز را دوباره وارد کن.",
            parse_mode="HTML",
        )
        return

    await state.set_state(MarketStates.WAITING_ALERT_PRICE)
    await state.update_data(alert_currency=nation.currency_code)
    await message.answer(
        format_alert_target_prompt(
            nation.currency_code,
            nation.exchange_rate,
        ),
        reply_markup=_cancel_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "market_chart_select")
async def market_chart_select_callback(
    call: CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    await state.set_state(MarketStates.WAITING_CHART_CURRENCY)
    await remember_inline_panel(state, call.message)
    if call.message:
        await call.message.edit_text(
            format_chart_currency_prompt(),
            reply_markup=_cancel_markup(),
            parse_mode="HTML",
        )
    await call.answer()


@router.message(MarketStates.WAITING_CHART_CURRENCY, F.text)
async def chart_currency_message(
    message: Message,
    state: FSMContext,
):
    nation = await get_active_currency(message.text or "")
    if nation is None:
        await message.answer(
            "⚠️ این ارز در بازار فعال نیست. کد ارز را دوباره وارد کن.",
            parse_mode="HTML",
        )
        return

    await close_inline_panel(state, message.bot)
    await state.clear()

    await message.answer(
        f"📊 <b>{html.escape(nation.currency_code)}</b> نمودار در حال آماده‌سازی است...",
        parse_mode="HTML",
    )
    try:
        await message.bot.delete_message(
            message.chat.id,
            message.message_id,
        )
    except Exception:
        pass

    from app.handlers.chart import (
        _build_photo,
        build_chart_caption,
        chart_keyboard,
    )

    chart_data = await get_chart_data_for_nation(
        nation.nation_id,
        "24h",
    )
    if not chart_data["enough_data"]:
        await message.answer(
            build_chart_caption(chart_data),
            reply_markup=chart_keyboard(nation.nation_id, "24h"),
            parse_mode="HTML",
        )
        return

    await message.answer_photo(
        photo=await _build_photo(chart_data),
        caption=build_chart_caption(chart_data),
        reply_markup=chart_keyboard(nation.nation_id, "24h"),
        parse_mode="HTML",
    )


@router.message(Command("alert"))
async def alert_command(
    message: Message,
    state: FSMContext,
):
    parts = (message.text or "").split()
    if len(parts) not in {3, 4}:
        await message.answer(
            "🔔 <b>ساخت هشدار قیمت</b>\n"
            "مثال:\n"
            "<code>/alert ARY 1.5</code>\n"
            "<code>/alert ARY 0.5 below</code>",
            parse_mode="HTML",
        )
        return

    try:
        target = Decimal(
            parts[2]
            .strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", "."),
        )
    except InvalidOperation:
        await message.answer("⚠️ قیمت هدف نامعتبره.")
        return

    direction = parse_direction(parts[3]) if len(parts) == 4 else None
    if len(parts) == 4 and direction is None:
        await message.answer(
            "⚠️ جهت باید <code>above</code> یا <code>below</code> باشه.",
            parse_mode="HTML",
        )
        return

    await state.clear()
    try:
        alert = await create_user_price_alert(
            message.from_user.id,
            parts[1],
            target,
            direction,
        )
        await message.answer(
            format_alert_created(alert, command=True),
            parse_mode="HTML",
        )
    except ValueError as exc:
        await message.answer(
            f"⚠️ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Could not create alert from command user=%s",
            message.from_user.id,
        )
        await message.answer("⚠️ ساخت هشدار انجام نشد.")


@router.callback_query(F.data.startswith("alert:set:"))
async def alert_set_callback(
    call: CallbackQuery,
    state: FSMContext,
):
    code = call.data.rsplit(":", 1)[1].strip().upper()
    nation = await get_active_currency(code)
    if nation is None:
        await call.answer(
            "⚠️ این ارز دیگر فعال نیست.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(MarketStates.WAITING_ALERT_PRICE)
    await state.update_data(alert_currency=code)
    if call.message:
        await call.message.edit_text(
            format_alert_target_prompt(code),
            reply_markup=_cancel_markup(),
            parse_mode="HTML",
        )
        await remember_inline_panel(state, call.message)
    await call.answer()


@router.message(MarketStates.WAITING_ALERT_PRICE, F.text)
async def alert_price_message(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    code = str(data.get("alert_currency") or "").upper()
    parts = (message.text or "").split()
    if not code or len(parts) not in {1, 2}:
        await state.clear()
        await message.answer(
            "⚠️ نشست هشدار نامعتبر شد. دوباره از بازار شروع کن.",
        )
        return

    try:
        target = Decimal(
            parts[0]
            .strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", "."),
        )
    except InvalidOperation:
        await message.answer(
            "⚠️ قیمت هدف نامعتبره. مثلاً 1.5 بنویس.",
        )
        return

    direction = parse_direction(parts[1]) if len(parts) == 2 else None
    if len(parts) == 2 and direction is None:
        await message.answer(
            "⚠️ جهت را above یا below بنویس.",
        )
        return

    try:
        alert = await create_user_price_alert(
            message.from_user.id,
            code,
            target,
            direction,
        )
        await close_inline_panel(state, message.bot)
        await state.clear()
        await message.answer(
            format_alert_created(alert),
            parse_mode="HTML",
        )
    except ValueError as exc:
        await message.answer(
            f"⚠️ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Could not create alert user=%s code=%s",
            message.from_user.id,
            code,
        )
        await state.clear()
        await message.answer("⚠️ ساخت هشدار انجام نشد.")


@router.callback_query(F.data == "alert:list")
async def alert_list_callback(
    call: CallbackQuery,
    *,
    acknowledge: bool = True,
):
    alerts = await list_user_price_alerts(call.from_user.id)
    text, actions = format_alert_list(alerts)
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data,
            )
        ]
        for button_text, callback_data in actions
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="↩️ بازگشت به بازار",
                callback_data="market_main",
            )
        ]
    )

    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    if acknowledge:
        await call.answer()


@router.callback_query(F.data.startswith("alert:delete:"))
async def alert_delete_callback(call: CallbackQuery):
    try:
        alert_id = int(call.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(
            "⚠️ شناسه هشدار نامعتبره.",
            show_alert=True,
        )
        return

    deleted = await delete_user_price_alert(
        call.from_user.id,
        alert_id,
    )
    await alert_list_callback(
        call,
        acknowledge=False,
    )
    await call.answer(
        "✅ هشدار حذف شد." if deleted else "⚠️ هشدار پیدا نشد.",
        show_alert=not deleted,
    )


@router.callback_query(F.data == "market_buy")
async def market_buy(
    call: CallbackQuery,
    state: FSMContext,
):
    user, nations = await get_buy_options(call.from_user.id)
    if not user:
        await call.answer(
            "🔴 حساب پیدا نشد. /start بزن.",
            show_alert=True,
        )
        return

    await state.clear()
    await safe_edit(
        call,
        format_buy_market(user, nations),
        market_buy_keyboard(nations),
    )
    await call.answer()


async def make_buy_preview(
    message: Message,
    state: FSMContext,
    nation_id: int,
    raw: str,
    *,
    user_id: int | None = None,
):
    actor_user_id = user_id or message.from_user.id
    try:
        spend = parse_trade_amount(raw)
        result = await prepare_buy_preview_for_user(
            user_id=actor_user_id,
            nation_id=nation_id,
            spend=spend,
        )
    except ValueError as exc:
        await message.answer(
            f"🔴 {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return

    await close_inline_panel(state, message.bot)
    await state.clear()
    await message.answer(
        format_buy_preview(result),
        reply_markup=trade_preview_keyboard(
            nation_id,
            spend,
            "buy",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("buy_"))
async def buy_currency(
    call: CallbackQuery,
    state: FSMContext,
):
    nation_id = int(call.data.rsplit("_", 1)[1])
    nation, user = await get_buy_currency_data(
        call.from_user.id,
        nation_id,
    )
    if not nation or not user:
        await call.answer(
            "⚠️ ارز پیدا نشد.",
            show_alert=True,
        )
        return

    await state.set_state(MarketStates.WAITING_BUY_AMOUNT)
    await state.update_data(nation_id=nation_id)
    await safe_edit(
        call,
        format_buy_currency(nation, user),
        buy_amount_keyboard(nation_id),
    )
    await remember_inline_panel(
        state,
        call.message,
    )
    await call.answer()


@router.callback_query(F.data.startswith("buyq_"))
async def buy_quick(
    call: CallbackQuery,
    state: FSMContext,
):
    _, amount, nation_id = call.data.split("_", 2)
    if amount == "all":
        balance = await get_user_xr_balance(call.from_user.id)
        if balance is None:
            await call.answer(
                "🔴 حساب پیدا نشد.",
                show_alert=True,
            )
            return
        spend = balance
    else:
        spend = Decimal(amount)

    await make_buy_preview(
        call.message,
        state,
        int(nation_id),
        str(spend),
        user_id=call.from_user.id,
    )
    await call.answer()


@router.message(MarketStates.WAITING_BUY_AMOUNT)
async def buy_amount_message(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    await make_buy_preview(
        message,
        state,
        int(data["nation_id"]),
        message.text or "",
    )


@router.callback_query(F.data.startswith("cbuy_"))
async def confirm_buy(
    call: CallbackQuery,
    state=None,
):
    _, nation_id_raw, spend_raw = call.data.split("_", 2)
    nation_id = int(nation_id_raw)
    spend = Decimal(spend_raw)

    try:
        result = await execute_buy_for_user(
            user_id=call.from_user.id,
            nation_id=nation_id,
            spend=spend,
        )
    except ValueError as exc:
        await call.answer(
            f"🔴 {html.escape(str(exc))}",
            show_alert=True,
        )
        return

    await safe_edit(
        call,
        format_buy_completed(result),
        market_keyboard_for_nation(nation_id),
    )
    await call.answer("✅ خرید انجام شد")


@router.callback_query(F.data == "market_sell")
async def market_sell(
    call: CallbackQuery,
    state: FSMContext,
):
    user, active_holdings, inactive_holdings = await get_sell_market_data(
        call.from_user.id,
    )
    if not user:
        await call.answer(
            "🔴 حساب پیدا نشد. /start بزن.",
            show_alert=True,
        )
        return

    await state.clear()
    await safe_edit(
        call,
        format_sell_market(
            active_holdings,
            inactive_holdings,
        ),
        sell_currency_keyboard(active_holdings),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sell_"))
async def sell_currency(
    call: CallbackQuery,
    state: FSMContext,
):
    code = call.data.removeprefix("sell_")
    nation, holding = await get_sell_currency_data(
        call.from_user.id,
        code,
    )
    if not nation or not holding or holding.amount <= 0:
        await call.answer(
            "🔴 موجودی این ارز صفر است.",
            show_alert=True,
        )
        return

    await state.set_state(MarketStates.WAITING_SELL_AMOUNT)
    await state.update_data(nation_id=nation.nation_id)
    await remember_inline_panel(
        state,
        call.message,
    )
    await safe_edit(
        call,
        format_sell_currency(nation, holding),
        sell_amount_keyboard(nation.currency_code),
    )
    await call.answer()


async def make_sell_preview(
    message: Message,
    state: FSMContext,
    nation_id: int,
    raw: str,
    *,
    user_id: int | None = None,
):
    actor_user_id = user_id or message.from_user.id
    try:
        amount = parse_trade_amount(raw)
        result = await prepare_sell_preview_for_user(
            user_id=actor_user_id,
            nation_id=nation_id,
            amount=amount,
        )
    except ValueError as exc:
        await message.answer(
            f"🔴 {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return

    await close_inline_panel(state, message.bot)
    await state.clear()
    await message.answer(
        format_sell_preview(result),
        reply_markup=trade_preview_keyboard(
            nation_id,
            amount,
            "sell",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("sellq_"))
async def sell_quick(
    call: CallbackQuery,
    state: FSMContext,
):
    _, pct, code = call.data.split("_", 2)
    nation, amount = await get_holding_amount(
        call.from_user.id,
        code,
    )
    if not nation or amount is None:
        await call.answer(
            "⚠️ موجودی این ارز پیدا نشد.",
            show_alert=True,
        )
        return

    sell_amount = amount * Decimal(pct) / Decimal("100")
    await make_sell_preview(
        call.message,
        state,
        nation.nation_id,
        str(sell_amount),
        user_id=call.from_user.id,
    )
    await call.answer()


@router.message(MarketStates.WAITING_SELL_AMOUNT)
async def sell_amount_message(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    await make_sell_preview(
        message,
        state,
        int(data["nation_id"]),
        message.text or "",
    )


@router.callback_query(F.data.startswith("csell_"))
async def confirm_sell(
    call: CallbackQuery,
    state=None,
):
    _, nation_id_raw, amount_raw = call.data.split("_", 2)
    nation_id = int(nation_id_raw)
    amount = Decimal(amount_raw)

    try:
        result = await execute_sell_for_user(
            user_id=call.from_user.id,
            nation_id=nation_id,
            amount=amount,
        )
    except ValueError as exc:
        await call.answer(
            f"🔴 {html.escape(str(exc))}",
            show_alert=True,
        )
        return

    await safe_edit(
        call,
        format_sell_completed(result),
        market_keyboard_for_nation(nation_id),
    )
    await call.answer("✅ فروش انجام شد")


@router.callback_query(F.data == "market_history")
async def market_history(call: CallbackQuery):
    user, rows = await get_market_history(call.from_user.id)
    if not user or user.home_nation_id is None:
        await call.answer(
            "🔴 حساب یا ملت فعال پیدا نشد. /start بزن.",
            show_alert=True,
        )
        return

    await safe_edit(
        call,
        format_market_history(user, rows),
        market_keyboard_for_nation(user.home_nation_id),
    )
    await call.answer()
