from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ButtonStyle
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, ReplyKeyboardRemove
from sqlalchemy import select

from app.database.models import BotGroup, Nation, User
from app.database.session import async_session
from app.keyboards.inline import (
    add_to_group_keyboard,
    confirm_found_nation_keyboard,
    founder_flag_selection_keyboard,
)
from app.services.nation_service import create_nation
from app.handlers.start import show_dashboard
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
DEFAULT_NATION_FLAG = "🏴"
FOUNDER_FLAG_OPTIONS = {
    DEFAULT_NATION_FLAG,
    "🚩", "🏳", "🎌", "🏁",
    "🇮🇷", "🇫🇮", "🇺🇸", "🇯🇵", "🇩🇪", "🇧🇷", "🇫🇷", "🇹🇷",
}


def founder_cancel_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="cancel_founder",
                style=ButtonStyle.DANGER,
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
        await call.message.edit_text(
            "🏛 <b>تأسیس ملت · مرحله 1</b>\n"
            "اول ربات رو به گروهی که می‌خوای پایتخت ملتت باشه اضافه کن.\n"
            "در فرم تلگرام، ربات رو به‌عنوان ادمین اضافه کن.\n"
            "دسترسی‌های لازم از قبل پیشنهاد می‌شن.\n"
            "بعد از ادمین شدن، روی «بررسی و دریافت اطلاعات» بزن تا مشخصات گروه ثبت بشه.",
            reply_markup=add_to_group_keyboard(group_link),
            parse_mode="HTML",
        )


async def _continue_group_onboarding(
    *,
    bot: Bot,
    dispatcher: Dispatcher,
    founder_user_id: int,
    group_id: int,
    group_title: str,
    group_username: str | None,
    group_type: str,
) -> None:
    founder_state = await dispatcher.fsm.get_context(
        bot=bot,
        chat_id=founder_user_id,
        user_id=founder_user_id,
    )
    current_state = await founder_state.get_state()
    if current_state != FounderStates.WAITING_GROUP_ADMIN.state:
        return

    data = await founder_state.get_data()
    if data.get("founder_user_id") != founder_user_id:
        return

    previous_message_id = data.get("onboarding_message_id")
    try:
        bot_member = await bot.get_chat_member(group_id, bot.id)
        founder_member = await bot.get_chat_member(group_id, founder_user_id)
        bot_status = getattr(bot_member.status, "value", bot_member.status)
        founder_status = getattr(founder_member.status, "value", founder_member.status)
    except Exception:
        logger.exception("Could not verify group onboarding state for %s", group_id)
        await bot.send_message(
            founder_user_id,
            "⚠️ وضعیت گروه قابل بررسی نیست. چند ثانیه بعد دوباره امتحان کن.",
        )
        return

    if bot_status not in {"administrator", "creator"}:
        return

    if founder_status not in {"administrator", "creator"}:
        await bot.send_message(
            founder_user_id,
            "⚠️ برای تأسیس ملت باید خودت ادمین یا مالک گروه باشی.",
        )
        return

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
            if existing.scalar_one_or_none() is not None:
                await founder_state.clear()
                await bot.send_message(
                    founder_user_id,
                    "⚠️ این گروه قبلاً پایتخت یک ملت فعاله.",
                )
                return

            bot_group = await session.get(BotGroup, group_id)
            if bot_group is None:
                session.add(
                    BotGroup(
                        group_id=group_id,
                        title=group_title,
                        username=group_username,
                        is_active=True,
                    )
                )
            else:
                bot_group.title = group_title
                bot_group.username = group_username
                bot_group.is_active = True

    await founder_state.update_data(
        group_id=group_id,
        group_title=group_title,
        group_username=group_username,
        group_type=group_type,
    )
    await founder_state.set_state(FounderStates.SET_NATION_NAME)

    if previous_message_id:
        try:
            await bot.delete_message(founder_user_id, int(previous_message_id))
        except Exception:
            logger.debug("Could not delete previous founder panel", exc_info=True)

    connected_message = await bot.send_message(
        founder_user_id,
        "🎉 <b>گروه با موفقیت متصل شد!</b>\n"
        f"🏛 پایتخت: <b>{html.escape(group_title)}</b>\n"
        "🤖 ربات با دسترسی ادمین فعال شد.\n"
        "حالا اسم انگلیسی ملتت رو بفرست.\n"
        "فقط حروف انگلیسی و فاصله، بدون عدد و علامت.",
        parse_mode="HTML",
        reply_markup=founder_cancel_keyboard(),
    )
    await founder_state.update_data(onboarding_message_id=connected_message.message_id)

    try:
        await bot.send_message(
            group_id,
            "🎉 <b>اتصال OPEX MONEY موفق شد!</b>\n"
            "👑 بنیان‌گذار این گروه را برای تأسیس ملت انتخاب کرده.\n"
            "🤖 ربات با دسترسی ادمین فعال شد.\n"
            "حالا در پیام خصوصی اسم ملت را انتخاب کن.",
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("Could not announce group onboarding in %s", group_id)


@founder_router.callback_query(
    F.data == "founder_check_group",
    StateFilter(FounderStates.WAITING_GROUP_ADMIN),
)
async def check_founder_group(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    """Re-check groups already observed by Telegram after the user taps Check."""
    founder_user_id = call.from_user.id

    async with async_session() as session:
        async with session.begin():
            groups = (
                await session.execute(
                    select(BotGroup)
                    .where(BotGroup.is_active.is_(True))
                    .order_by(BotGroup.updated_at.desc(), BotGroup.group_id.desc())
                    .limit(20)
                )
            ).scalars().all()

    for group in groups:
        try:
            bot_member = await bot.get_chat_member(group.group_id, bot.id)
            founder_member = await bot.get_chat_member(group.group_id, founder_user_id)
        except Exception:
            continue

        bot_status = getattr(bot_member.status, "value", bot_member.status)
        founder_status = getattr(founder_member.status, "value", founder_member.status)

        if bot_status not in {"administrator", "creator"}:
            continue
        if founder_status not in {"administrator", "creator"}:
            continue

        await _continue_group_onboarding(
            bot=bot,
            dispatcher=dispatcher,
            founder_user_id=founder_user_id,
            group_id=group.group_id,
            group_title=group.title,
            group_username=group.username,
            group_type="supergroup",
        )
        await call.answer("✅ گروه پیدا شد و وضعیت ربات بررسی شد.")
        return

    await call.answer(
        "⚠️ هنوز گروهی که ربات در آن ادمین باشد پیدا نشد. "
        "اول ربات را ادمین کن، سپس دوباره «بررسی گروه» را بزن.",
        show_alert=True,
    )

@founder_router.my_chat_member()
async def bot_group_status_changed(
    event: ChatMemberUpdated,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return

    new_status = getattr(
        event.new_chat_member.status,
        "value",
        event.new_chat_member.status,
    )
    if new_status not in {"administrator", "creator"}:
        return

    group_title = event.chat.title or "گروه بدون نام"
    group_username = getattr(event.chat, "username", None)

    async with async_session() as session:
        async with session.begin():
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

    actor = event.from_user
    if actor is not None:
        try:
            await bot.send_message(
                actor.id,
                "✅ ربات ادمین شد. مشخصات گروه ثبت شد.\n"
                "حالا در پنل تأسیس ملت روی «🔎 بررسی و دریافت اطلاعات» بزن.",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("Could not notify founder about group admin state", exc_info=True)


@founder_router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^/start(?:@[^ ]+)?\s+founder_(\d+)$"),
)
async def group_founder_start(
    message: Message,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    match = message.text and re.match(
        r"^/start(?:@[^ ]+)?\s+founder_(\d+)$",
        message.text,
    )
    if not match:
        return

    founder_user_id = int(match.group(1))
    group_title = message.chat.title or "گروه بدون نام"
    group_username = getattr(message.chat, "username", None)

    async with async_session() as session:
        async with session.begin():
            bot_group = await session.get(BotGroup, message.chat.id)
            if bot_group is None:
                session.add(
                    BotGroup(
                        group_id=message.chat.id,
                        title=group_title,
                        username=group_username,
                        is_active=True,
                    )
                )
            else:
                bot_group.title = group_title
                bot_group.username = group_username
                bot_group.is_active = True

    try:
        await bot.send_message(
            founder_user_id,
            f"✅ گروه «{html.escape(group_title)}» ثبت شد.\n"
            "حالا به پنل تأسیس ملت برگرد و «🔎 بررسی و دریافت اطلاعات» را بزن.",
            parse_mode="HTML",
        )
    except Exception:
        logger.debug("Could not notify founder after group start", exc_info=True)


@founder_router.message(
    FounderStates.SET_NATION_NAME,
    F.text,
)
async def receive_nation_name(message: Message, state: FSMContext) -> None:
    valid, error = validate_nation_name(message.text or "")
    if not valid:
        data = await state.get_data()
        previous_message_id = data.get("onboarding_message_id")
        if previous_message_id:
            try:
                await message.bot.delete_message(message.chat.id, int(previous_message_id))
            except Exception:
                logger.debug("Could not delete invalid-name panel", exc_info=True)

        panel = await message.answer(
            error,
            reply_markup=founder_cancel_keyboard(),
        )
        await state.update_data(onboarding_message_id=panel.message_id)
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

    previous_message_id = data.get("onboarding_message_id")
    if previous_message_id:
        try:
            await message.bot.delete_message(message.chat.id, int(previous_message_id))
        except Exception:
            logger.debug("Could not delete previous founder panel", exc_info=True)

    confirmation_message = await message.answer(
        "📋 <b>آماده تأسیس ملت</b>\n"
        f"🏛 نام ملت: <b>{html.escape(data.get('nation_name', message.text.strip()))}</b>\n"
        f"💱 کد خودکار ارز: <b>{html.escape(code)}</b>\n"
        f"🗺 پایتخت: <b>{html.escape(data.get('group_title', 'گروه'))}</b>\n"
        "کد ارز بر اساس نام ملت ساخته شده و باید یکتا باشه.",
        reply_markup=confirm_found_nation_keyboard(),
        parse_mode="HTML",
    )
    await state.update_data(onboarding_message_id=confirmation_message.message_id)


async def _verify_founder_group(
    *,
    bot: Bot,
    group_id: int,
    founder_user_id: int,
) -> str | None:
    try:
        bot_member = await bot.get_chat_member(group_id, bot.id)
        founder_member = await bot.get_chat_member(group_id, founder_user_id)
        bot_status = getattr(bot_member.status, "value", bot_member.status)
        founder_status = getattr(founder_member.status, "value", founder_member.status)

        if bot_status not in {"administrator", "creator"}:
            return "⚠️ ربات دیگه ادمین این گروه نیست."
        if founder_status not in {"administrator", "creator"}:
            return "⚠️ تو دیگه ادمین این گروه نیستی."
    except Exception:
        logger.exception("Could not verify founder group before creation")
        return "⚠️ وضعیت گروه قابل بررسی نیست. دوباره امتحان کن."

    return None


async def _create_founder_nation(
    *,
    state: FSMContext,
    bot: Bot,
    founder_user_id: int,
    flag_emoji: str,
) -> tuple[Nation, int]:
    data = await state.get_data()
    group_id = data.get("group_id")
    nation_name = data.get("nation_name")
    currency_code = data.get("currency_code")

    if not all((group_id, nation_name, currency_code)):
        await state.clear()
        raise ValueError("⚠️ اطلاعات تأسیس کامل نیست. دوباره شروع کن.")

    try:
        normalized_group_id = int(group_id)
    except (TypeError, ValueError) as exc:
        await state.clear()
        raise ValueError("⚠️ اطلاعات گروه معتبر نیست. دوباره شروع کن.") from exc

    verification_error = await _verify_founder_group(
        bot=bot,
        group_id=normalized_group_id,
        founder_user_id=founder_user_id,
    )
    if verification_error:
        raise ValueError(verification_error)

    selected_flag = (
        flag_emoji
        if flag_emoji in FOUNDER_FLAG_OPTIONS
        else DEFAULT_NATION_FLAG
    )

    async with async_session() as session:
        try:
            nation = await create_nation(
                session=session,
                founder_user_id=founder_user_id,
                group_id=normalized_group_id,
                nation_name=nation_name,
                currency_code=currency_code,
                flag_emoji=selected_flag,
            )
        except ValueError:
            raise
        except Exception:
            logger.exception("Nation creation failed")
            raise RuntimeError("⚠️ تأسیس ملت انجام نشد. دوباره امتحان کن.")

    await state.clear()
    return nation, normalized_group_id


async def _send_founder_success(
    *,
    target_message: Message,
    bot: Bot,
    nation: Nation,
    group_id: int,
    founder_name: str,
    display_user=None,
) -> None:
    try:
        await target_message.delete()
    except Exception:
        logger.debug("Could not delete founder inline panel", exc_info=True)

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, nation.founder_user_id)

    if user is not None:
        await show_dashboard(
            target_message,
            user,
            replace_inline=True,
            bot=bot,
            display_user=display_user,
        )

    try:
        flag = html.escape(nation.flag_emoji or DEFAULT_NATION_FLAG)
        await bot.send_message(
            group_id,
            "🎉 <b>ملت جدید تأسیس شد!</b>\n"
            f"{flag} <b>نام ملت: {html.escape(nation.name)}</b>\n"
            f"💱 ارز رسمی: <b>{html.escape(nation.currency_code)}</b>\n"
            f"👑 بنیان‌گذار: {html.escape(founder_name or 'بنیان‌گذار')}\n"
            "این گروه حالا پایتخت این ملت است.",
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning(
            "Could not announce nation %s in group %s",
            nation.nation_id,
            group_id,
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
        await call.answer(
            "⚠️ اطلاعات تأسیس کامل نیست. دوباره شروع کن.",
            show_alert=True,
        )
        return

    await state.update_data(flag_emoji=DEFAULT_NATION_FLAG)
    await state.set_state(FounderStates.SELECT_FLAG)
    await call.answer()

    if call.message:
        await call.message.edit_text(
            "🚩 <b>پرچم ملتت رو انتخاب کن</b>\n"
            f"🏛 نام ملت: <b>{html.escape(nation_name)}</b>\n"
            f"💱 کد ارز: <b>{html.escape(currency_code)}</b>\n\n"
            "پرچم فقط برای ظاهر و هویت بصری ملته و روی اقتصاد بازی اثری نداره.",
            reply_markup=founder_flag_selection_keyboard(),
            parse_mode="HTML",
        )


@founder_router.callback_query(
    F.data.startswith("founder_flag:"),
    StateFilter(FounderStates.SELECT_FLAG),
)
async def select_founder_flag(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    raw_flag = (call.data or "").split(":", 1)[1] if call.data else ""
    selected_flag = (
        DEFAULT_NATION_FLAG
        if raw_flag == "default"
        else raw_flag
    )

    try:
        nation, group_id = await _create_founder_nation(
            state=state,
            bot=bot,
            founder_user_id=call.from_user.id,
            flag_emoji=selected_flag,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    except RuntimeError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.answer("✅ پرچم ملت ثبت شد.")
    if call.message:
        await _send_founder_success(
            target_message=call.message,
            bot=bot,
            nation=nation,
            group_id=group_id,
            founder_name=call.from_user.first_name or "بنیان‌گذار",
            display_user=call.from_user,
        )


@founder_router.message(FounderStates.SELECT_FLAG)
async def receive_founder_flag_fallback(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    try:
        nation, group_id = await _create_founder_nation(
            state=state,
            bot=bot,
            founder_user_id=message.from_user.id,
            flag_emoji=DEFAULT_NATION_FLAG,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except RuntimeError as exc:
        await message.answer(str(exc))
        return

    await _send_founder_success(
        target_message=message,
        bot=bot,
        nation=nation,
        group_id=group_id,
        founder_name=message.from_user.first_name or "بنیان‌گذار",
        display_user=message.from_user,
    )


@founder_router.callback_query(
    F.data == "cancel_founder",
    StateFilter(
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_NATION_NAME,
        FounderStates.CONFIRM,
        FounderStates.SELECT_FLAG,
    ),
)
async def cancel_founder(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            logger.debug("Could not delete founder inline panel", exc_info=True)

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, call.from_user.id)

        if user is not None:
            await show_dashboard(
                call.message,
                user,
                replace_inline=True,
                bot=bot,
                display_user=call.from_user,
            )
    await call.answer("❌ تأسیس ملت لغو شد.")
