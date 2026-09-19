from __future__ import annotations

import html
import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

from app.database.models import (
    CurrencyHolding,
    Nation,
    Transaction,
    TradePreview,
    User,
    UserActivity,
)
from app.database.session import async_session
from app.keyboards.inline import (
    buy_amount_keyboard,
    market_buy_keyboard,
    market_keyboard,
    sell_amount_keyboard,
    sell_currency_keyboard,
    trade_preview_keyboard,
)
from app.services.economic_engine import get_active_members
from app.services.market_service import get_user_sell_holdings
from app.services.alert_service import (
    create_price_alert,
    delete_price_alert,
    list_price_alerts,
    parse_direction,
)
from app.services.market_intelligence import (
    format_change_text,
    format_percent_value,
    format_volume,
    get_market_overview,
    get_risk_label,
)
from app.services.mission_service import increment_mission
from app.services.user_service import sync_user_balance
from app.services.rules.resolver import resolve
from app.services.temporal_service import get_peak_multiplier
from app.states.market import MarketStates
from app.utils.ui import close_inline_panel, remember_inline_panel
from app.utils.formatting import (
    calc_trade,
    fmt_amount,
    fmt_pct,
    fmt_rate,
    get_rate_change,
    get_rate_emoji,
    to_fa,
)

router = Router(name="market")
logger = logging.getLogger(__name__)

def market_keyboard_for_nation(nation_id: int) -> InlineKeyboardMarkup:
    keyboard = market_keyboard()
    rows = [list(row) for row in keyboard.inline_keyboard]
    rows.insert(
        1,
        [
            InlineKeyboardButton(
                text="📈 نمودار",
                callback_data=f"market_chart:{nation_id}",
            )
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def market_intelligence_keyboard(nations: list[Nation]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for nation in nations:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📈 {nation.currency_code}",
                    callback_data=f"market_chart:{nation.nation_id}",
                ),
                InlineKeyboardButton(
                    text="🔔 هشدار",
                    callback_data=f"alert:set:{nation.currency_code}",
                ),
            ]
        )

    rows.append([
        InlineKeyboardButton(
            text="🔔 هشدارهای من",
            callback_data="alert:list",
        )
    ])
    rows.extend([list(row) for row in market_keyboard().inline_keyboard])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def safe_edit(call, text, markup=None):
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


def _fmt_rule_percent(value: Decimal) -> str:
    normalized = Decimal(str(value)).normalize()
    return to_fa(f"{normalized:f}") + "٪"


async def _trade_parameters(
    session,
    *,
    user_id: int,
    nation_id: int,
) -> tuple[Decimal, Decimal]:
    fee_rate = Decimal(
        str(
            await resolve(
                session,
                "market.tx_fee",
                nation_id=nation_id,
                player_id=user_id,
            )
        )
    )
    peak_multiplier = await get_peak_multiplier(session, user_id)
    return fee_rate, peak_multiplier


def _direction_emoji(change: float) -> str:
    if change > 0:
        return "🟢"
    if change < 0:
        return "🔴"
    return "🟡"


def _risk_title(label: str) -> str:
    return label.split(" ", 1)[1] if " " in label else label


def _format_mover(code: str | None, pct: float) -> str:
    if not code:
        return "—"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "➡️"
    return f"{html.escape(code)} {arrow} {format_percent_value(pct)}"


def market_text(user, overview: dict, active: int) -> str:
    lines = [
        "💹 <b>بازار OPEX</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 ΩXR: <b>{fmt_amount(user.xr_balance)}</b> · "
        f"{html.escape(user.home_nation_id and overview['currencies'][0]['nation'].currency_code or '—')}",
        "",
        "━━━━━━━━━━━━━━━",
        overview["mood"],
        "━━━━━━━━━━━━━━━",
    ]

    winner_code, winner_pct = overview["top_mover"]["winner"]
    loser_code, loser_pct = overview["top_mover"]["loser"]
    if winner_code:
        lines.append(
            f"🏆 بهترین: {_format_mover(winner_code, winner_pct)}"
        )
    if loser_code:
        lines.append(
            f"💀 بدترین: {_format_mover(loser_code, loser_pct)}"
        )
    lines.append("")

    for item in overview["currencies"]:
        nation = item["nation"]
        code = html.escape(nation.currency_code)
        price = fmt_rate(item["current_rate"])
        change = float(item["change_24h"])
        direction = _direction_emoji(change)
        risk_title = html.escape(_risk_title(item["risk_label"]))

        lines.extend(
            [
                f"{direction} <b>{code}</b> [{risk_title}]  "
                f"<b>{price} OPX</b>",
                f"   {format_change_text(change, '24h')}",
                f"   📊 حجم ۲۴ ساعت: <b>{format_volume(item['volume_24h'])} OPX</b>",
                f"   <i>{html.escape(item['insight'])}</i>",
                "",
            ]
        )

        if sum(len(line) + 1 for line in lines) > 3300:
            lines.append("… فقط بخشی از ارزها در این صفحه نمایش داده شد.")
            break

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"⏱ بروزرسانی نرخ‌ها هر ۱۵ دقیقه · 👥 {to_fa(active)} عضو فعال ملت اصلی",
        ]
    )
    return "\n".join(lines)


async def render_market(message: Message, edit_call=None):
    async with async_session() as session:
        async with session.begin():
            user_id = edit_call.from_user.id if edit_call is not None else message.from_user.id
            user = await session.get(User, user_id)
            if not user or user.home_nation_id is None:
                text = "🔴 حساب پیدا نشد. /start بزن."
                if edit_call:
                    await edit_call.answer(text, show_alert=True)
                else:
                    await message.answer(text, parse_mode="HTML")
                return

            overview = await get_market_overview(
                session,
                user.home_nation_id,
                limit=20,
            )
            if not overview["currencies"]:
                text = "⚠️ هنوز ارز فعالی برای نمایش بازار وجود ندارد."
                nations_for_keyboard: list[Nation] = []
            else:
                nations_for_keyboard = [item["nation"] for item in overview["currencies"]]
                active = await get_active_members(session, user.home_nation_id)
                text = market_text(user, overview, active)

    markup = market_intelligence_keyboard(nations_for_keyboard)
    if edit_call:
        await safe_edit(edit_call, text, markup)
    else:
        await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
        await message.answer(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


@router.message(F.text == "💹 بازار")
async def market_button(message: Message):
    await render_market(message)


@router.callback_query(F.data == "market_main")
async def market_main(call: CallbackQuery):
    try:
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
        logger.exception("Market refresh failed for user=%s", call.from_user.id)
        await call.answer("⚠️ بازار موقتاً در دسترس نیست.", show_alert=True)


@router.message(Command("alert"))
async def alert_command(message: Message, state: FSMContext):
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

    code = parts[1].strip().upper()
    try:
        target = Decimal(
            parts[2].strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", ".")
        )
    except InvalidOperation:
        await message.answer("⚠️ قیمت هدف نامعتبره.")
        return

    direction = parse_direction(parts[3]) if len(parts) == 4 else None
    if len(parts) == 4 and direction is None:
        await message.answer("⚠️ جهت باید <code>above</code> یا <code>below</code> باشه.", parse_mode="HTML")
        return

    await state.clear()
    try:
        async with async_session() as session:
            async with session.begin():
                alert = await create_price_alert(
                    session,
                    user_id=message.from_user.id,
                    currency_code=code,
                    target_price=target,
                    direction=direction,
                )
        arrow = "▲" if alert.direction == "above" else "▼"
        await message.answer(
            f"✅ هشدار ثبت شد\n"
            f"🔔 {html.escape(alert.currency_code)} · هدف: <b>{fmt_rate(alert.target_price)} OPX</b> {arrow}",
            parse_mode="HTML",
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}", parse_mode="HTML")
    except Exception:
        logger.exception("Could not create alert from command user=%s", message.from_user.id)
        await message.answer("⚠️ ساخت هشدار انجام نشد.")


@router.callback_query(F.data.startswith("alert:set:"))
async def alert_set_callback(call: CallbackQuery, state: FSMContext):
    code = call.data.rsplit(":", 1)[1].strip().upper()
    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(
                select(Nation)
                .where(
                    Nation.currency_code == code,
                    Nation.is_active.is_(True),
                )
                .limit(1)
            )

    if nation is None:
        await call.answer("⚠️ این ارز دیگر فعال نیست.", show_alert=True)
        return

    await state.clear()
    await state.set_state(MarketStates.WAITING_ALERT_PRICE)
    await state.update_data(alert_currency=code)
    if call.message:
        await call.message.edit_text(
            f"🔔 <b>هشدار قیمت</b>\n\n"
            f"قیمت هدف برای <b>{html.escape(code)}</b> را بنویس.\n"
            "مثال: <code>1.5</code> یا <code>1.5 below</code>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="❌ انصراف",
                        callback_data="market_main",
                    )]
                ]
            ),
            parse_mode="HTML",
        )
    if call.message:
        await remember_inline_panel(state, call.message)
    await call.answer()


@router.message(MarketStates.WAITING_ALERT_PRICE, F.text)
async def alert_price_message(message: Message, state: FSMContext):
    data = await state.get_data()
    code = str(data.get("alert_currency") or "").upper()
    parts = (message.text or "").split()
    if not code or len(parts) not in {1, 2}:
        await state.clear()
        await message.answer("⚠️ نشست هشدار نامعتبر شد. دوباره از بازار شروع کن.")
        return

    try:
        target = Decimal(
            parts[0]
            .strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", ".")
        )
    except InvalidOperation:
        await message.answer("⚠️ قیمت هدف نامعتبره. مثلاً 1.5 بنویس.")
        return

    direction = parse_direction(parts[1]) if len(parts) == 2 else None
    if len(parts) == 2 and direction is None:
        await message.answer("⚠️ جهت را above یا below بنویس.")
        return

    try:
        async with async_session() as session:
            async with session.begin():
                alert = await create_price_alert(
                    session,
                    user_id=message.from_user.id,
                    currency_code=code,
                    target_price=target,
                    direction=direction,
                )
        await close_inline_panel(state, message.bot)
        await state.clear()
        arrow = "▲" if alert.direction == "above" else "▼"
        await message.answer(
            f"✅ هشدار {html.escape(code)} ثبت شد.\n"
            f"هدف: <b>{fmt_rate(alert.target_price)} OPX</b> {arrow}",
            parse_mode="HTML",
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}", parse_mode="HTML")
    except Exception:
        logger.exception("Could not create alert user=%s code=%s", message.from_user.id, code)
        await state.clear()
        await message.answer("⚠️ ساخت هشدار انجام نشد.")


@router.callback_query(F.data == "alert:list")
async def alert_list_callback(
    call: CallbackQuery,
    *,
    acknowledge: bool = True,
):
    async with async_session() as session:
        async with session.begin():
            alerts = await list_price_alerts(session, call.from_user.id)

    lines = [
        "🔔 <b>هشدارهای قیمت من</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if not alerts:
        lines.append("هنوز هشداری ثبت نکردی.")
    else:
        for alert in alerts[:20]:
            status = "✅ فعال شده" if alert.triggered else "⏳ فعال"
            arrow = "▲" if alert.direction == "above" else "▼"
            lines.append(
                f"• <b>{html.escape(alert.currency_code)}</b> · "
                f"{fmt_rate(alert.target_price)} OPX {arrow} · {status}"
            )
            rows.append([
                InlineKeyboardButton(
                    text=f"🗑 حذف {alert.currency_code} #{alert.id}",
                    callback_data=f"alert:delete:{alert.id}",
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="↩️ بازگشت به بازار",
            callback_data="market_main",
        )
    ])

    if call.message:
        await call.message.edit_text(
            "\n".join(lines),
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
        await call.answer("⚠️ شناسه هشدار نامعتبره.", show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            deleted = await delete_price_alert(
                session,
                user_id=call.from_user.id,
                alert_id=alert_id,
            )

    if call.message:
        await alert_list_callback(call, acknowledge=False)
    await call.answer(
        "✅ هشدار حذف شد." if deleted else "⚠️ هشدار پیدا نشد.",
        show_alert=not deleted,
    )


@router.callback_query(F.data == "market_buy")
async def market_buy(call: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            nations = (
                await session.execute(
                    select(Nation)
                    .where(Nation.is_active.is_(True))
                    .order_by(Nation.exchange_rate.desc())
                    .limit(3)
                )
            ).scalars().all()

        if not user:
            await call.answer("🔴 حساب پیدا نشد. /start بزن.", show_alert=True)
            return

        text = (
            "📈 <b>خرید ارز</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 ΩXR موجود: <b>{fmt_amount(user.xr_balance)}</b>\n\n"
            "<b>کدوم ارز می‌خوای بخری؟</b>"
        )
        for nation in nations:
            text += (
                f"\n🏛 <code>{html.escape(nation.currency_code)}</code> · "
                f"{html.escape(nation.name)} · "
                f"<b>{fmt_rate(nation.exchange_rate)} ΩXR</b>"
            )

    await state.clear()
    await safe_edit(call, text, market_buy_keyboard(nations))
    await call.answer()


async def make_buy_preview(
    message: Message,
    state: FSMContext,
    nation_id: int,
    raw: str,
    *,
    user_id: int | None = None,
):
    try:
        spend = Decimal(
            raw.strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", ".")
        )
    except InvalidOperation:
        await message.answer("🔴 فقط عدد بنویس — مثلاً: 250", parse_mode="HTML")
        return

    if spend < 10:
        await message.answer(
            "🔴 حداقل مقدار خرید 10 ΩXR است.",
            parse_mode="HTML",
        )
        return

    actor_user_id = user_id if user_id is not None else message.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, actor_user_id)
            nation = await session.get(Nation, nation_id)

            if not user or not nation:
                await message.answer(
                    "⚠️ اطلاعات معامله پیدا نشد.",
                    parse_mode="HTML",
                )
                return

            if spend > user.xr_balance:
                await message.answer(
                    f"🔴 موجودی کافی نیست.\n"
                    f"موجودی: {fmt_amount(user.xr_balance)} ΩXR\n"
                    f"مبلغ وارد شده: {fmt_amount(spend)} ΩXR\n\n"
                    "مبلغ کمتری وارد کن.",
                    parse_mode="HTML",
                )
                return

            fee_rate, peak_multiplier = await _trade_parameters(
                session,
                user_id=user.user_id,
                nation_id=nation_id,
            )
            calc = calc_trade(
                spend,
                nation.exchange_rate,
                True,
                fee_rate_percent=fee_rate,
                benefit_multiplier=peak_multiplier,
            )
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == nation_id,
                )
            )
            current = holding.amount if holding else Decimal("0")

            session.add(
                TradePreview(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    side="buy",
                    spend=spend,
                    preview_rate=nation.exchange_rate,
                )
            )

            peak_signal = (
                "\n📈 <i>بازار الان با تو راه میاد.</i>\n"
                if peak_multiplier > Decimal("1")
                else ""
            )
            text = (
                "📈 <b>تأیید خرید</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📤 پرداخت:   <b>{fmt_amount(spend)} ΩXR</b>\n"
                f"📥 دریافت:   <b>{fmt_amount(calc['receive'])} "
                f"{html.escape(nation.currency_code)}</b>\n\n"
                "─────────────────\n"
                f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = "
                f"{fmt_rate(nation.exchange_rate)} ΩXR</code>\n"
                f"📋 کارمزد: <b>{fmt_amount(calc['fee'])} ΩXR</b> "
                f"({_fmt_rule_percent(fee_rate)})\n"
                f"{peak_signal}\n"
                "─────────────────\n"
                "<b>موجودی بعد از معامله:</b>\n"
                f"ΩXR: <b>{fmt_amount(user.xr_balance - spend)}</b>\n"
                f"{html.escape(nation.currency_code)}: "
                f"<b>{fmt_amount(current + calc['receive'])}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )

    await close_inline_panel(state, message.bot)
    await state.clear()
    await message.answer(
        text,
        reply_markup=trade_preview_keyboard(nation_id, spend, "buy"),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("buy_"))
async def buy_currency(call, state):
    nation_id = int(call.data.rsplit("_", 1)[1])
    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)
            user = await session.get(User, call.from_user.id)

        if not nation or not user:
            await call.answer("⚠️ ارز پیدا نشد.", show_alert=True)
            return

        text = (
            f"📈 <b>خرید <code>{html.escape(nation.currency_code)}</code></b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = "
            f"{fmt_rate(nation.exchange_rate)} ΩXR</code>\n"
            f"💰 موجودی: <b>{fmt_amount(user.xr_balance)} ΩXR</b>\n\n"
            "<b>چقدر ΩXR خرج می‌کنی؟</b>\n"
            "<i>حداقل 10 ΩXR</i>"
        )

    await state.set_state(MarketStates.WAITING_BUY_AMOUNT)
    await state.update_data(nation_id=nation_id)
    await safe_edit(call, text, buy_amount_keyboard(nation_id))
    await remember_inline_panel(state, call.message)
    await call.answer()


@router.callback_query(F.data.startswith("buyq_"))
async def buy_quick(call, state):
    _, amount, nation_id = call.data.split("_", 2)
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)

    spend = user.xr_balance if amount == "all" and user else Decimal(amount)
    await make_buy_preview(
        call.message,
        state,
        int(nation_id),
        str(spend),
        user_id=call.from_user.id,
    )
    await call.answer()


@router.message(MarketStates.WAITING_BUY_AMOUNT)
async def buy_amount_message(message, state):
    data = await state.get_data()
    await make_buy_preview(
        message,
        state,
        int(data["nation_id"]),
        message.text or "",
    )


@router.callback_query(F.data.startswith("cbuy_"))
async def confirm_buy(call, state=None):
    _, nation_id_raw, spend_raw = call.data.split("_", 2)
    nation_id = int(nation_id_raw)
    spend = Decimal(spend_raw)
    text = None

    async with async_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User)
                    .where(User.user_id == call.from_user.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            nation = await session.get(Nation, nation_id, with_for_update=True)
            preview = (
                await session.scalar(
                    select(TradePreview)
                    .where(
                        TradePreview.user_id == call.from_user.id,
                        TradePreview.nation_id == nation_id,
                        TradePreview.side == "buy",
                        TradePreview.spend == spend,
                    )
                    .order_by(TradePreview.created_at.desc())
                    .limit(1)
                )
                if user
                else None
            )

            if not user or not nation or not preview:
                await call.answer(
                    "⏱ پیش‌نمایش منقضی شد. دوباره مقدار رو وارد کن.",
                    show_alert=True,
                )
                return

            if (
                abs(nation.exchange_rate - preview.preview_rate)
                / preview.preview_rate
                > Decimal("0.01")
            ):
                await call.answer(
                    "⚠️ نرخ تغییر کرد. پیش‌نمایش جدید رو تأیید کن.",
                    show_alert=True,
                )
                return

            if user.xr_balance < spend:
                await call.answer("🔴 موجودی کافی نیست.", show_alert=True)
                return

            fee_rate, peak_multiplier = await _trade_parameters(
                session,
                user_id=user.user_id,
                nation_id=nation_id,
            )
            calc = calc_trade(
                spend,
                nation.exchange_rate,
                True,
                fee_rate_percent=fee_rate,
                benefit_multiplier=peak_multiplier,
            )
            holding = await session.scalar(
                select(CurrencyHolding)
                .where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == nation_id,
                )
                .with_for_update()
            )
            if holding is None:
                holding = CurrencyHolding(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    amount=Decimal("0"),
                )
                session.add(holding)
                await session.flush()

            user.xr_balance -= spend
            holding.amount += calc["receive"]
            nation.trade_volume_24h += spend

            session.add(
                Transaction(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    transaction_type="buy",
                    spend_xr=spend,
                    amount=calc["receive"],
                    fee_xr=calc["fee"],
                    rate=nation.exchange_rate,
                )
            )
            session.add(
                UserActivity(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    activity_type="trade",
                )
            )
            await session.delete(preview)

            peak_signal = (
                "\n📈 بازار الان با تو راه میاد."
                if peak_multiplier > Decimal("1")
                else ""
            )
            text = (
                "✅ <b>خرید انجام شد.</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📤 پرداختی: <s>{fmt_amount(spend)} ΩXR</s>\n"
                f"📥 دریافتی: <b>{fmt_amount(calc['receive'])} "
                f"{html.escape(nation.currency_code)}</b>\n"
                f"{peak_signal}\n"
                "─────────────────\n"
                "💰 موجودی:\n"
                f"ΩXR: <b>{fmt_amount(user.xr_balance)}</b>\n"
                f"{html.escape(nation.currency_code)}: "
                f"<b>{fmt_amount(holding.amount)}</b>"
            )

            try:
                await increment_mission(session, user.user_id, "DAILY_TRADE_1")
                await increment_mission(session, user.user_id, "WEEKLY_BUY_5")
                await increment_mission(
                    session,
                    user.user_id,
                    "WEEKLY_TRADE_VOLUME",
                    amount=int(calc["receive"]),
                )
                await increment_mission(session, user.user_id, "FIRST_TRADE")
            except Exception:
                logger.exception(
                    "Mission trigger failed after buy for user %s",
                    user.user_id,
                )

    await safe_edit(call, text, market_keyboard_for_nation(nation_id))
    await call.answer("✅ خرید انجام شد")


@router.callback_query(F.data == "market_sell")
async def market_sell(call, state):
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            active_holdings, inactive_holdings = await get_user_sell_holdings(
                session,
                call.from_user.id,
            )

        if not user:
            await call.answer("🔴 حساب پیدا نشد. /start بزن.", show_alert=True)
            return

    if not active_holdings and not inactive_holdings:
        await safe_edit(
            call,
            "📉 <b>فروش ارز</b>\n"
            "─────────────────\n"
            "هنوز ارزی برای فروش نداری.\n\n"
            "از 📈 خرید ارز شروع کن.",
            sell_currency_keyboard([]),
        )
        await call.answer()
        return

    text_lines = [
        "📉 <b>فروش ارز</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if active_holdings:
        text_lines.append("<b>ارزهای قابل فروش:</b>")
        for holding in active_holdings:
            text_lines.append(
                f"💱 <code>{html.escape(holding.currency_code)}</code> "
                f"· موجودی: <b>{fmt_amount(holding.amount)}</b>"
            )

    if inactive_holdings:
        text_lines.extend([
            "",
            "🚫 <b>ارزهای غیرقابل فروش:</b>",
        ])
        for holding in inactive_holdings:
            text_lines.append(
                f"• <code>{html.escape(holding.currency_code)}</code> "
                f"({html.escape(holding.nation_name)}) · "
                "ملت این ارز منحل شده - قابل فروش نیست"
            )

    await state.clear()
    await safe_edit(
        call,
        "\n".join(text_lines),
        sell_currency_keyboard(active_holdings),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sell_"))
async def sell_currency(call, state):
    code = call.data.removeprefix("sell_")
    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(
                select(Nation).where(
                    Nation.currency_code == code,
                    Nation.is_active.is_(True),
                )
            )
            holding = (
                await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == call.from_user.id,
                        CurrencyHolding.nation_id == nation.nation_id,
                    )
                )
                if nation
                else None
            )

    if nation is not None and not nation.is_active:
        await call.answer("🚫 ملت این ارز منحل شده - قابل فروش نیست.", show_alert=True)
        return
    if not nation or not holding or holding.amount <= 0:
        await call.answer("🔴 موجودی این ارز صفر است.", show_alert=True)
        return

    await state.set_state(MarketStates.WAITING_SELL_AMOUNT)
    await state.update_data(nation_id=nation.nation_id)
    await remember_inline_panel(state, call.message)
    text = (
        f"📉 <b>فروش <code>{html.escape(nation.currency_code)}</code></b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = "
        f"{fmt_rate(nation.exchange_rate)} ΩXR</code>\n"
        f"💰 موجودی: <b>{fmt_amount(holding.amount)} "
        f"{html.escape(nation.currency_code)}</b>\n\n"
        "<b>چقدر می‌فروشی؟</b>"
    )
    await safe_edit(call, text, sell_amount_keyboard(nation.currency_code))
    await call.answer()


async def make_sell_preview(
    message,
    state,
    nation_id: int,
    raw: str,
    *,
    user_id: int | None = None,
):
    try:
        amount = Decimal(
            raw.strip()
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", ".")
        )
    except InvalidOperation:
        await message.answer("🔴 فقط عدد بنویس — مثلاً: 50", parse_mode="HTML")
        return

    if amount <= 0:
        await message.answer(
            "🔴 مقدار باید بیشتر از صفر باشه.",
            parse_mode="HTML",
        )
        return

    actor_user_id = user_id if user_id is not None else message.from_user.id

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)
            user = await session.get(User, actor_user_id)
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == actor_user_id,
                    CurrencyHolding.nation_id == nation_id,
                )
            )
            if not nation or not user or not holding:
                await message.answer(
                    "⚠️ موجودی این ارز پیدا نشد.",
                    parse_mode="HTML",
                )
                return
            if holding.amount < amount:
                await message.answer(
                    f"🔴 موجودی کافی نیست.\n"
                    f"موجودی: {fmt_amount(holding.amount)} "
                    f"{html.escape(nation.currency_code)}\n"
                    f"مبلغ وارد شده: {fmt_amount(amount)} "
                    f"{html.escape(nation.currency_code)}",
                    parse_mode="HTML",
                )
                return

            fee_rate, peak_multiplier = await _trade_parameters(
                session,
                user_id=user.user_id,
                nation_id=nation_id,
            )
            calc = calc_trade(
                amount,
                nation.exchange_rate,
                False,
                fee_rate_percent=fee_rate,
                benefit_multiplier=peak_multiplier,
            )
            session.add(
                TradePreview(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    side="sell",
                    spend=amount,
                    preview_rate=nation.exchange_rate,
                )
            )
            xr_after = user.xr_balance + calc["receive"]
            currency_after = holding.amount - amount
            peak_signal = (
                "\n📈 <i>بازار الان با تو راه میاد.</i>\n"
                if peak_multiplier > Decimal("1")
                else ""
            )
            text = (
                "📉 <b>تأیید فروش</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📤 فروش:     <b>{fmt_amount(amount)} "
                f"{html.escape(nation.currency_code)}</b>\n"
                f"📥 دریافت:   <b>{fmt_amount(calc['receive'])} ΩXR</b>\n\n"
                "─────────────────\n"
                f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = "
                f"{fmt_rate(nation.exchange_rate)} ΩXR</code>\n"
                f"📋 کارمزد: <b>{fmt_amount(calc['fee'])} ΩXR</b> "
                f"({_fmt_rule_percent(fee_rate)})\n"
                f"{peak_signal}\n"
                "─────────────────\n"
                "<b>موجودی بعد از معامله:</b>\n"
                f"ΩXR: <b>{fmt_amount(xr_after)}</b>\n"
                f"{html.escape(nation.currency_code)}: "
                f"<b>{fmt_amount(currency_after)}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )

    await close_inline_panel(state, message.bot)
    await state.clear()
    await message.answer(
        text,
        reply_markup=trade_preview_keyboard(nation_id, amount, "sell"),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("sellq_"))
async def sell_quick(call, state):
    _, pct, code = call.data.split("_", 2)
    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(
                select(Nation).where(
                    Nation.currency_code == code,
                    Nation.is_active.is_(True),
                )
            )
            holding = (
                await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == call.from_user.id,
                        CurrencyHolding.nation_id == nation.nation_id,
                    )
                )
                if nation
                else None
            )

    if not nation or not holding:
        await call.answer("⚠️ موجودی این ارز پیدا نشد.", show_alert=True)
        return

    await make_sell_preview(
        call.message,
        state,
        nation.nation_id,
        str(holding.amount * Decimal(pct) / Decimal("100")),
        user_id=call.from_user.id,
    )
    await call.answer()


@router.message(MarketStates.WAITING_SELL_AMOUNT)
async def sell_amount_message(message, state):
    data = await state.get_data()
    await make_sell_preview(
        message,
        state,
        int(data["nation_id"]),
        message.text or "",
    )


@router.callback_query(F.data.startswith("csell_"))
async def confirm_sell(call, state=None):
    _, nation_id_raw, amount_raw = call.data.split("_", 2)
    nation_id = int(nation_id_raw)
    amount = Decimal(amount_raw)
    text = None

    async with async_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User)
                    .where(User.user_id == call.from_user.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            nation = await session.get(
                Nation,
                nation_id,
                with_for_update=True,
            )
            preview = (
                await session.scalar(
                    select(TradePreview)
                    .where(
                        TradePreview.user_id == call.from_user.id,
                        TradePreview.nation_id == nation_id,
                        TradePreview.side == "sell",
                        TradePreview.spend == amount,
                    )
                    .order_by(TradePreview.created_at.desc())
                    .limit(1)
                )
                if user
                else None
            )
            holding = (
                await session.scalar(
                    select(CurrencyHolding)
                    .where(
                        CurrencyHolding.user_id == call.from_user.id,
                        CurrencyHolding.nation_id == nation_id,
                    )
                    .with_for_update()
                )
                if user
                else None
            )

            if not user or not nation or not preview or not holding:
                await call.answer(
                    "⏱ پیش‌نمایش منقضی شد. دوباره مقدار رو وارد کن.",
                    show_alert=True,
                )
                return

            if (
                abs(nation.exchange_rate - preview.preview_rate)
                / preview.preview_rate
                > Decimal("0.01")
            ):
                await call.answer(
                    "⚠️ نرخ تغییر کرد. پیش‌نمایش جدید رو تأیید کن.",
                    show_alert=True,
                )
                return

            if holding.amount < amount:
                await call.answer("🔴 موجودی کافی نیست.", show_alert=True)
                return

            fee_rate, peak_multiplier = await _trade_parameters(
                session,
                user_id=user.user_id,
                nation_id=nation_id,
            )
            calc = calc_trade(
                amount,
                nation.exchange_rate,
                False,
                fee_rate_percent=fee_rate,
                benefit_multiplier=peak_multiplier,
            )
            holding.amount -= amount
            user.xr_balance += calc["receive"]
            nation.trade_volume_24h += amount * nation.exchange_rate

            session.add(
                Transaction(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    transaction_type="sell",
                    spend_xr=amount * nation.exchange_rate,
                    amount=amount,
                    fee_xr=calc["fee"],
                    rate=nation.exchange_rate,
                )
            )
            session.add(
                UserActivity(
                    user_id=user.user_id,
                    nation_id=nation_id,
                    activity_type="trade",
                )
            )
            await sync_user_balance(session, user.user_id)
            await session.delete(preview)

            peak_signal = (
                "\n📈 بازار الان با تو راه میاد."
                if peak_multiplier > Decimal("1")
                else ""
            )
            text = (
                "✅ <b>فروش انجام شد.</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📤 فروختی:  <b>{fmt_amount(amount)} "
                f"{html.escape(nation.currency_code)}</b>\n"
                f"📥 دریافتی: <b>{fmt_amount(calc['receive'])} ΩXR</b>\n"
                f"{peak_signal}\n"
                "─────────────────\n"
                "💰 موجودی:\n"
                f"ΩXR: <b>{fmt_amount(user.xr_balance)}</b>\n"
                f"{html.escape(nation.currency_code)}: "
                f"<b>{fmt_amount(holding.amount)}</b>"
            )

            try:
                await increment_mission(session, user.user_id, "DAILY_TRADE_1")
                await increment_mission(
                    session,
                    user.user_id,
                    "WEEKLY_TRADE_VOLUME",
                    amount=int(amount),
                )
                await increment_mission(session, user.user_id, "FIRST_TRADE")
            except Exception:
                logger.exception(
                    "Mission trigger failed after sell for user %s",
                    user.user_id,
                )

    await safe_edit(call, text, market_keyboard_for_nation(nation_id))
    await call.answer("✅ فروش انجام شد")


@router.callback_query(F.data == "market_history")
async def market_history(call):
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            rows = (
                await session.execute(
                    select(Transaction, Nation.currency_code)
                    .join(Nation, Nation.nation_id == Transaction.nation_id)
                    .where(Transaction.user_id == call.from_user.id)
                    .order_by(Transaction.created_at.desc())
                    .limit(5)
                )
            ).all()

    if not user or user.home_nation_id is None:
        await call.answer("🔴 حساب یا ملت فعال پیدا نشد. /start بزن.", show_alert=True)
        return

    market_markup = market_keyboard_for_nation(user.home_nation_id)

    lines = [
        "📜 <b>تاریخچه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not rows:
        lines.append("هنوز معامله‌ای انجام ندادی.")

    for transaction, code in rows:
        lines.append(
            f"{'📈' if transaction.transaction_type == 'buy' else '📉'} "
            f"<code>{html.escape(code)}</code> · "
            f"{fmt_amount(transaction.amount)} · "
            f"{fmt_rate(transaction.rate)} ΩXR"
        )

    await safe_edit(call, "\n".join(lines), market_markup)
    await call.answer()
