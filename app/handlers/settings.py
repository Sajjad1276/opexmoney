from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from app.database.session import async_session
from app.services.settings_service import (
    change_home_nation,
    change_username,
    get_active_nations,
    get_settings_data,
    get_user_stats,
    is_username_taken,
)
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name
from app.handlers.onboarding_fix import BLOCKED_NAME, INVALID_NAME
from app.utils.formatting import fmt_amount, to_fa

router = Router(name="settings")
logger = logging.getLogger(__name__)

RLM = "\u200f"
SETTINGS_ERROR = "خطا در تنظیمات. دوباره تلاش کن."

USERNAME_PROMPT = (
    f"{RLM}✏️ <b>نام جدید معامله‌گر را بنویس:</b>\n"
    f"{RLM}(۳ تا ۲۰ حرف — فارسی، انگلیسی، عدد، خط تیره)"
)


class SettingsStates(StatesGroup):
    CHANGE_USERNAME = State()
    CONFIRM_CHANGE_NATION = State()
    SELECT_NEW_NATION = State()


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ تغییر نام معامله‌گر",
                    callback_data="settings:change_username",
                    style=ButtonStyle.PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏛 تغییر ملت اصلی",
                    callback_data="settings:change_nation",
                    style=ButtonStyle.PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 آمار کامل من",
                    callback_data="settings:stats",
                    style=ButtonStyle.PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_to_dashboard",
                )
            ],
        ]
    )


def confirm_nation_change_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ مطمئنم",
                    callback_data="settings:nation_confirm",
                ),
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="settings:nation_cancel",
                ),
            ]
        ]
    )


def nation_select_keyboard(nations: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{nation['name']} ({nation['currency_code']})",
                callback_data=f"settings:nation_select:{nation['id']}",
            )
        ]
        for nation in nations[:20]
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="settings:nation_cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_settings_msg(data: dict) -> str:
    lines = [
        "⚙️ <b>تنظیمات</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"👤 نام معامله‌گر: <code>{html.escape(data['username'])}</code>",
        f"🏛 ملت اصلی: {html.escape(data['nation_name'] or 'ندارد')}",
        f"📅 عضویت: {html.escape(data['created_at_jalali'])}",
        f"🆔 شناسه: <code>{to_fa(data['user_id'])}</code>",
    ]
    return "\n".join(f"{RLM}{line}" for line in lines)


def build_stats_msg(username: str, stats: dict) -> str:
    best_rank = (
        f"#{to_fa(stats['best_rank'])}"
        if stats["best_rank"]
        else "ثبت نشده"
    )
    lines = [
        f"📊 <b>آمار {html.escape(username)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"💹 کل معاملات: {to_fa(stats['tx_count'])}",
        f"📈 حجم خرید: {to_fa(fmt_amount(stats['buy_volume']))} ΩXR",
        f"📉 حجم فروش: {to_fa(fmt_amount(stats['sell_volume']))} ΩXR",
        f"⚡ معاملات امروز: {to_fa(stats['today_count'])}",
        f"🏆 بهترین رتبه: {best_rank}",
        f"📅 روزهای فعال: {to_fa(stats['active_days'])} روز",
    ]
    return "\n".join(f"{RLM}{line}" for line in lines)


async def _get_settings_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with async_session() as session:
        async with session.begin():
            data = await get_settings_data(session, user_id)
    return build_settings_msg(data), settings_keyboard()


async def _show_settings_from_message(message: Message) -> None:
    text, markup = await _get_settings_panel(message.from_user.id)
    await message.answer("⁠", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def _edit_settings_from_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    text, markup = await _get_settings_panel(callback.from_user.id)
    await callback.message.edit_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "⚙️ تنظیمات")
async def show_settings(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        await _show_settings_from_message(message)
    except Exception as e:
        logger.exception(e)
        await message.answer(SETTINGS_ERROR)


@router.callback_query(F.data == "settings:back")
async def open_settings_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await state.clear()
        if callback.message is None:
            await callback.answer()
            return
        await _edit_settings_from_callback(callback)
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:change_username")
async def start_change_username(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await state.set_state(SettingsStates.CHANGE_USERNAME)
        if callback.message is None:
            await callback.answer()
            return
        await callback.message.edit_text(
            USERNAME_PROMPT,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف",
                            callback_data="settings:cancel_fsm",
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.message(SettingsStates.CHANGE_USERNAME)
async def receive_new_username(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        new_name = (message.text or "").strip()

        if is_blocked_trader_name(new_name):
            await message.answer(BLOCKED_NAME, parse_mode=ParseMode.HTML)
            return

        if not is_valid_trader_name(new_name):
            await message.answer(INVALID_NAME, parse_mode=ParseMode.HTML)
            return

        async with async_session() as session:
            async with session.begin():
                taken = await is_username_taken(
                    session,
                    new_name,
                    message.from_user.id,
                )
                if not taken:
                    await change_username(
                        session,
                        message.from_user.id,
                        new_name,
                    )

        if taken:
            await message.answer(
                f"{RLM}این نام قبلاً گرفته شده. نام دیگری انتخاب کن.",
                parse_mode=ParseMode.HTML,
            )
            return

        await state.clear()
        await message.answer(
            f"{RLM}✅ نام معامله‌گر به <code>{html.escape(new_name)}</code> تغییر یافت.",
            parse_mode=ParseMode.HTML,
        )
        await _show_settings_from_message(message)
    except Exception as e:
        logger.exception(e)
        await message.answer(SETTINGS_ERROR)


@router.callback_query(F.data == "settings:change_nation")
async def start_change_nation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                data = await get_settings_data(session, callback.from_user.id)
                nations = (
                    await get_active_nations(session)
                    if data["nation_name"] is None
                    else []
                )

        if data["nation_name"] is not None:
            await state.set_state(SettingsStates.CONFIRM_CHANGE_NATION)
            if callback.message is None:
                await callback.answer()
                return
            warning = "\n".join(
                [
                    f"{RLM}⚠️ <b>تغییر ملت اصلی</b>",
                    f"{RLM}━━━━━━━━━━━━━━━━━━",
                    f"{RLM}موجودی ارز ملت فعلی‌ات نگه داشته می‌شه،",
                    f"{RLM}ولی ارز اصلی حسابت به ملت جدید تغییر می‌کنه.",
                    f"{RLM}مطمئنی؟",
                ]
            )
            await callback.message.edit_text(
                warning,
                reply_markup=confirm_nation_change_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return

        if not nations:
            await callback.answer("هیچ ملت فعالی وجود ندارد", show_alert=True)
            return

        await state.set_state(SettingsStates.SELECT_NEW_NATION)
        if callback.message is None:
            await callback.answer()
            return
        await callback.message.edit_text(
            f"{RLM}🏛 <b>ملت جدید را انتخاب کن:</b>",
            reply_markup=nation_select_keyboard(nations),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:nation_confirm")
async def confirm_nation_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                nations = await get_active_nations(session)

        if not nations:
            await callback.answer("هیچ ملت فعالی وجود ندارد", show_alert=True)
            return

        await state.set_state(SettingsStates.SELECT_NEW_NATION)
        if callback.message is None:
            await callback.answer()
            return
        await callback.message.edit_text(
            f"{RLM}🏛 <b>ملت جدید را انتخاب کن:</b>",
            reply_markup=nation_select_keyboard(nations),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data.startswith("settings:nation_select:"))
async def select_new_nation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        nation_id = int(callback.data.rsplit(":", 1)[1])
        async with async_session() as session:
            async with session.begin():
                await change_home_nation(
                    session,
                    callback.from_user.id,
                    nation_id,
                )

        await state.clear()

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            f"{RLM}✅ ملت اصلی تغییر کرد.",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(0.5)
        await _edit_settings_from_callback(callback)
        await callback.answer()
    except ValueError as exc:
        logger.warning(
            "Home nation change rejected | user_id=%s reason=%s",
            callback.from_user.id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:nation_cancel")
async def cancel_nation_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await state.clear()
        if callback.message is None:
            await callback.answer()
            return
        await _edit_settings_from_callback(callback)
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:stats")
async def show_stats(callback: CallbackQuery) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                stats = await get_user_stats(session, callback.from_user.id)
                data = await get_settings_data(session, callback.from_user.id)

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            build_stats_msg(data["username"], stats),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="↩️ بازگشت",
                            callback_data="settings:back_from_stats",
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:back_from_stats")
async def back_from_stats(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await state.clear()
        if callback.message is None:
            await callback.answer()
            return
        await _edit_settings_from_callback(callback)
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)


@router.callback_query(F.data == "settings:cancel_fsm")
async def cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await state.clear()
        if callback.message is None:
            await callback.answer()
            return
        await _edit_settings_from_callback(callback)
        await callback.answer()
    except Exception as e:
        logger.exception(e)
        await callback.answer(SETTINGS_ERROR, show_alert=True)
