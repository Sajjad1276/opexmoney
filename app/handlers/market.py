from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database.models import CurrencyHolding, Nation, Transaction, TradePreview, User, UserActivity
from app.database.session import async_session
from app.keyboards.inline import buy_amount_keyboard, market_buy_keyboard, market_keyboard, sell_amount_keyboard, sell_currency_keyboard, trade_preview_keyboard
from app.services.economic_engine import get_active_members
from app.states.market import MarketStates
from app.utils.formatting import calc_trade, fmt_amount, fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa

router = Router(name="market")

async def safe_edit(call: CallbackQuery, text: str, markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        return True
    except TelegramBadRequest:
        return False


def market_text(user: User, nation: Nation, others, active: int) -> str:
    change = get_rate_change(nation)
    lines = ["💹 <b>بازار زنده OPEX</b>", f"ارز ملت تو: <code>{html.escape(nation.currency_code)}</code> → <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> {get_rate_emoji(change)} {fmt_pct(change)}", "─────────────────", "<b>💱 سایر ارزها:</b>"]
    for n in others:
        lines.append(f"🏛 <code>{html.escape(n.currency_code)}</code> <b>{fmt_rate(n.exchange_rate)} ΩXR</b> <i>({fmt_pct(get_rate_change(n))})</i>")
    for _ in range(3 - len(others)):
        lines.append("▫️ <i>ارز دیگری هنوز فعال نیست.</i>")
    lines += ["─────────────────", f"💰 <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_amount(user.balance)}</b> | <code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>", f"⏱ <i>آخرین آپدیت نرخ</i> | 👥 {to_fa(active)} فعال"]
    return "\n".join(lines)


async def render_market(message: Message, edit_call: CallbackQuery | None = None) -> None:
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user or user.home_nation_id is None:
            if edit_call: await edit_call.answer("حساب اقتصادی پیدا نشد.", show_alert=True)
            else: await message.answer("حساب اقتصادی پیدا نشد. دوباره /start بزن.", parse_mode="HTML")
            return
        nation = await session.get(Nation, user.home_nation_id)
        others = (await session.execute(select(Nation).where(Nation.is_active.is_(True), Nation.nation_id != nation.nation_id).order_by(Nation.exchange_rate.desc()).limit(3))).scalars().all()
        active = await get_active_members(session, nation.nation_id)
        text = market_text(user, nation, others, active)
    if edit_call:
        await safe_edit(edit_call, text, market_keyboard())
    else:
        await message.answer(text, reply_markup=market_keyboard(), parse_mode="HTML")


@router.message(F.text == "💹 بازار")
async def market_button(message: Message):
    await render_market(message)


@router.callback_query(F.data == "market_main")
async def market_main(call: CallbackQuery):
    try:
        await render_market(call.message, call)
        await call.answer()
    except Exception:
        await call.answer("بازار موقتاً در دسترس نیست.", show_alert=True)


@router.callback_query(F.data == "market_refresh")
async def market_refresh(call: CallbackQuery):
    try:
        async with async_session() as session:
            user = await session.get(User, call.from_user.id)
            if not user or user.home_nation_id is None:
                await call.answer("نرخ‌ها تغییری نکردن")
                return
            nation = await session.get(Nation, user.home_nation_id)
            others = (await session.execute(select(Nation).where(Nation.is_active.is_(True), Nation.nation_id != nation.nation_id).order_by(Nation.exchange_rate.desc()).limit(3))).scalars().all()
            active = await get_active_members(session, nation.nation_id)
            text = market_text(user, nation, others, active)
        try:
            await call.message.edit_text(text, reply_markup=market_keyboard(), parse_mode="HTML")
            await call.answer()
        except TelegramBadRequest as exc:
            await call.answer("نرخ‌ها تغییری نکردن" if "not modified" in str(exc).lower() else None)
    except Exception:
        await call.answer("نرخ‌ها تغییری نکردن")


@router.callback_query(F.data == "market_buy")
async def market_buy(call: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await session.get(User, call.from_user.id)
        nations = (await session.execute(select(Nation).where(Nation.is_active.is_(True)).order_by(Nation.exchange_rate.desc()).limit(3))).scalars().all()
        if not user:
            await call.answer("حساب اقتصادی پیدا نشد.", show_alert=True); return
        text = "📈 <b>خرید ارز</b>\n━━━━━━━━━━━━━━━━━━━━\nموجودی <code>ΩXR</code>: <b>" + fmt_amount(user.xr_balance) + "</b>\n\n<b>کدوم ارز میخوای بخری؟</b>"
        for n in nations: text += f"\n🏛 <code>{html.escape(n.currency_code)}</code> — {html.escape(n.name)}\n<b>{fmt_rate(n.exchange_rate)} ΩXR</b> هر واحد"
    await state.clear(); await safe_edit(call, text, market_buy_keyboard(nations)); await call.answer()


async def make_buy_preview(message: Message, state: FSMContext, nation_id: int, raw: str):
    try: spend = Decimal(raw.strip().replace("٬", "").replace(",", "").replace("٫", "."))
    except InvalidOperation:
        await message.answer("🔴 فقط عدد بنویس. مثال: ۲۵۰", parse_mode="HTML"); return
    if spend < 10:
        await message.answer("🔴 حداقل معامله ۱۰ ΩXR است.", parse_mode="HTML"); return
    async with async_session() as session:
        user = await session.get(User, message.from_user.id); nation = await session.get(Nation, nation_id)
        if not user or not nation: return
        if spend > user.xr_balance:
            await message.answer(f"🔴 موجودی کافی نیست.\nموجودی: {fmt_amount(user.xr_balance)} ΩXR\nوارد کردی: {fmt_amount(spend)} ΩXR", parse_mode="HTML"); return
        calc = calc_trade(spend, nation.exchange_rate, True)
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user.user_id, CurrencyHolding.nation_id == nation_id))
        current = holding.amount if holding else Decimal("0")
        session.add(TradePreview(user_id=user.user_id, nation_id=nation_id, side="buy", spend=spend, preview_rate=nation.exchange_rate)); await session.commit()
        text = f"📈 <b>تأیید خرید</b>\n━━━━━━━━━━━━━━━━━━━━\n📤 پرداخت: <b>{fmt_amount(spend)} ΩXR</b>\n📥 دریافت: <b>{fmt_amount(calc['receive'])} {html.escape(nation.currency_code)}</b>\nنرخ: <code>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\nکارمزد: <b>{fmt_amount(calc['fee'])} ΩXR</b> (۰٫۵٪)\n<b>بعد معامله:</b> ΩXR <b>{fmt_amount(user.xr_balance-spend)}</b> | {html.escape(nation.currency_code)} <b>{fmt_amount(current+calc['receive'])}</b>"
    await state.clear(); await message.answer(text, reply_markup=trade_preview_keyboard(nation_id, spend, "buy"), parse_mode="HTML")


@router.callback_query(F.data.startswith("buy_"))
async def buy_currency(call: CallbackQuery, state: FSMContext):
    nation_id = int(call.data.rsplit("_", 1)[1])
    async with async_session() as session:
        nation = await session.get(Nation, nation_id); user = await session.get(User, call.from_user.id)
        if not nation or not user: await call.answer("ارز پیدا نشد.", show_alert=True); return
        text = f"📈 <b>خرید <code>{html.escape(nation.currency_code)}</code></b>\n━━━━━━━━━━━━━━━━━━━━\nنرخ فعلی: <code>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\nموجودی <code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>\n\n<b>چقدر ΩXR خرج کنی؟</b>\n<i>حداقل ۱۰ ΩXR</i>"
    await state.set_state(MarketStates.WAITING_BUY_AMOUNT); await state.update_data(nation_id=nation_id); await safe_edit(call, text, buy_amount_keyboard(nation_id)); await call.answer()


@router.callback_query(F.data.startswith("buyq_"))
async def buy_quick(call: CallbackQuery, state: FSMContext):
    _, amount, nation_id = call.data.split("_", 2)
    async with async_session() as session: user = await session.get(User, call.from_user.id)
    spend = user.xr_balance if amount == "all" and user else Decimal(amount)
    await make_buy_preview(call.message, state, int(nation_id), str(spend)); await call.answer()


@router.message(MarketStates.WAITING_BUY_AMOUNT)
async def buy_amount_message(message: Message, state: FSMContext):
    data = await state.get_data(); await make_buy_preview(message, state, int(data["nation_id"]), message.text or "")


@router.callback_query(F.data.startswith("cbuy_"))
async def confirm_buy(call: CallbackQuery):
    _, nation_id_raw, spend_raw = call.data.split("_", 2); nation_id = int(nation_id_raw); spend = Decimal(spend_raw)
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.user_id == call.from_user.id).with_for_update())).scalar_one_or_none()
        nation = await session.get(Nation, nation_id, with_for_update=True)
        preview = await session.scalar(select(TradePreview).where(TradePreview.user_id == call.from_user.id, TradePreview.nation_id == nation_id, TradePreview.side == "buy", TradePreview.spend == spend).order_by(TradePreview.created_at.desc()).limit(1)) if user else None
        if not user or not nation or not preview: await call.answer("پیش‌نمایش معامله منقضی شده.", show_alert=True); return
        if abs(nation.exchange_rate-preview.preview_rate)/preview.preview_rate > Decimal("0.01"): await call.answer("نرخ تغییر کرد، دوباره تأیید کن.", show_alert=True); return
        if user.xr_balance < spend: await call.answer("موجودی کافی نیست.", show_alert=True); return
        calc = calc_trade(spend, nation.exchange_rate, True)
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user.user_id, CurrencyHolding.nation_id == nation_id).with_for_update())
        if holding is None: holding = CurrencyHolding(user_id=user.user_id, nation_id=nation_id, amount=Decimal("0")); session.add(holding); await session.flush()
        user.xr_balance -= spend; holding.amount += calc["receive"]; nation.trade_volume_24h += spend
        session.add(Transaction(user_id=user.user_id, nation_id=nation_id, transaction_type="buy", spend_xr=spend, amount=calc["receive"], fee_xr=calc["fee"], rate=nation.exchange_rate))
        session.add(UserActivity(user_id=user.user_id, nation_id=nation_id, activity_type="trade")); await session.delete(preview); await session.commit()
        text = f"✅ <b>خرید انجام شد!</b>\n━━━━━━━━━━━━━━━━━━━━\n📤 پرداختی: <s>{fmt_amount(spend)} ΩXR</s>\n📥 دریافتی: <b>{fmt_amount(calc['receive'])} {html.escape(nation.currency_code)}</b>\n💰 ΩXR: <b>{fmt_amount(user.xr_balance)}</b> | {html.escape(nation.currency_code)}: <b>{fmt_amount(holding.amount)}</b>\n<i>این معامله روی نرخ {html.escape(nation.currency_code)} اثر گذاشت.</i>"
    await safe_edit(call, text, market_keyboard()); await call.answer("خرید انجام شد")


@router.callback_query(F.data == "market_sell")
async def market_sell(call: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        user = await session.get(User, call.from_user.id)
        rows = (await session.execute(select(CurrencyHolding, Nation.currency_code).join(Nation, Nation.nation_id == CurrencyHolding.nation_id).where(CurrencyHolding.user_id == call.from_user.id, CurrencyHolding.amount > 0).order_by(CurrencyHolding.amount.desc()))).all()
        if not user: await call.answer("حساب اقتصادی پیدا نشد.", show_alert=True); return
        if not rows:
            await safe_edit(call, "📉 <b>فروش ارز</b>\n─────────────────\nهیچ ارزی برای فروش نداری.\nاول از بازار ارز بخر.", sell_currency_keyboard([])); await call.answer(); return
        holdings = [type("Holding", (), {"currency_code": code, "amount": h.amount}) for h, code in rows]
        text = "📉 <b>فروش ارز</b>\n━━━━━━━━━━━━━━━━━━━━\n<b>کدوم ارز رو میخوای بفروشی؟</b>" + "".join(f"\n💱 <code>{html.escape(h.currency_code)}</code> — <b>{fmt_amount(h.amount)}</b>" for h in holdings)
    await state.clear(); await safe_edit(call, text, sell_currency_keyboard(holdings)); await call.answer()


@router.callback_query(F.data.startswith("sell_"))
async def sell_currency(call: CallbackQuery, state: FSMContext):
    code = call.data.removeprefix("sell_")
    async with async_session() as session:
        nation = await session.scalar(select(Nation).where(Nation.currency_code == code, Nation.is_active.is_(True))); holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == call.from_user.id, CurrencyHolding.nation_id == nation.nation_id)) if nation else None
    if not nation or not holding or holding.amount <= 0: await call.answer("این ارز موجودی نداره.", show_alert=True); return
    await state.set_state(MarketStates.WAITING_SELL_AMOUNT); await state.update_data(nation_id=nation.nation_id)
    text = f"📉 <b>فروش <code>{html.escape(nation.currency_code)}</code></b>\n━━━━━━━━━━━━━━━━━━━━\nنرخ: <code>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\nموجودی: <b>{fmt_amount(holding.amount)} {html.escape(nation.currency_code)}</b>\n\n<b>چقدر بفروشی؟</b>"
    await safe_edit(call, text, sell_amount_keyboard(nation.currency_code)); await call.answer()


async def make_sell_preview(message: Message, state: FSMContext, nation_id: int, raw: str):
    try: amount = Decimal(raw.strip().replace("٬", "").replace(",", "").replace("٫", "."))
    except InvalidOperation: await message.answer("🔴 فقط عدد بنویس.", parse_mode="HTML"); return
    if amount <= 0: await message.answer("🔴 مقدار باید بیشتر از صفر باشد.", parse_mode="HTML"); return
    async with async_session() as session:
        nation = await session.get(Nation, nation_id); user = await session.get(User, message.from_user.id); holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == message.from_user.id, CurrencyHolding.nation_id == nation_id))
        if not nation or not user or not holding or holding.amount < amount: await message.answer("🔴 موجودی کافی نیست.", parse_mode="HTML"); return
        calc = calc_trade(amount, nation.exchange_rate, False); session.add(TradePreview(user_id=user.user_id, nation_id=nation_id, side="sell", spend=amount, preview_rate=nation.exchange_rate)); await session.commit()
        text = f"📉 <b>تأیید فروش</b>\n━━━━━━━━━━━━━━━━━━━━\n📤 فروش: <b>{fmt_amount(amount)} {html.escape(nation.currency_code)}</b>\n📥 دریافت: <b>{fmt_amount(calc['receive'])} ΩXR</b>\nنرخ: <code>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\nکارمزد: <b>{fmt_amount(calc['fee'])} ΩXR</b> (۰٫۵٪)"
    await state.clear(); await message.answer(text, reply_markup=trade_preview_keyboard(nation_id, amount, "sell"), parse_mode="HTML")


@router.callback_query(F.data.startswith("sellq_"))
async def sell_quick(call: CallbackQuery, state: FSMContext):
    _, pct, code = call.data.split("_", 2)
    async with async_session() as session:
        nation = await session.scalar(select(Nation).where(Nation.currency_code == code, Nation.is_active.is_(True))); holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == call.from_user.id, CurrencyHolding.nation_id == nation.nation_id)) if nation else None
    if not nation or not holding: await call.answer("موجودی این ارز پیدا نشد.", show_alert=True); return
    await make_sell_preview(call.message, state, nation.nation_id, str(holding.amount*Decimal(pct)/100)); await call.answer()


@router.message(MarketStates.WAITING_SELL_AMOUNT)
async def sell_amount_message(message: Message, state: FSMContext):
    data = await state.get_data(); await make_sell_preview(message, state, int(data["nation_id"]), message.text or "")


@router.callback_query(F.data.startswith("csell_"))
async def confirm_sell(call: CallbackQuery):
    _, nation_id_raw, amount_raw = call.data.split("_", 2); nation_id = int(nation_id_raw); amount = Decimal(amount_raw)
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.user_id == call.from_user.id).with_for_update())).scalar_one_or_none(); nation = await session.get(Nation, nation_id, with_for_update=True)
        preview = await session.scalar(select(TradePreview).where(TradePreview.user_id == call.from_user.id, TradePreview.nation_id == nation_id, TradePreview.side == "sell", TradePreview.spend == amount).order_by(TradePreview.created_at.desc()).limit(1)) if user else None
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == call.from_user.id, CurrencyHolding.nation_id == nation_id).with_for_update()) if user else None
        if not user or not nation or not preview or not holding: await call.answer("پیش‌نمایش معامله منقضی شده.", show_alert=True); return
        if abs(nation.exchange_rate-preview.preview_rate)/preview.preview_rate > Decimal("0.01"): await call.answer("نرخ تغییر کرد، دوباره تأیید کن.", show_alert=True); return
        if holding.amount < amount: await call.answer("موجودی کافی نیست.", show_alert=True); return
        calc = calc_trade(amount, nation.exchange_rate, False); holding.amount -= amount; user.xr_balance += calc["receive"]; nation.trade_volume_24h += amount*nation.exchange_rate
        session.add(Transaction(user_id=user.user_id, nation_id=nation_id, transaction_type="sell", spend_xr=amount*nation.exchange_rate, amount=amount, fee_xr=calc["fee"], rate=nation.exchange_rate)); session.add(UserActivity(user_id=user.user_id, nation_id=nation_id, activity_type="trade")); await session.delete(preview); await session.commit()
        text = f"✅ <b>فروش انجام شد!</b>\n━━━━━━━━━━━━━━━━━━━━\n📤 فروختی: <b>{fmt_amount(amount)} {html.escape(nation.currency_code)}</b>\n📥 دریافتی: <b>{fmt_amount(calc['receive'])} ΩXR</b>\n💰 موجودی ΩXR: <b>{fmt_amount(user.xr_balance)}</b>\n<i>این معامله روی نرخ {html.escape(nation.currency_code)} اثر گذاشت.</i>"
    await safe_edit(call, text, market_keyboard()); await call.answer("فروش انجام شد")


@router.callback_query(F.data == "market_chart")
async def market_chart(call: CallbackQuery):
    await safe_edit(call, "📊 <b>نمودار نرخ</b>\n━━━━━━━━━━━━━━━━━━━━\nنمودار تصویری نرخ در مرحله بعدی اضافه میشه.", market_keyboard()); await call.answer()


@router.callback_query(F.data == "market_history")
async def market_history(call: CallbackQuery):
    async with async_session() as session:
        rows = (await session.execute(select(Transaction, Nation.currency_code).join(Nation, Nation.nation_id == Transaction.nation_id).where(Transaction.user_id == call.from_user.id).order_by(Transaction.created_at.desc()).limit(5))).all()
    lines = ["📜 <b>تاریخچه معاملات</b>", "━━━━━━━━━━━━━━━━━━━━"]
    if not rows: lines.append("هنوز معامله‌ای نداری.")
    for tx, code in rows: lines.append(f"{'📈' if tx.transaction_type == 'buy' else '📉'} <code>{html.escape(code)}</code> · {fmt_amount(tx.amount)} · {fmt_rate(tx.rate)} ΩXR")
    await safe_edit(call, "\n".join(lines), market_keyboard()); await call.answer()
