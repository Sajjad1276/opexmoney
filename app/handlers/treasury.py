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
from app.services.nation_service import get_user_active_nation_context
from app.handlers.nation_management import nation_admin_panel
from app.handlers.start import show_dashboard
from app.keyboards.inline import nation_panel_keyboard
from app.services.user_service import is_fully_registered
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


TREASURY_RETURN_TARGETS = {"dashboard", "nations", "management"}


def treasury_keyboard(
    nation_id: int,
    role: str | None,
    *,
    return_target: str = "dashboard",
) -> InlineKeyboardMarkup:
    if return_target not in TREASURY_RETURN_TARGETS:
        return_target = "dashboard"

    rows = [
        [
            InlineKeyboardButton(
                text="💎 واریز به خزانه",
                callback_data=f"treasury:deposit:{nation_id}:{return_target}",
                style=ButtonStyle.SUCCESS,
            )
        ]
    ]

    if role == "founder":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📤 برداشت",
                    callback_data=f"treasury:withdraw:{nation_id}:{return_target}",
                    style=ButtonStyle.DANGER,
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="📋 گزارش کامل",
                callback_data=f"treasury:report:{nation_id}:{return_target}",
                style=ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                text="↩️ بازگشت",
                callback_data=f"treasury:back:{nation_id}:{return_target}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_withdraw_keyboard(
    nation_id: int,
    amount_str: str,
    return_target: str = "dashboard",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید برداشت",
                    callback_data=(
                        f"treasury:withdraw_confirm:{nation_id}:{amount_str}:{return_target}"
                    ),
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"treasury:cancel:{nation_id}:{return_target}",
                ),
            ]
        ]
    )


def cancel_keyboard(
    nation_id: int,
    return_target: str = "dashboard",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=f"treasury:cancel:{nation_id}:{return_target}",
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
            f"💎 ذخیره دلار: "
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
                f"{_amount_text(log['amount_xr'])} دلار — "
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
        parts = data.split(":")
        nation_id = int(parts[2])
        return_target = parts[3] if len(parts) >= 4 else "nations"
        if return_target not in TREASURY_RETURN_TARGETS:
            return_target = "nations"
        if state is not None:
            await state.update_data(treasury_return=return_target)
            if clear_state:
                await state.clear()
                await state.update_data(treasury_return=return_target)

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
                markup = treasury_keyboard(nation_id, role, return_target=return_target)

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
                context = await get_user_active_nation_context(
                    session,
                    message.from_user.id,
                    repair=True,
                )

                if context is None:
                    logger.warning(
                        "TREASURY|resolve_failed|registered_user_without_active_nation"
                    )
                    await message.answer(
                        "⚠️ هنوز ملت فعالی برای حسابت پیدا نشد.\n"
                        "از «🌍 ملت‌ها» ملت فعال خودت را باز کن."
                    )
                    return

                nation, role, source = context
                nation_id = nation.nation_id
                logger.info(
                    "TREASURY|resolved|nation=%s|role=%s|source=%s",
                    nation_id,
                    role,
                    source,
                )

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


@router.callback_query(F.data.regexp(r"^treasury:show:\d+(?::(?:dashboard|nations|management))?$"))
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


@router.callback_query(F.data.regexp(r"^treasury:deposit:\d+:(?:dashboard|nations|management)$"))
async def start_deposit(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        return_target = parts[3]
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
        await state.update_data(
            nation_id=nation_id,
            treasury_return=return_target,
        )

        if callback.message is not None:
            await callback.message.edit_text(
                "💎 <b>واریز به خزانه</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"موجودی دلار شما: "
                f"<code>{_amount_text(xr_balance)}</code>\n"
                f"حداقل واریز: <code>{_amount_text(MIN_DEPOSIT)}</code> دلار\n\n"
                "مقدار واریز را بنویسید:",
                reply_markup=cancel_keyboard(nation_id, return_target),
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
                            f"<code>{_amount_text(result['balance'])}</code> دلار",
                            parse_mode="HTML",
                        )
                        return
                    if reason == "below_minimum":
                        await message.answer(
                            "🔴 حداقل مقدار واریز "
                            f"{_amount_text(result['minimum'])} دلار است."
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
                    f"{_amount_text(amount)} دلار به خزانه واریز شد.\n"
                    f"موجودی جدید شما: "
                    f"<code>{_amount_text(result['new_balance'])}</code> دلار"
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
            return_target = data.get("treasury_return", "dashboard")
            await message.answer(
                refreshed_text,
                reply_markup=treasury_keyboard(
                    int(nation_id),
                    role,
                    return_target=return_target,
                ),
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


@router.callback_query(F.data.regexp(r"^treasury:withdraw:\d+:(?:dashboard|nations|management)$"))
async def start_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        return_target = parts[3]
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
        await state.update_data(
            nation_id=nation_id,
            treasury_return=return_target,
        )

        if callback.message is not None:
            await callback.message.edit_text(
                "📤 <b>برداشت از خزانه</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"موجودی خزانه: "
                f"<code>{_amount_text(treasury['balance_xr'])}</code> دلار\n\n"
                "مقدار برداشت را بنویسید:",
                reply_markup=cancel_keyboard(nation_id, return_target),
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
            "⚠️ <b>تأیید برداشت</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"مقدار: <code>{_amount_text(amount)}</code> دلار\n"
            "این مبلغ به موجودی دلار شما اضافه می‌شود.\n\n"
            "مطمئنید؟",
            reply_markup=confirm_withdraw_keyboard(
                int(nation_id),
                amount_str,
                data.get("treasury_return", "dashboard"),
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


@router.callback_query(F.data.regexp(r"^treasury:withdraw_confirm:\d+:[0-9.]+:(?:dashboard|nations|management)$"))
async def confirm_withdraw(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        amount = Decimal(parts[3])
        return_target = parts[4]

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
            f"{_amount_text(amount)} دلار از خزانه برداشت شد."
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
                    reply_markup=treasury_keyboard(
                        nation_id,
                        role,
                        return_target=return_target,
                    ),
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
        return_target = data.get("treasury_return", "dashboard")

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
                return_target,
            ),
        )
    except Exception:
        logger.exception(
            "Failed to re-show withdrawal confirmation user=%s",
            message.from_user.id,
        )
        await state.clear()
        await message.answer("⚠️ خطای موقت رخ داد. دوباره وارد خزانه شو.")


@router.callback_query(F.data.regexp(r"^treasury:report:\d+:(?:dashboard|nations|management)$"))
async def show_full_report(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        return_target = parts[3]
        await state.update_data(treasury_return=return_target)

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
                    f"{_amount_text(log['amount_xr'])} دلار"
                    f"{note}\n"
                    f"   {time_ago(log['created_at'])}"
                )

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ بازگشت به خزانه",
                        callback_data=f"treasury:show:{nation_id}:{return_target}",
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


@router.callback_query(
    F.data.regexp(r"^treasury:back:\d+:(?:dashboard|nations|management)$")
)
async def treasury_back(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        nation_id = int(parts[2])
        return_target = parts[3]

        await state.clear()

        if return_target == "dashboard":
            async with async_session() as session:
                async with session.begin():
                    user = await session.get(User, callback.from_user.id)
                    if user is None:
                        await callback.answer("⚠️ حساب کاربری پیدا نشد.", show_alert=True)
                        return
            if callback.message is not None:
                await show_dashboard(
                    callback.message,
                    user,
                    replace_inline=True,
                    bot=callback.bot,
                    display_user=callback.from_user,
                )
            await callback.answer()
            return

        if return_target == "nations":
            if callback.message is not None:
                async with async_session() as session:
                    async with session.begin():
                        user = await session.get(User, callback.from_user.id)
                        registered = user is not None and await is_fully_registered(
                            session,
                            callback.from_user.id,
                        )
                        if not registered:
                            await callback.answer(
                                "⚠️ اول باید وارد بازی بشی.",
                                show_alert=True,
                            )
                            return
                        context = await get_user_active_nation_context(
                            session,
                            callback.from_user.id,
                            repair=True,
                        )
                        active_nation = context[0] if context is not None else None
                        active_role = context[1] if context is not None else None
                        is_manager = active_role in {"founder", "minister"}
                        nation_panel = nation_panel_keyboard(
                            is_manager,
                            active_nation.nation_id if active_nation is not None else nation_id,
                        )
                await callback.message.edit_text(
                    "🌍 <b>ملت‌ها</b>\n"
                    "اینجا می‌تونی ملت‌ها رو بررسی کنی.\n"
                    "از گزینه‌ها برای ادامه استفاده کن.",
                    reply_markup=nation_panel,
                    parse_mode="HTML",
                )
            await callback.answer()
            return

        if return_target == "management":
            try:
                markup = await nation_admin_panel(
                    callback.from_user.id,
                    nation_id,
                )
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return

            async with async_session() as session:
                async with session.begin():
                    nation = await session.get(Nation, nation_id)
                    if nation is None or not nation.is_active:
                        await callback.answer("⚠️ ملت فعال پیدا نشد.", show_alert=True)
                        return
                    # Reuse the canonical management panel text contract.
                    member_count = nation.member_count
                    text = f"👑 <b>مدیریت {html.escape(nation.name)}</b>\n"
                    text += "━━━━━━━━━━━━━━━━━━━━\n"
                    text += f"👥 اعضا: <b>{member_count}</b>\n"
                    text += f"💰 خزانه: <b>{_amount_text(nation.treasury)}</b> دلار\n"
                await callback.message.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            await callback.answer()
            return
    except Exception:
        logger.exception(
            "Treasury back navigation failed user=%s data=%r",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(
            "⚠️ بازگشت انجام نشد.",
            show_alert=True,
        )


@router.callback_query(F.data.regexp(r"^treasury:cancel:\d+:(?:dashboard|nations|management)$"))
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
