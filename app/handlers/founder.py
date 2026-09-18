from __future__ import annotations

import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy import select

from app.database.models import BotGroup, Nation
from app.database.session import async_session
from app.keyboards.inline import (
    add_to_group_keyboard,
    confirm_found_nation_keyboard,
)
from app.keyboards.reply import main_menu_keyboard
from app.services.nation_service import create_nation
from app.services.user_service import get_user
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from app.utils.validators import (
    generate_unique_currency_code,
    validate_nation_name,
)

logger = logging.getLogger(__name__)
founder_router = Router(name="founder")

_ADMIN_LINK_RIGHTS = "delete_messages+restrict_members+invite_users+pin_messages+manage_topics"


def founder_cancel_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="cancel_founder",
            )
        ]]
    )


@founder_router.callback_query(
    F.data == "found_nation",
    StateFilter(None, OnboardingStates.SELECT_NATION),
)
async def start_founder(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await get_user(session, call.from_user.id)
            if user is None or not (user.username or "").strip():
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
            if user.role == "founder":
                await call.answer("⚠️ تو قبلاً یه ملت داری.", show_alert=True)
                return

    me = await bot.get_me()
    group_link = (
        f"https://t.me/{me.username}"
        f"?startgroup=founder_{call.from_user.id}"
        f"&admin={_ADMIN_LINK_RIGHTS}"
    )

    await state.clear()
    await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
    await state.update_data(founder_user_id=call.from_user.id)

    await call.answer()
    if call.message:
        await call.message.answer(
            "🏛 <b>تأسیس ملت · مرحله ۱</b>\n"
            "اول ربات رو به گروهی که می‌خوای پایتخت ملتت باشه اضافه کن.\n"
            "در فرم تلگرام، ربات رو به‌عنوان ادمین اضافه کن.\n"
            "دسترسی‌های لازم از قبل پیشنهاد می‌شن.\n"
            "بعد از ادمین شدن، ربات خودش گروه رو تشخیص می‌ده.",
            reply_markup=add_to_group_keyboard(group_link),
            parse_mode="HTML",
        )


@founder_router.my_chat_member()
async def bot_group_status_changed(
    event: ChatMemberUpdated,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return

    new_status = getattr(event.new_chat_member.status, "value", event.new_chat_member.status)
    if new_status not in {"administrator", "creator"}:
        return

    actor = event.from_user
    if actor is None:
        return

    actor_state = await dispatcher.fsm.get_context(
        bot=bot,
        chat_id=actor.id,
        user_id=actor.id,
    )
    current_state = await actor_state.get_state()
    if current_state != FounderStates.WAITING_GROUP_ADMIN.state:
        return

    data = await actor_state.get_data()
    if data.get("founder_user_id") != actor.id:
        return

    try:
        actor_member = await bot.get_chat_member(event.chat.id, actor.id)
    except Exception:
        logger.exception("Could not verify founder admin status in chat %s", event.chat.id)
        await bot.send_message(
            actor.id,
            "⚠️ نتونستم دسترسی ادمینت رو بررسی کنم. دوباره امتحان کن.",
        )
        return

    actor_status = getattr(actor_member.status, "value", actor_member.status)
    if actor_status not in {"administrator", "creator"}:
        await bot.send_message(
            actor.id,
            "⚠️ برای تأسیس ملت باید خودت ادمین یا مالک گروه باشی.",
        )
        return

    group_title = event.chat.title or "گروه بدون نام"
    group_username = getattr(event.chat, "username", None)

    async with async_session() as session:
        async with session.begin():
            existing = await session.execute(
                select(Nation.nation_id)
                .where(
                    Nation.group_id == event.chat.id,
                    Nation.is_active.is_(True),
                )
                .limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                await actor_state.clear()
                await bot.send_message(
                    actor.id,
                    "⚠️ این گروه قبلاً پایتخت یک ملت فعاله.",
                )
                return

            bot_group = await session.get(BotGroup, event.chat.id)
            if bot_group is None:
                session.add(
                    BotGroup(
                        group_id=event.chat.id,
                        title=group_title,
                        username=group_username,
                        is_active=True,
                    )
                )
            else:
                bot_group.title = group_title
                bot_group.username = group_username
                bot_group.is_active = True

    await actor_state.update_data(
        group_id=event.chat.id,
        group_title=group_title,
        group_username=group_username,
        group_type=event.chat.type,
    )
    await actor_state.set_state(FounderStates.SET_NATION_NAME)

    await bot.send_message(
        actor.id,
        "🎉 <b>گروه با موفقیت متصل شد!</b>\n"
        f"🏛 پایتخت: <b>{html.escape(group_title)}</b>\n"
        "🤖 ربات با دسترسی ادمین فعال شد.\n"
        "حالا اسم انگلیسی ملتت رو بفرست.\n"
        "فقط حروف انگلیسی و فاصله، بدون عدد و علامت.",
        parse_mode="HTML",
        reply_markup=founder_cancel_keyboard(),
    )

    try:
        await bot.send_message(
            event.chat.id,
            "🎉 <b>اتصال OPEX MONEY موفق شد!</b>\n"
            f"👑 {html.escape(actor.first_name or 'بنیان‌گذار')} این گروه رو برای تأسیس ملت انتخاب کرده.\n"
            "🤖 ربات با دسترسی ادمین فعال شد.\n"
            "حالا در پیام خصوصی اسم ملت رو انتخاب کن.",
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("Could not announce group onboarding in %s", event.chat.id)


@founder_router.message(
    FounderStates.SET_NATION_NAME,
    F.text,
)
async def receive_nation_name(message: Message, state: FSMContext) -> None:
    valid, error = validate_nation_name(message.text or "")
    if not valid:
        await message.answer(error, reply_markup=founder_cancel_keyboard())
        return

    async with async_session() as session:
        async with session.begin():
            code = await generate_unique_currency_code(message.text or "", session)

    data = await state.get_data()
    await state.update_data(
        nation_name=" ".join((message.text or "").strip().split()),
        currency_code=code,
    )
    await state.set_state(FounderStates.CONFIRM)

    await message.answer(
        "📋 <b>آماده تأسیس ملت</b>\n"
        f"🏛 نام ملت: <b>{html.escape(data.get('nation_name', message.text.strip()))}</b>\n"
        f"💱 کد خودکار ارز: <b>{html.escape(code)}</b>\n"
        f"🗺 پایتخت: <b>{html.escape(data.get('group_title', 'گروه'))}</b>\n"
        "کد ارز بر اساس نام ملت ساخته شده و باید یکتا باشه.",
        reply_markup=confirm_found_nation_keyboard(),
        parse_mode="HTML",
    )


@founder_router.callback_query(
    F.data.in_({"confirm_found", "confirm_founder"}),
    StateFilter(FounderStates.CONFIRM),
)
async def confirm_founder(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    group_id = data.get("group_id")
    nation_name = data.get("nation_name")
    currency_code = data.get("currency_code")

    if not all((group_id, nation_name, currency_code)):
        await state.clear()
        await call.answer("⚠️ اطلاعات تأسیس کامل نیست. دوباره شروع کن.", show_alert=True)
        return

    try:
        bot_member = await bot.get_chat_member(int(group_id), bot.id)
        founder_member = await bot.get_chat_member(int(group_id), call.from_user.id)
        bot_status = getattr(bot_member.status, "value", bot_member.status)
        founder_status = getattr(founder_member.status, "value", founder_member.status)
        if bot_status not in {"administrator", "creator"}:
            await call.answer("⚠️ ربات دیگه ادمین این گروه نیست.", show_alert=True)
            return
        if founder_status not in {"administrator", "creator"}:
            await call.answer("⚠️ تو دیگه ادمین این گروه نیستی.", show_alert=True)
            return
    except Exception:
        logger.exception("Could not verify founder group before creation")
        await call.answer("⚠️ وضعیت گروه قابل بررسی نیست. دوباره امتحان کن.", show_alert=True)
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
            "🎉 <b>ملت تأسیس شد!</b>\n"
            f"🏛 {html.escape(nation.name)}\n"
            f"💱 ارز رسمی: <b>{html.escape(nation.currency_code)}</b>\n"
            "👑 تو بنیان‌گذار این ملتی.\n"
            "💰 موجودی اولیه: ۱۰۰۰ واحد\n"
            "🌐 منوی اصلی آماده‌ست.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )

    try:
        await bot.send_message(
            int(group_id),
            "🎉 <b>ملت جدید تأسیس شد!</b>\n"
            f"🏛 نام ملت: <b>{html.escape(nation.name)}</b>\n"
            f"💱 ارز رسمی: <b>{html.escape(nation.currency_code)}</b>\n"
            f"👑 بنیان‌گذار: {html.escape(call.from_user.first_name or 'بنیان‌گذار')}\n"
            "این گروه حالا پایتخت این ملت است.",
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("Could not announce nation %s in group %s", nation.nation_id, group_id)


@founder_router.callback_query(
    F.data == "cancel_founder",
    StateFilter(
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_NATION_NAME,
        FounderStates.CONFIRM,
    ),
)
async def cancel_founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    if call.message:
        await call.message.answer(
            "❌ تأسیس ملت لغو شد.",
            reply_markup=main_menu_keyboard(),
        )
