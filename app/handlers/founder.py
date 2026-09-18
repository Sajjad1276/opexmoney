from __future__ import annotations

import html
import logging
import re

from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.database.models import CurrencyHolding, Nation, User
from app.database.session import async_session
from app.keyboards.reply import main_menu
from app.services.nation_service import create_nation
from app.services.user_service import get_user
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from app.utils.validators import validate_currency_code, validate_nation_name

logger = logging.getLogger(__name__)
founder_router = Router(name="founder")


def founder_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_founder")]
        ]
    )


def founder_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأسیس ملت",
                    callback_data="confirm_founder",
                ),
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="cancel_founder",
                ),
            ]
        ]
    )


def _group_id_is_valid(value: str) -> bool:
    return bool(re.fullmatch(r"-100\d{9,10}", value.strip()))


@founder_router.callback_query(
    F.data == "found_nation",
    StateFilter(None, OnboardingStates.SELECT_NATION),
)
async def start_founder(call: CallbackQuery, state: FSMContext) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            if user is None or not (user.username or "").strip():
                await call.answer("⚠️ اول باید اسم معامله‌گرت رو ثبت کنی.", show_alert=True)
                return

    if user and user.role == "founder":
        await call.answer(
            "⚠️ تو قبلاً یه ملت داری. هر معامله‌گر فقط یه ملت می‌تونه بسازه.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(FounderStates.WAITING_GROUP_LINK)
    await call.answer()

    if call.message:
        await call.message.answer(
            "🏛 <b>تأسیس ملت — مرحله ۱ از ۳</b>\n"
            "پایتخت ملتت کجاست؟\n"
            "آیدی عددی گروه تلگرامی رو بفرست.\n"
            "(مثال: -1001234567890)\n"
            "ربات باید از قبل در اون گروه باشه.",
            reply_markup=founder_cancel_keyboard(),
            parse_mode="HTML",
        )


@founder_router.message(
    FounderStates.WAITING_GROUP_LINK,
    F.text,
)
async def receive_group_id(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()

    if not _group_id_is_valid(value):
        await message.answer(
            "⚠️ آیدی گروه اشتباهه.\n"
            "آیدی باید عدد منفی باشه. (مثال: -1001234567890)",
            reply_markup=founder_cancel_keyboard(),
        )
        return

    group_id = int(value)
    async with async_session() as session:
        async with session.begin():
            existing = await session.execute(
                select(Nation.nation_id)
                .where(
                    Nation.group_id == group_id,
                    Nation.is_active.is_(True),
                )
                .limit(1)
            )
            group_taken = existing.scalar_one_or_none() is not None

    if group_taken:
        await message.answer(
            "⚠️ این گروه قبلاً یه ملت فعال داره.\n"
            "یه گروه دیگه انتخاب کن.",
            reply_markup=founder_cancel_keyboard(),
        )
        return

    await state.update_data(group_id=group_id)
    await state.set_state(FounderStates.SET_NATION_NAME)
    await message.answer(
        "🏛 <b>تأسیس ملت — مرحله ۲ از ۳</b>\n\n"
        "اسم ملتت رو بنویس.\n\n"
        "بین ۲ تا ۲۰ کاراکتر\n"
        "فارسی یا انگلیسی مجازه.",
        reply_markup=founder_cancel_keyboard(),
        parse_mode="HTML",
    )


@founder_router.message(
    FounderStates.SET_NATION_NAME,
    F.text,
)
async def receive_nation_name(message: Message, state: FSMContext) -> None:
    valid, error = validate_nation_name(message.text or "")
    if not valid:
        await message.answer(error, reply_markup=founder_cancel_keyboard())
        return

    await state.update_data(nation_name=(message.text or "").strip())
    await state.set_state(FounderStates.SET_CURRENCY_CODE)
    await message.answer(
        "🏛 <b>تأسیس ملت — مرحله ۳ از ۳</b>\n\n"
        "کد ارز ملتت رو انتخاب کن.\n\n"
        "دقیقاً ۳ حرف لاتین بزرگ\n"
        "مثال: IRN یا PRS یا AZD\n"
        "این کد دیگه قابل تغییر نیست.",
        reply_markup=founder_cancel_keyboard(),
        parse_mode="HTML",
    )


@founder_router.message(
    FounderStates.SET_CURRENCY_CODE,
    F.text,
)
async def receive_currency_code(message: Message, state: FSMContext) -> None:
    async with async_session() as session:
        async with session.begin():
            valid, error = await validate_currency_code(message.text or "", session)

    if not valid:
        await message.answer(error, reply_markup=founder_cancel_keyboard())
        return

    code = (message.text or "").strip().upper()
    data = await state.get_data()
    await state.update_data(currency_code=code)
    await state.set_state(FounderStates.CONFIRM)

    await message.answer(
        "📋 <b>اطلاعات ملت تو:</b>\n"
        f"🏛 {html.escape(data['nation_name'])} · 💱 {html.escape(code)}\n"
        f"🗺 پایتخت: گروه {data['group_id']}\n"
        f"👑 بنیان‌گذار: {html.escape(message.from_user.first_name or 'معامله‌گر')}\n"
        f"💰 موجودی اولیه: ۱۰۰۰ {html.escape(code)}\n"
        "کد ارز بعد از تأسیس قابل تغییر نیست.",
        reply_markup=founder_confirm_keyboard(),
        parse_mode="HTML",
    )


@founder_router.callback_query(
    F.data == "confirm_founder",
    StateFilter(FounderStates.CONFIRM),
)
async def confirm_founder(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    group_id = data.get("group_id")
    nation_name = data.get("nation_name")
    currency_code = data.get("currency_code")

    if not all((group_id, nation_name, currency_code)):
        await state.clear()
        await call.answer("⚠️ اطلاعات تأسیس کامل نیست. دوباره شروع کن.", show_alert=True)
        return

    async with async_session() as session:
        try:
            nation = await create_nation(
                session=session,
                founder_user_id=call.from_user.id,
                group_id=int(group_id),
                nation_name=nation_name,
                currency_code=currency_code,
            )
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
        except Exception:
            logger.exception("Nation creation failed")
            await call.answer("⚠️ تأسیس ملت انجام نشد. دوباره امتحان کن.", show_alert=True)
            return

    await state.clear()
    await call.answer()

    if call.message:
        await call.message.answer(
            "🎉 <b>ملت تأسیس شد!</b>\n\n"
            f"🏛 ملت {html.escape(nation.name)}\n"
            "👑 تو بنیان‌گذار این ملتی\n"
            f"💰 موجودی: ۱۰۰۰ {html.escape(nation.currency_code)}\n"
            "📈 نرخ اولیه: ۱.۰۰ ΩXR\n"
            "از پنل ملت‌ها می‌تونی مدیریت کنی.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )

    try:
        await bot.send_message(
            chat_id=int(group_id),
            text=(
                f"🏛 <b>ملت {html.escape(nation.name)} تأسیس شد!</b>\n\n"
                f"💱 ارز رسمی: {html.escape(nation.currency_code)}\n"
                f"👑 بنیان‌گذار: {html.escape(call.from_user.first_name or 'معامله‌گر')}\n"
                "📈 نرخ اولیه: ۱.۰۰ ΩXR\n"
                "برای پیوستن، ربات رو استارت کن."
            ),
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("Could not announce nation %s in group %s", nation.nation_id, group_id)


@founder_router.callback_query(
    F.data == "cancel_founder",
    StateFilter(
        FounderStates.WAITING_GROUP_LINK,
        FounderStates.SET_NATION_NAME,
        FounderStates.SET_CURRENCY_CODE,
        FounderStates.CONFIRM,
    ),
)
async def cancel_founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    if call.message:
        await call.message.answer(
            "❌ تأسیس ملت لغو شد.",
            reply_markup=main_menu(),
        )
