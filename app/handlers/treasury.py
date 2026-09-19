from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.enums import ButtonStyle
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from app.database.models import NationMember, NationMemberRole, User
from app.database.session import async_session
from app.services.nation_service import get_user_active_nation
from app.services.treasury_service import (
    MIN_DEPOSIT,
    deposit_to_treasury,
    get_full_report,
    get_member_role,
    get_treasury,
    get_treasury_logs,
    withdraw_from_treasury,
)
from app.utils.formatting import fmt_amount, to_fa
from app.utils.ui import close_inline_panel, remember_inline_panel


logger = logging.getLogger(__name__)
router = Router(name="treasury")


class TreasuryStates(StatesGroup):
    ENTER_DEPOSIT_AMOUNT = State()
    ENTER_WITHDRAW_AMOUNT = State()
    CONFIRM_WITHDRAW = State()


ACTION_LABEL = {
    "deposit": "واریز",
    "withdraw": "برداشت",
    "war_fund": "صندوق جنگ",
    "reward": "پاداش",
}

_PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹",
    "0123456789",
)


def persian_to_int(text: str) -> Decimal:
    normalized = (
        (text or "")
        .strip()
        .translate(_PERSIAN_DIGITS)
        .replace("٬", "")
        .replace(",", "")
        .replace("٫", ".")
        .replace(" ", "")
    )
    if not normalized:
        raise InvalidOperation("empty amount")
    return Decimal(normalized)


def _amount_text(value: Decimal) -> str:
    return to_fa(fmt_amount(value))


def time_ago(dt: datetime) -> str:
    delta = datetime.utcnow() - dt
    minutes = max(0, int(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{to_fa(minutes)} دقیقه پیش"
    hours = minutes // 60
    if hours < 24:
        return f"{to_fa(hours)} ساعت پیش"
    days = hours // 24
    return f"{to_fa(days)} روز پیش"


def treasury_keyboard(
    nation_id: int,
    role: str | None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="💎 واریز به خزانه",
                callback_data=f"treasury:deposit:{nation_id}",
                style=ButtonStyle.SUCCESS,
            )
        ]
    ]

    if role == "founder":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📤 برداشت",
                    callback_data=f"treasury:withdraw:{nation_id}",
                    style=ButtonStyle.DANGER,
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="📋 گزارش کامل",
                callback_data=f"treasury:report:{nation_id}",
                style=ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                text="↩️ بازگشت",
                callback_data=f"nm:panel:{nation_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_withdraw_keyboard(
    nation_id: int,
    amount_str: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید برداشت",
                    callback_data=(
                        f"treasury:withdraw_confirm:{nation_id}:{amount_str}"
                    ),
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"treasury:cancel:{nation_id}",
                ),
            ]
        ]
    )


def cancel_keyboard(nation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"treasury:cancel:{nation_id}",
                    style=ButtonStyle.DANGER,
                )
            ]
        ]
    )


def build_treasury_msg(
    treasury: dict,
    logs: list[dict],
) -> str:
    nation_name = html.escape(treasury["nation_name"])
    currency_code = html.escape(treasury["currency_code"])

    lines = [
        f"🏦 <b>خزانه {nation_name}</b>",
        "━━━━━━━━━━━━━━━━━━",
        (
            f"💎 ذخیره ΩXR: "
            f"<code>{_amount_text(treasury['balance_xr'])}</code>"
        ),
        (
            f"💰 ذخیره {currency_code}: "
            f"<code>{_amount_text(treasury['balance_local'])}</code>"
        ),
        (
            f"📈 کل واریزی تاریخی: "
            f"<code>{_amount_text(treasury['total_deposited'])}</code>"
        ),
        "━━━━━━━━━━━━━━━━━━",
        "📋 <b>آخرین تراکنش‌ها:</b>",
    ]

    if not logs:
        lines.append(" ▸ هنوز تراکنشی ثبت نشده")
    else:
        for log in logs[:5]:
            username = log["actor_username"]
            actor = (
                f"@{html.escape(username)}"
                if username
                else "ناشناس"
            )
            action = ACTION_LABEL.get(log["action"], log["action"])
            lines.append(
                f" ▸ {actor} — {action} "
                f"{_amount_text(log['amount_xr'])} ΩXR — "
                f"{time_ago(log['created_at'])}"
            )

    return "\n".join(lines)


async def _render_treasury(
    callback: CallbackQuery,
    *,
    state: FSMContext | None = None,
    clear_state: bool = False,
) -> bool:
    try:
        data = callback.data or ""
        nation_id = int(data.split(":")[2])
        if state is not None and clear_state:
            await state.clear()

        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    callback.from_user.id,
                    nation_id,
                )
                if role is None:
                    await callback.answer(
                        "⛔ دسترسی ندارید.",
                        show_alert=True,
                    )
                    return False

                treasury = await get_treasury(session, nation_id)
                if treasury is None:
                    await callback.answer(
                        "⚠️ خزانه هنوز راه‌اندازی نشده.",
                        show_alert=True,
                    )
                    return False

                logs = await get_treasury_logs(
                    session,
                    nation_id,
                    limit=5,
                )

                text = build_treasury_msg(treasury, logs)
                markup = treasury_keyboard(nation_id, role)

        if callback.message is not None:
            await callback.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        else:
            await callback.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        await callback.answer()
        return True
    except Exception:
        logger.exception(
            "Failed to render treasury for user=%s data=%r",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(
            "⚠️ نمایش خزانه با مشکل روبه‌رو شد.",
            show_alert=True,
        )
        return False


@router.message(F.text == "🏦 خزانه")
async def open_treasury_from_main_menu(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    try:
        async with async_session() as session:
            async with session.begin():
                nation = await get_user_active_nation(
                    session,
                    message.from_user.id,
                    repair_founder_membership=True,
                )

                if nation is None:
                    await message.answer(
                        "⚠️ عضویت فعال ملت پیدا نشد.\n"
                        "از بخش «🌍 ملت‌ها» یک ملت را انتخاب کن یا دوباره /start را بزن."
                    )
                    return

                nation_id = nation.nation_id
                role = await get_member_role(
                    session,
                    message.from_user.id,
                    nation_id,
                )
                if role is None:
                    await message.answer(
                        "⚠️ عضویت ملت پیدا نشد.\n"
                        "اطلاعات عضویت با وضعیت حساب هماهنگ نبود؛ دوباره از «🌍 ملت‌ها» وارد شو."
                    )
                    return

                treasury = await get_treasury(session, nation_id)
                if treasury is None:
                    await message.answer("⚠️ خزانه هنوز راه‌اندازی نشده.")
                    return

                logs = await get_treasury_logs(session, nation_id, limit=5)
                text = build_treasury_msg(treasury, logs)
                markup = treasury_keyboard(nation_id, role)

        await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
        await message.answer(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Failed to open treasury from main menu for user=%s",
            message.from_user.id,
        )
        await message.answer("⚠️ نمایش خزانه انجام نشد. دوباره تلاش کن.")


@router.callback_query(F.data.regexp(r"^treasury:show:\d+$"))
async def show_treasury(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await _render_treasury(
            callback,
            state=state,
            clear_state=True,
        )
    except Exception:
        logger.exception(
            "Treasury show handler failed user=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ نمایش خزانه انجام نشد.",
            show_alert=True,
        )


@router.callback_query(F.data.regexp(r"^treasury:deposit:\d+$"))
async def start_deposit(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        nation_id = int((callback.data or "").split(":")[2])
        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    callback.from_user.id,
                    nation_id,
                )
                if role is None:
                    await callback.answer(
                        "⛔ عضو این ملت نیستی.",
                        show_alert=True,
                    )
                    return

                user = await session.get(User, callback.from_user.id)

                if user is None:
                    await callback.answer(
                        "⚠️ حساب کاربری پیدا نشد.",
                        show_alert=True,
                    )
                    return

                xr_balance = Decimal(str(user.xr_balance))

        await state.clear()
        await state.set_state(TreasuryStates.ENTER_DEPOSIT_AMOUNT)
        await state.update_data(nation_id=nation_id)

        if callback.message is not None:
            await callback.message.edit_text(
                "💎 <b>واریز به خزانه</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"موجودی ΩXR شما: "
                f"<code>{_amount_text(xr_balance)}</code>\n"
                f"حداقل واریز: <code>{_amount_text(MIN_DEPOSIT)}</code> ΩXR\n\n"
                "مقدار واریز را بنویسید:",
                reply_markup=cancel_keyboard(nation_id),
                parse_mode="HTML",
            )
            await remember_inline_panel(state, callback.message)
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to start treasury deposit for user=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ شروع واریز انجام نشد.",
            show_alert=True,
        )


@router.message(
    TreasuryStates.ENTER_DEPOSIT_AMOUNT,
    F.text,
)
async def receive_deposit_amount(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    nation_id = data.get("nation_id")

    if not nation_id:
        await state.clear()
        await message.answer("⚠️ نشست واریز منقضی شده. دوباره وارد خزانه شو.")
        return

    try:
        amount = persian_to_int(message.text or "")
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ مقدار نامعتبر است. عدد وارد کن.")
        return

    if amount <= 0:
        await message.answer("⚠️ مقدار نامعتبر است. عدد وارد کن.")
        return

    try:
        async with async_session() as session:
            async with session.begin():
                result = await deposit_to_treasury(
                    session,
                    message.from_user.id,
                    int(nation_id),
                    amount,
                )

                if not result["ok"]:
                    reason = result["reason"]
                    if reason == "insufficient_balance":
                        await message.answer(
                            "🔴 موجودی کافی نیست.\n"
                            f"موجودی شما: "
                            f"<code>{_amount_text(result['balance'])}</code> ΩXR",
                            parse_mode="HTML",
                        )
                        return
                    if reason == "below_minimum":
                        await message.answer(
                            "🔴 حداقل مقدار واریز "
                            f"{_amount_text(result['minimum'])} ΩXR است."
                        )
                        return
                    if reason == "invalid_amount":
                        await message.answer("⚠️ مقدار نامعتبر است.")
                        return
                    raise ValueError("deposit_failed")

                treasury = await get_treasury(
                    session,
                    int(nation_id),
                )
                logs = await get_treasury_logs(
                    session,
                    int(nation_id),
                    limit=5,
                )
                refreshed_text = build_treasury_msg(treasury, logs)

                success = (
                    "✅ <b>واریز موفق</b>\n"
                    f"{_amount_text(amount)} ΩXR به خزانه واریز شد.\n"
                    f"موجودی جدید شما: "
                    f"<code>{_amount_text(result['new_balance'])}</code> ΩXR"
                )

        await close_inline_panel(state, message.bot)
        await state.clear()
        await message.answer(success, parse_mode="HTML")

        role = None
        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    message.from_user.id,
                    int(nation_id),
                )
        if role is not None:
            await message.answer(
                refreshed_text,
                reply_markup=treasury_keyboard(int(nation_id), role),
                parse_mode="HTML",
            )
    except Exception:
        logger.exception(
            "Treasury deposit failed user=%s nation=%s",
            message.from_user.id,
            nation_id,
        )
        await state.clear()
        await message.answer(
            "⚠️ واریز انجام نشد. دوباره تلاش کن."
        )


@router.callback_query(F.data.regexp(r"^treasury:withdraw:\d+$"))
async def start_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        nation_id = int((callback.data or "").split(":")[2])
        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    callback.from_user.id,
                    nation_id,
                )
                if role != "founder":
                    await callback.answer(
                        "👑 فقط بنیان‌گذار می‌تواند برداشت کند.",
                        show_alert=True,
                    )
                    return

                treasury = await get_treasury(session, nation_id)
                if treasury is None:
                    await callback.answer(
                        "⚠️ خزانه هنوز راه‌اندازی نشده.",
                        show_alert=True,
                    )
                    return

        await state.clear()
        await state.set_state(TreasuryStates.ENTER_WITHDRAW_AMOUNT)
        await state.update_data(nation_id=nation_id)

        if callback.message is not None:
            await callback.message.edit_text(
                "📤 <b>برداشت از خزانه</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"موجودی خزانه: "
                f"<code>{_amount_text(treasury['balance_xr'])}</code> ΩXR\n\n"
                "مقدار برداشت را بنویسید:",
                reply_markup=cancel_keyboard(nation_id),
                parse_mode="HTML",
            )
            await remember_inline_panel(state, callback.message)
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to start treasury withdrawal user=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ شروع برداشت انجام نشد.",
            show_alert=True,
        )


@router.message(
    TreasuryStates.ENTER_WITHDRAW_AMOUNT,
    F.text,
)
async def receive_withdraw_amount(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    nation_id = data.get("nation_id")

    if not nation_id:
        await state.clear()
        await message.answer("⚠️ نشست برداشت منقضی شده. دوباره وارد خزانه شو.")
        return

    try:
        amount = persian_to_int(message.text or "")
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ مقدار نامعتبر است.")
        return

    if amount <= 0:
        await message.answer("⚠️ مقدار نامعتبر است.")
        return

    try:
        await state.set_state(TreasuryStates.CONFIRM_WITHDRAW)
        amount_str = format(amount, "f")
        await state.update_data(
            nation_id=int(nation_id),
            withdraw_amount=amount_str,
        )

        await close_inline_panel(state, message.bot)
        confirm_message = await message.answer(
            "⚠️ <b>تأیید برداشت</b>
n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"مقدار: <code>{_amount_text(amount)}</code> ΩXR\n"
            "این مبلغ به موجودی ΩXR شما اضافه می‌شود.\n\n"
            "مطمئنید؟",
            reply_markup=confirm_withdraw_keyboard(
                int(nation_id),
                amount_str,
            ),
            parse_mode="HTML",
        )
        await remember_inline_panel(state, confirm_message)
   except Exception:
        logger.exception(
            "Failed to prepare treasury withdrawal user=%s",
            message.from_user.id,
        )
        await state.clear()
        await message.answer("⚠️ آماده‌سازی برداشت انجام نشد.")


@router.callback_query(F.data.regexp(r"^treasury:withdraw_confirm:\d+:[0-9.]+$"))
async def confirm_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        amount = Decimal(parts[3])

        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    callback.from_user.id,
                    nation_id,
                )
                if role != "founder":
                    await callback.answer(
                        "⛔ دسترسی ندارید.",
                        show_alert=True,
                    )
                    return

                result = await withdraw_from_treasury(
                    session,
                    callback.from_user.id,
                    nation_id,
                    amount,
                )

                if not result["ok"]:
                    reason = result["reason"]
                    if reason == "permission_denied":
                        await callback.answer(
                            "⛔ دسترسی ندارید.",
                            show_alert=True,
                        )
                        return
                    if reason == "insufficient_treasury":
                        await callback.answer(
                            "🔴 موجودی خزانه کافی نیست.",
                            show_alert=True,
                        )
                        return
                    if reason == "invalid_amount":
                        await callback.answer(
                            "⚠️ مقدار برداشت نامعتبر است.",
                            show_alert=True,
                        )
                        return
                    raise ValueError("withdraw_failed")

                treasury = await get_treasury(session, nation_id)
                logs = await get_treasury_logs(
                    session,
                    nation_id,
                    limit=5,
                )
                refreshed_text = build_treasury_msg(treasury, logs)

        await close_inline_panel(state, callback.bot)
        await state.clear()

        success_text = (
            "✅ <b>برداشت موفق</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{_amount_text(amount)} ΩXR از خزانه برداشت شد."
        )
        if callback.message is not None:
            async with async_session() as session:
                async with session.begin():
                    role = await get_member_role(
                        session,
                        callback.from_user.id,
                        nation_id,
                    )
            if role is not None:
                await callback.message.edit_text(
                    refreshed_text,
                    reply_markup=treasury_keyboard(nation_id, role),
                    parse_mode="HTML",
                )

        await callback.answer("✅ برداشت انجام شد.")
    except Exception:
        logger.exception(
            "Treasury withdrawal failed user=%s data=%r",
            callback.from_user.id,
            callback.data,
        )
        await state.clear()
        await callback.answer(
            "⚠️ برداشت انجام نشد.",
            show_alert=True,
        )


@router.message(
    TreasuryStates.CONFIRM_WITHDRAW,
    F.text,
)
async def confirm_withdraw_text(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        data = await state.get_data()
        nation_id = data.get("nation_id")
        amount_str = data.get("withdraw_amount")

        if not nation_id or not amount_str:
            await state.clear()
            await message.answer("⚠️ نشست برداشت منقضی شده.")
            return

        amount = Decimal(str(amount_str))
        await message.answer(
            "⚠️ لطفاً یکی از دکمه‌ها را انتخاب کنید.",
            reply_markup=confirm_withdraw_keyboard(
                int(nation_id),
                format(amount, "f"),
            ),
        )
    except Exception:
        logger.exception(
            "Failed to re-show withdrawal confirmation user=%s",
            message.from_user.id,
        )
        await state.clear()
        await message.answer("⚠️ خطای موقت رخ داد. دوباره وارد خزانه شو.")


@router.callback_query(F.data.regexp(r"^treasury:report:\d+$"))
async def show_full_report(callback: CallbackQuery) -> None:
    try:
        nation_id = int((callback.data or "").split(":")[2])

        async with async_session() as session:
            async with session.begin():
                role = await get_member_role(
                    session,
                    callback.from_user.id,
                    nation_id,
                )
                if role is None:
                    await callback.answer(
                        "⛔ دسترسی ندارید.",
                        show_alert=True,
                    )
                    return

                treasury = await get_treasury(session, nation_id)
                if treasury is None:
                    await callback.answer(
                        "⚠️ خزانه هنوز راه‌اندازی نشده.",
                        show_alert=True,
                    )
                    return

                logs = await get_full_report(
                    session,
                    nation_id,
                    limit=50,
                )

        lines = [
            f"📋 <b>گزارش کامل خزانه "
            f"{html.escape(treasury['nation_name'])}</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]

        if not logs:
            lines.append("هیچ تراکنشی وجود ندارد.")
        else:
            for log in logs:
                username = log["actor_username"]
                actor = (
                    f"@{html.escape(username)}"
                    if username
                    else "ناشناس"
                )
                action = ACTION_LABEL.get(log["action"], log["action"])
                note = (
                    f"\n   {html.escape(log['note'])}"
                    if log["note"]
                    else ""
                )
                lines.append(
                    f"▸ {actor} — {action} "
                    f"{_amount_text(log['amount_xr'])} ΩXR"
                    f"{note}\n"
                    f"   {time_ago(log['created_at'])}"
                )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ بازگشت به خزانه",
                        callback_data=f"treasury:show:{nation_id}",
                    )
                ]
            ]
        )

        if callback.message is not None:
            await callback.message.edit_text(
                "\n".join(lines),
                reply_markup=markup,
                parse_mode="HTML",
            )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to show treasury report user=%s data=%r",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(
            "⚠️ گزارش خزانه در دسترس نیست.",
            show_alert=True,
        )


@router.callback_query(F.data.regexp(r"^treasury:cancel:\d+$"))
async def cancel_treasury(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await _render_treasury(
            callback,
            state=state,
            clear_state=True,
        )
    except Exception:
        logger.exception(
            "Treasury cancel handler failed user=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ لغو عملیات انجام نشد.",
            show_alert=True,
        )
