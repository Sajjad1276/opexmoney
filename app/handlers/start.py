from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.keyboards.inline import cancel_keyboard, first_trade_keyboard, nation_selection_keyboard, trade_confirmation_keyboard, welcome_keyboard
from app.keyboards.reply import main_menu
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.user_service import get_registration_status, get_user
from app.states.onboarding import OnboardingStates
from app.utils.formatting import fmt_amount, fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa

router = Router(name="start")
logger = logging.getLogger(__name__)


def user_mention(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "معامله‌گر")}</a>'


def current_date_fa() -> str:
    now = datetime.now()
    return to_fa(now.strftime("%Y/%m/%d"))



async def _safe_edit_text(call: CallbackQuery, text: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Message edit failed: %s", exc)
        return False


async def _safe_edit_caption(call: CallbackQuery, caption: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_caption"):
            return False
        await call.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Caption edit failed: %s", exc)
        return False


START_CAPTION = """🌐 <b>{bot_name}</b>
سلام {user_name}.

بازارهای OPEX هر روز
میلیاردها واحد ارز جابه‌جا می‌کنن.
تو کجا می‌ایستی؟"""

USERNAME_CAPTION = """💹 <b>اسم معامله‌گرت رو انتخاب کن</b>
{user_mention}، این اسم روی تابلوی معاملات OPEX نمایش داده میشه.

بنویس:
· ۳ تا ۱۵ حرف انگلیسی
· فقط حروف A-Z، بدون فاصله، عدد و @"""

def nation_list_text(user, trader_name: str, nations: list[Nation]) -> str:
    lines = [f"✅ <b>«{html.escape(trader_name)}»</b> ثبت شد.", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", f"{user_mention(user)}، حالا باید به یه ملت بپیوندی.", "", "ارز اون ملت، پول اصلی حسابت میشه.", "هر معامله‌ات مستقیم روی نرخ اون ارز اثر میذاره.", "", "<b>🌍 ملت‌های فعال:</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for index, nation in enumerate(nations, start=1):
        change = get_rate_change(nation)
        lines.extend([f"🏛 <b>{html.escape(nation.name)} · {html.escape(nation.currency_code)}</b>", f"{get_rate_emoji(change)} <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> · <i>{fmt_pct(change)} امروز</i>", f"👥 {to_fa(nation.active_members_24h)} عضو · 🏆 رتبه #{to_fa(nation.nation_rank or 0)}"])
        if index != len(nations):
            lines.append("─────────────────")
    lines.extend(["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "نرخ‌ها هر ۱۵ دقیقه آپدیت میشن."])
    return "\n".join(lines)


async def show_dashboard(message: Message, user: User) -> None:
    async with async_session() as session:
        nation = await session.get(Nation, user.home_nation_id) if user.home_nation_id else None
        if nation is None:
            await message.answer("<b>OPEX MONEY</b>\nحسابت آماده است، اما هنوز ملت اصلی نداری.", reply_markup=main_menu(), parse_mode="HTML")
            return
        rank = nation.nation_rank or await get_nation_rank(session, nation.nation_id)
        total_nations = await session.scalar(select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))) or 0
        active = nation.active_members_24h
        change = get_rate_change(nation)
        minutes = max(0, int((datetime.utcnow() - nation.last_rate_update).total_seconds() // 60)) if nation.last_rate_update else 0
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user.user_id, CurrencyHolding.nation_id == nation.nation_id))
        balance = holding.amount if holding else user.balance
        text = ("🌐 <b>OPEX MONEY</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" f"{user_mention(message.from_user)}\n\n" f"🏛 {html.escape(nation.name)} · {html.escape(user.username)}\n" f"💰 <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_amount(balance)}</b> · <code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>\n\n" f"{get_rate_emoji(change)} <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> · <i>{fmt_pct(change)} امروز</i>\n" f"🏆 رتبه #{to_fa(rank)} از {to_fa(total_nations)} · 👥 {to_fa(active)} فعال\n" f"⏱ <i>{to_fa(minutes)} دقیقه پیش</i>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await message.answer(text, reply_markup=main_menu(), parse_mode="HTML")


async def continue_registration(
    message: Message,
    state: FSMContext,
    user: User,
    missing: list[str],
) -> None:
    if "username" in missing or not (user.username or "").strip():
        await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
        await message.answer(
            USERNAME_CAPTION.format(
                user_mention=user_mention(message.from_user),
            ),
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(
        user_id=user.user_id,
        username=user.username,
    )

    if "holding" not in missing:
        await state.clear()
        await show_dashboard(message, user)
        return

    # A persisted home nation means the user is not really at the nation
    # selection step. Repair the missing holding from the existing balance.
    if user.home_nation_id is not None:
        async with async_session() as session:
            async with session.begin():
                nation = await session.get(Nation, user.home_nation_id)
                holding = await session.scalar(
                    select(CurrencyHolding).where(
                        CurrencyHolding.user_id == user.user_id,
                        CurrencyHolding.nation_id == user.home_nation_id,
                    )
                )
                if nation is not None and holding is None:
                    session.add(
                        CurrencyHolding(
                            user_id=user.user_id,
                            nation_id=user.home_nation_id,
                            amount=user.balance,
                        )
                    )
        await state.clear()
        await show_dashboard(message, user)
        return

    async with async_session() as session:
        nations = await get_active_nations(session, limit=3)

    await state.set_state(OnboardingStates.SELECT_NATION)

    if not nations:
        await message.answer(
            "🌍 <b>هنوز هیچ ملتی تأسیس نشده!</b>\n\n"
            "تو می‌تونی اولین بنیان‌گذار تاریخ باشی\n"
            "و اولین ملت OPEX MONEY رو بسازی.",
            reply_markup=nation_selection_keyboard([]),
            parse_mode="HTML",
        )
        return

    await message.answer(
        nation_list_text(message.from_user, user.username, nations),
        reply_markup=nation_selection_keyboard(nations),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()

    async with async_session() as session:
        async with session.begin():
            status = await get_registration_status(session, message.from_user.id)
            if status["status"] == "complete":
                user = status["user"]
                if user.home_nation_id is not None:
                    session.add(UserActivity(
                        user_id=user.user_id,
                        nation_id=user.home_nation_id,
                        activity_type="login",
                    ))

    if status["status"] == "complete":
        user = status["user"]
        await show_dashboard(message, user)
        return

    if status["status"] == "partial":
        await continue_registration(
            message,
            state,
            status["user"],
            status["missing"],
        )
        return

    caption = START_CAPTION.format(
        bot_name="OPEX MONEY",
        user_name=html.escape(message.from_user.first_name or "معامله‌گر"),
        current_date=current_date_fa(),
    )
    await message.answer(
        caption,
        reply_markup=welcome_keyboard(),
        parse_mode="HTML",
    )


async def _begin_registration(message: Message, state: FSMContext) -> None:
    await state.clear()

    async with async_session() as session:
        status = await get_registration_status(session, message.from_user.id)

    if status["status"] == "complete":
        await show_dashboard(message, status["user"])
        return

    if status["status"] == "partial":
        await continue_registration(
            message,
            state,
            status["user"],
            status["missing"],
        )
        return

    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await message.answer(
        USERNAME_CAPTION.format(
            user_mention=user_mention(message.from_user),
        ),
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "🎮 شروع بازی")
async def start_game_button(message: Message, state: FSMContext) -> None:
    await _begin_registration(message, state)


@router.callback_query(F.data == "start_game", StateFilter(None))
async def start_game_callback(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        await call.answer("⚠️ پیام شروع بازی پیدا نشد.", show_alert=True)
        return

    await call.answer()
    await _begin_registration(call.message, state)


async def _show_help(message: Message) -> None:
    await message.answer(
        "❓ <b>راهنمای OPEX MONEY</b>\n"
        "تو یه معامله‌گر اقتصادی هستی.\n"
        "به ملت‌ها بپیوند، ارز بخر و بفروش.\n"
        "نرخ ارز با فعالیت بازار تغییر می‌کنه.\n"
        "برای شروع، اسم معامله‌گرت رو انتخاب کن.",
        reply_markup=welcome_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "❓ راهنما")
async def start_help(message: Message) -> None:
    await _show_help(message)


@router.callback_query(F.data == "show_help", StateFilter(None))
async def start_help_callback(call: CallbackQuery) -> None:
    await call.answer()
    if call.message:
        await _show_help(call.message)


@router.callback_query(F.data.startswith("join_"), StateFilter(OnboardingStates.SELECT_NATION))
async def join_nation(call: CallbackQuery, state: FSMContext) -> None:
    try:
        nation_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, AttributeError):
        await call.answer("⚠️ این ملت معتبر نیست.", show_alert=True)
        return
    data = await state.get_data()
    trader_name = data.get("username")
    if not trader_name:
        await call.answer("⏱ فرآیند ثبت‌نام منقضی شد. /start بزن.", show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(Nation).where(
                    Nation.nation_id == nation_id,
                    Nation.is_active.is_(True),
                ).with_for_update()
            )
            nation = result.scalar_one_or_none()
            if nation is None:
                await call.answer("⚠️ این ملت دیگه در دسترس نیست.", show_alert=True)
                return

            existing = await get_user(session, call.from_user.id)
            if existing is not None:
                if existing.home_nation_id is None:
                    holding = await session.scalar(
                        select(CurrencyHolding).where(
                            CurrencyHolding.user_id == existing.user_id,
                            CurrencyHolding.nation_id == nation.nation_id,
                        )
                    )
                    if holding is None:
                        existing.home_nation_id = nation.nation_id
                        existing.balance = Decimal("500.00")
                        session.add(CurrencyHolding(
                            user_id=existing.user_id,
                            nation_id=nation.nation_id,
                            amount=Decimal("500.00"),
                        ))
                        nation.member_count += 1
                        session.add(UserActivity(
                            user_id=existing.user_id,
                            nation_id=nation.nation_id,
                            activity_type="login",
                        ))
                await state.clear()
                await call.answer()
                if call.message:
                    await show_dashboard(call.message, existing)
                return

            user = User(
                user_id=call.from_user.id,
                username=trader_name,
                home_nation_id=nation.nation_id,
                balance=Decimal("500.00"),
                xr_balance=Decimal("0.00"),
                role="player",
            )
            holding = CurrencyHolding(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                amount=Decimal("500.00"),
            )
            nation.member_count += 1
            session.add_all([
                user,
                holding,
                UserActivity(
                    user_id=user.user_id,
                    nation_id=nation.nation_id,
                    activity_type="login",
                ),
            ])
            await session.flush()
            rank = await get_nation_rank(session, nation.nation_id)
            total_nations = await session.scalar(
                select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))
            ) or 0
            initial_omx = Decimal("500") * nation.exchange_rate

    text = (
        f"🏛 <b>{html.escape(nation.name)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{user_mention(call.from_user)}، شهروند رسمی این ملت شدی.\n\n"
        f"💰 موجودی اولیه:\n<b>۵۰۰ <code>{html.escape(nation.currency_code)}</code> ≈ {fmt_amount(initial_omx)} ΩXR</b>\n\n"
        "─────────────────\n"
        f"{get_rate_emoji(get_rate_change(nation))} نرخ <code>{html.escape(nation.currency_code)}</code>: <b>{fmt_rate(nation.exchange_rate)} ΩXR</b>\n"
        f"<i>{fmt_pct(get_rate_change(nation))} نسبت به دیروز</i>\n\n"
        f"🏆 رتبه #{to_fa(rank)} از {to_fa(total_nations)}\n"
        f"👥 {to_fa(nation.active_members_24h)} عضو فعال\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"هر معامله‌ات روی نرخ <code>{html.escape(nation.currency_code)}</code> اثر میذاره."
    )
    await state.clear()
    await state.update_data(first_trade_available=True)
    await _safe_edit_text(call, text, first_trade_keyboard())
    await call.answer()


@router.callback_query(F.data == "first_trade_tutorial")
async def first_trade_tutorial(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"):
        await call.answer()
        return
    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    if not user or not nation:
        await call.answer("⚠️ اطلاعات معامله پیدا نشد.", show_alert=True)
        return
    receive_omx = Decimal("50") * nation.exchange_rate
    change = get_rate_change(nation)
    text = ("⚡ <b>اولین معامله</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" f"📤 می‌فروشی:   <b>۵۰ <code>{html.escape(nation.currency_code)}</code></b>\n" f"📥 دریافت می‌کنی: <b>{fmt_amount(receive_omx)} <code>ΩXR</code></b>\n\n─────────────────\n" f"💹 نرخ: <code>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} ΩXR</code>\n" f"{get_rate_emoji(change)} تغییر ۲۴h: <b>{fmt_pct(change)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await _safe_edit_text(call, text, trade_confirmation_keyboard())
    await call.answer()


@router.callback_query(F.data == "confirm_first_trade")
async def confirm_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("first_trade_available"):
        await call.answer()
        return

    async with async_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User).where(
                        User.user_id == call.from_user.id
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if not user or not user.home_nation_id:
                await call.answer("⚠️ حساب پیدا نشد. /start بزن.", show_alert=True)
                return
            nation = await session.get(Nation, user.home_nation_id, with_for_update=True)
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user.user_id,
                    CurrencyHolding.nation_id == user.home_nation_id,
                ).with_for_update()
            )
            if not nation or not holding:
                await call.answer("⚠️ حساب پیدا نشد. /start بزن.", show_alert=True)
                return
            if holding.amount < Decimal("50"):
                await call.answer("🔴 موجودی کافی نیست.", show_alert=True)
                return
            rate = nation.exchange_rate
            receive_omx = Decimal("50") * rate
            holding.amount -= Decimal("50")
            user.balance = holding.amount
            user.xr_balance += receive_omx
            nation.trade_volume_24h += receive_omx
            session.add(Transaction(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                transaction_type="sell",
                spend_xr=receive_omx,
                amount=Decimal("50"),
                fee_xr=Decimal("0"),
                rate=rate,
            ))
            session.add(UserActivity(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                activity_type="trade",
            ))

    text = (
        "✅ <b>معامله انجام شد.</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 فروختی:   <s>۵۰ {html.escape(nation.currency_code)}</s>\n"
        f"📥 دریافتی:  <b>{fmt_amount(receive_omx)} ΩXR</b>\n\n"
        "─────────────────\n"
        f"💰 موجودی:\n<code>{html.escape(nation.currency_code)}</code>: <b>{fmt_amount(holding.amount)}</b>\n"
        f"<code>ΩXR</code>: <b>{fmt_amount(user.xr_balance)}</b>\n\n"
        "─────────────────\n✨ <b>«اولین قدم در بازارهای OPEX» باز شد.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await state.clear()
    await _safe_edit_text(call, text)
    await call.answer()
    if call.message:
        await call.message.answer(
            "🌐 <b>منوی اصلی آماده‌ست.</b>",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "skip_first_trade")
async def skip_first_trade(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        nation = await session.get(Nation, user.home_nation_id) if user and user.home_nation_id else None
    currency_code = nation.currency_code if nation else "ارز"
    balance = fmt_amount(nation and (await _get_holding_amount(call.from_user.id, nation.nation_id)) or Decimal("500")) if nation else "۵۰۰"
    text = f"{html.escape(call.from_user.first_name or 'معامله‌گر')}، هر وقت آماده شدی\nاز 💹 بازار شروع کن.\n\n💰 موجودی: {balance} <code>{html.escape(currency_code)}</code>"
    await _safe_edit_text(call, text)
    await call.answer()
    if call.message:
        await call.message.answer("🌐 <b>منوی اصلی آماده‌ست.</b>", reply_markup=main_menu(), parse_mode="HTML")


async def _get_holding_amount(user_id: int, nation_id: int) -> Decimal:
    async with async_session() as session:
        holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user_id, CurrencyHolding.nation_id == nation_id))
        return holding.amount if holding else Decimal("500")


@router.callback_query(F.data == "cancel_start")
async def cancel_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = f"{html.escape(call.from_user.first_name or 'معامله‌گر')}، ثبت‌نام لغو شد.\n\nهر وقت خواستی، /start بزن."
    if call.message and getattr(call.message, "photo", None):
        await _safe_edit_caption(call, text)
    else:
        await _safe_edit_text(call, text)
    await call.answer()