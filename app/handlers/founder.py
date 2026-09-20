from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy import select

from app.database.models import Nation, NationFoundingDraft, User
from app.database.session import async_session
from app.handlers.start import show_dashboard
from app.keyboards.inline import (
    add_to_group_keyboard,
    founder_cancel_keyboard,
    founder_flag_selection_keyboard,
    founder_name_step_keyboard,
    founder_review_keyboard,
)
from app.services.founder_service import (
    bind_group,
    cancel_draft,
    finalize_draft,
    get_active_draft,
    get_draft_by_token,
    get_or_create_draft,
    reset_group,
    set_flag,
    set_nation_name,
)
from app.services.user_service import get_user
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from app.utils.validators import (
    generate_unique_currency_code,
    validate_nation_name,
)

logger = logging.getLogger(__name__)

founder_router = Router(name="founder")

_ADMIN_LINK_RIGHTS = (
    "delete_messages+restrict_members+invite_users+pin_messages+manage_topics"
)
DEFAULT_NATION_FLAG = "🏴"

FOUNDER_FLAG_OPTIONS = {
    DEFAULT_NATION_FLAG,
    "🚩",
    "🏳",
    "🎌",
    "🏁",
    "🇮🇷",
    "🇫🇮",
    "🇺🇸",
    "🇯🇵",
    "🇩🇪",
    "🇧🇷",
    "🇫🇷",
    "🇹🇷",
}


def _status_value(member) -> str:
    return getattr(member.status, "value", member.status)


async def _delete_panel(state: FSMContext, bot: Bot, chat_id: int) -> None:
    data = await state.get_data()
    message_id = data.get("founder_panel_message_id")
    if not message_id:
        return

    try:
        await bot.delete_message(chat_id, int(message_id))
    except Exception:
        logger.debug("Could not delete founder panel", exc_info=True)

    await state.update_data(founder_panel_message_id=None)


async def _send_panel(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup,
) -> Message:
    await _delete_panel(state, message.bot, message.chat.id)
    panel = await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )
    await state.update_data(founder_panel_message_id=panel.message_id)
    return panel


async def _edit_panel(
    call: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup,
) -> None:
    if call.message is None:
        return

    try:
        await call.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        await state.update_data(founder_panel_message_id=call.message.message_id)
        return
    except TelegramBadRequest:
        logger.debug("Could not edit founder panel", exc_info=True)

    await _send_panel(call.message, state, text, reply_markup)


async def _set_state_for_draft(state: FSMContext, status: str) -> None:
    mapping = {
        "WAITING_GROUP": FounderStates.WAITING_GROUP_ADMIN,
        "GROUP_READY": FounderStates.SET_NATION_NAME,
        "NAMING": FounderStates.SET_NATION_NAME,
        "FLAG": FounderStates.SELECT_FLAG,
        "REVIEW": FounderStates.CONFIRM,
    }
    target = mapping.get(status)
    if target is not None:
        await state.set_state(target)


def _group_link(bot_username: str, token: str) -> str:
    return (
        f"https://t.me/{bot_username}"
        f"?startgroup=founder_{token}"
        f"&admin={_ADMIN_LINK_RIGHTS}"
    )


def _group_step_text(group_title: str | None = None) -> str:
    title = (
        f"\n📍 گروه انتخاب‌شده: <b>{html.escape(group_title)}</b>\n"
        if group_title
        else ""
    )
    return (
        "🏛 <b>تأسیس ملت</b>\n\n"
        "اول پایتخت ملتت رو مشخص کن.\n"
        "ربات رو به گروهت اضافه کن و اجازه ادمین شدنش رو بده."
        f"{title}\n"
        "بعد روی «بررسی گروه» بزن."
    )


def _name_step_text(group_title: str) -> str:
    return (
        "🏛 <b>مرحله ۲ · نام ملت</b>\n\n"
        f"📍 پایتخت: <b>{html.escape(group_title)}</b>\n\n"
        "اسم ملت رو به انگلیسی وارد کن.\n"
        "۲ تا ۲۰ کاراکتر، فقط حروف انگلیسی و فاصله."
    )


def _flag_step_text(
    nation_name: str,
    currency_code: str,
    group_title: str,
) -> str:
    return (
        "🚩 <b>مرحله ۳ · هویت ملت</b>\n\n"
        f"🏛 نام ملت: <b>{html.escape(nation_name)}</b>\n"
        f"💱 کد ارز: <b>{html.escape(currency_code)}</b>\n"
        f"📍 پایتخت: <b>{html.escape(group_title)}</b>\n\n"
        "پرچم فقط ظاهر ملت رو مشخص می‌کنه و روی اقتصاد بازی اثری نداره."
    )


def _review_text(draft) -> str:
    return (
        "📜 <b>مرحله ۴ · بررسی نهایی</b>\n\n"
        f"{html.escape(draft.flag_emoji or DEFAULT_NATION_FLAG)} "
        f"<b>{html.escape(draft.nation_name or 'بدون نام')}</b>\n\n"
        f"💱 ارز رسمی: <code>{html.escape(draft.currency_code or '---')}</code>\n"
        f"📍 پایتخت: <b>{html.escape(draft.group_title or '---')}</b>\n\n"
        "با تأیید، این گروه به عنوان پایتخت ملت ثبت میشه و "
        "موجودی اولیه بنیان‌گذار ایجاد میشه.\n\n"
        "بعد از تأیید، ملت واقعاً ساخته میشه."
    )


async def _get_user_or_none(user_id: int) -> User | None:
    async with async_session() as session:
        async with session.begin():
            return await get_user(session, user_id)


async def _verify_group(
    bot: Bot,
    *,
    group_id: int,
    founder_user_id: int,
) -> tuple[bool, str]:
    try:
        bot_member = await bot.get_chat_member(group_id, bot.id)
        founder_member = await bot.get_chat_member(group_id, founder_user_id)
    except Exception:
        logger.exception(
            "Founder group verification failed | group_id=%s founder=%s",
            group_id,
            founder_user_id,
        )
        return False, "⚠️ وضعیت گروه قابل بررسی نیست. دوباره امتحان کن."

    bot_status = _status_value(bot_member)
    founder_status = _status_value(founder_member)

    if bot_status not in {"administrator", "creator"}:
        return False, "⚠️ ربات هنوز ادمین این گروه نیست."

    if founder_status not in {"administrator", "creator"}:
        return False, "⚠️ برای تأسیس ملت باید خودت ادمین یا مالک گروه باشی."

    return True, ""


async def _show_group_step(
    message: Message,
    state: FSMContext,
    bot: Bot,
    draft,
    *,
    edit_call: CallbackQuery | None = None,
) -> None:
    me = await bot.get_me()
    markup = add_to_group_keyboard(
        _group_link(me.username or "", draft.launch_token)
    )
    text = _group_step_text(draft.group_title)

    if edit_call is not None:
        await _edit_panel(edit_call, state, text, markup)
    else:
        await _send_panel(message, state, text, markup)


async def _show_name_step(
    message: Message,
    state: FSMContext,
    draft,
    *,
    edit_call: CallbackQuery | None = None,
) -> None:
    markup = founder_name_step_keyboard()
    text = _name_step_text(draft.group_title or "گروه")

    if edit_call is not None:
        await _edit_panel(edit_call, state, text, markup)
    else:
        await _send_panel(message, state, text, markup)


async def _show_flag_step(
    message: Message,
    state: FSMContext,
    draft,
    *,
    edit_call: CallbackQuery | None = None,
) -> None:
    markup = founder_flag_selection_keyboard()
    text = _flag_step_text(
        draft.nation_name or "---",
        draft.currency_code or "---",
        draft.group_title or "گروه",
    )

    if edit_call is not None:
        await _edit_panel(edit_call, state, text, markup)
    else:
        await _send_panel(message, state, text, markup)


async def _show_review_step(
    message: Message,
    state: FSMContext,
    draft,
    *,
    edit_call: CallbackQuery | None = None,
) -> None:
    markup = founder_review_keyboard()
    text = _review_text(draft)

    if edit_call is not None:
        await _edit_panel(edit_call, state, text, markup)
    else:
        await _send_panel(message, state, text, markup)


@founder_router.callback_query(
    F.data == "found_nation",
    StateFilter(None, OnboardingStates.SELECT_NATION),
)
async def start_founder(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    user = await _get_user_or_none(call.from_user.id)
    if user is None or not (user.username or "").strip():
        await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
        return

    try:
        async with async_session() as session:
            draft = await get_or_create_draft(session, call.from_user.id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await _set_state_for_draft(state, draft.status)
    await call.answer()

    if call.message is None:
        return

    if draft.status == "WAITING_GROUP":
        await _show_group_step(call.message, state, bot, draft, edit_call=call)
        return

    if draft.status in {"GROUP_READY", "NAMING"}:
        await _show_name_step(call.message, state, draft, edit_call=call)
        return

    if draft.status == "FLAG":
        await _show_flag_step(call.message, state, draft, edit_call=call)
        return

    if draft.status == "REVIEW":
        await _show_review_step(call.message, state, draft, edit_call=call)
        return

    await call.answer(
        "⚠️ وضعیت تأسیس قابل ادامه نیست. دوباره شروع کن.",
        show_alert=True,
    )


@founder_router.my_chat_member()
async def founder_group_status_changed(
    event: ChatMemberUpdated,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return

    new_status = _status_value(event.new_chat_member)
    if new_status not in {"administrator", "creator"}:
        return

    actor = event.from_user
    if actor is None:
        return

    async with async_session() as session:
        async with session.begin():
            draft = await session.scalar(
                select(NationFoundingDraft)
                .where(
                    NationFoundingDraft.founder_user_id == actor.id,
                    NationFoundingDraft.status == "WAITING_GROUP",
                )
                .order_by(NationFoundingDraft.id.desc())
                .limit(1)
            )

    if draft is None:
        return

    # Do not bind the group from this event. The exact random startgroup token
    # is the only authoritative group-binding event.
    try:
        await bot.send_message(
            actor.id,
            (
                "✅ ربات ادمین شد.\n"
                "برای اتصال همین گروه به فرآیند تأسیس، از لینک «➕ افزودن ربات به گروه» "
                "در پنل تأسیس دوباره استفاده کن."
            ),
        )
    except Exception:
        logger.debug(
            "Could not notify founder after bot promotion",
            exc_info=True,
        )


@founder_router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^/start(?:@[^ ]+)?\s+founder_([A-Za-z0-9_-]+)$"),
)
async def group_founder_start(
    message: Message,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    match = re.match(
        r"^/start(?:@[^ ]+)?\s+founder_([A-Za-z0-9_-]+)$",
        message.text or "",
    )
    if not match or message.from_user is None:
        return

    token = match.group(1)

    async with async_session() as session:
        async with session.begin():
            draft = await get_draft_by_token(session, token)

    if draft is None:
        return

    if draft.founder_user_id != message.from_user.id:
        await message.answer("⚠️ این دعوت برای بنیان‌گذار دیگری ساخته شده.")
        return

    ok, error = await _verify_group(
        bot,
        group_id=message.chat.id,
        founder_user_id=message.from_user.id,
    )
    if not ok:
        await message.answer(error)
        return

    try:
        async with async_session() as session:
            bound = await bind_group(
                session,
                founder_user_id=message.from_user.id,
                token=token,
                group_id=message.chat.id,
                group_title=message.chat.title or "گروه بدون نام",
                group_username=getattr(message.chat, "username", None),
                group_type=message.chat.type,
            )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    private_state = dispatcher.fsm.get_context(
        bot=bot,
        chat_id=message.from_user.id,
        user_id=message.from_user.id,
    )
    await private_state.set_state(FounderStates.SET_NATION_NAME)

    try:
        await bot.send_message(
            message.from_user.id,
            (
                "✅ <b>گروه پایتخت متصل شد.</b>\n"
                f"📍 پایتخت: <b>{html.escape(bound.group_title or 'گروه')}</b>\n\n"
                "حالا اسم ملت رو بفرست."
            ),
            reply_markup=founder_cancel_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        logger.debug(
            "Could not notify founder after group start",
            exc_info=True,
        )

    try:
        await bot.send_message(
            message.chat.id,
            "✅ اتصال پایتخت انجام شد. ادامه تأسیس در گفت‌وگوی خصوصی بنیان‌گذار انجام میشه.",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.debug(
            "Could not announce private continuation in group",
            exc_info=True,
        )


@founder_router.callback_query(
    F.data == "founder_check_group",
    StateFilter(FounderStates.WAITING_GROUP_ADMIN),
)
async def check_founder_group(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    async with async_session() as session:
        async with session.begin():
            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=True,
            )
            if draft is None:
                await call.answer(
                    "⚠️ فرآیند تأسیس منقضی شده. دوباره شروع کن.",
                    show_alert=True,
                )
                return

            if draft.group_id is None:
                await call.answer(
                    "⚠️ هنوز گروهی به این فرآیند متصل نشده.",
                    show_alert=True,
                )
                return

            group_id = draft.group_id

    ok, error = await _verify_group(
        bot,
        group_id=group_id,
        founder_user_id=call.from_user.id,
    )
    if not ok:
        await call.answer(error, show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            existing_nation = await session.scalar(
                select(Nation)
                .where(
                    Nation.group_id == group_id,
                    Nation.is_active.is_(True),
                )
                .with_for_update()
                .limit(1)
            )
            if existing_nation is not None:
                draft = await get_active_draft(
                    session,
                    call.from_user.id,
                    lock=True,
                )
                if draft is not None:
                    draft.status = "CANCELLED"
                await call.answer(
                    "⚠️ این گروه قبلاً پایتخت یک ملت شده.",
                    show_alert=True,
                )
                return

            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=True,
            )
            if draft is None:
                await call.answer(
                    "⚠️ فرآیند تأسیس منقضی شده. دوباره شروع کن.",
                    show_alert=True,
                )
                return

            draft.status = "GROUP_READY"
            draft.expires_at = datetime.utcnow() + timedelta(minutes=30)

    await state.set_state(FounderStates.SET_NATION_NAME)
    await call.answer("✅ گروه تأیید شد.")

    if call.message:
        await _show_name_step(call.message, state, draft, edit_call=call)


@founder_router.message(
    StateFilter(
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_NATION_NAME,
        FounderStates.SELECT_FLAG,
        FounderStates.CONFIRM,
    ),
    CommandStart(),
)
async def founder_start_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    user = await _get_user_or_none(message.from_user.id)
    if user is not None:
        await show_dashboard(message, user)
    else:
        await message.answer("👋 برای شروع، /start رو دوباره بزن.")


@founder_router.message(
    FounderStates.SET_NATION_NAME,
    F.text,
)
async def receive_nation_name(
    message: Message,
    state: FSMContext,
) -> None:
    value = " ".join((message.text or "").strip().split())
    valid, error = validate_nation_name(value)
    if not valid:
        await _send_panel(
            message,
            state,
            error,
            founder_cancel_keyboard(),
        )
        return

    try:
        async with async_session() as session:
            async with session.begin():
                code = await generate_unique_currency_code(value, session)

        async with async_session() as session:
            draft = await set_nation_name(
                session,
                founder_user_id=message.from_user.id,
                nation_name=value,
                currency_code=code,
            )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await state.set_state(FounderStates.SELECT_FLAG)
    await _show_flag_step(message, state, draft)


@founder_router.callback_query(
    F.data.startswith("founder_flag:"),
    StateFilter(FounderStates.SELECT_FLAG),
)
async def select_founder_flag(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    raw = (call.data or "").split(":", 1)[1] if call.data else ""
    flag = DEFAULT_NATION_FLAG if raw == "default" else raw

    if flag not in FOUNDER_FLAG_OPTIONS:
        await call.answer("⚠️ پرچم معتبر نیست.", show_alert=True)
        return

    try:
        async with async_session() as session:
            draft = await set_flag(
                session,
                founder_user_id=call.from_user.id,
                flag_emoji=flag,
            )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.set_state(FounderStates.CONFIRM)
    await call.answer()
    if call.message is not None:
        await _show_review_step(call.message, state, draft, edit_call=call)


@founder_router.callback_query(
    F.data == "founder_edit_name",
    StateFilter(FounderStates.SELECT_FLAG, FounderStates.CONFIRM),
)
async def founder_edit_name(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    async with async_session() as session:
        async with session.begin():
            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=True,
            )
            if draft is not None:
                draft.status = "NAMING"

    if draft is None:
        await call.answer("⚠️ اطلاعات تأسیس پیدا نشد.", show_alert=True)
        return

    await state.set_state(FounderStates.SET_NATION_NAME)
    await call.answer()
    if call.message is not None:
        await _show_name_step(call.message, state, draft, edit_call=call)


@founder_router.callback_query(
    F.data == "founder_edit_flag",
    StateFilter(FounderStates.CONFIRM),
)
async def founder_edit_flag(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    async with async_session() as session:
        async with session.begin():
            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=True,
            )
            if draft is not None:
                draft.status = "FLAG"

    if draft is None:
        await call.answer("⚠️ اطلاعات تأسیس پیدا نشد.", show_alert=True)
        return

    await state.set_state(FounderStates.SELECT_FLAG)
    await call.answer()
    if call.message is not None:
        await _show_flag_step(call.message, state, draft, edit_call=call)


@founder_router.callback_query(
    F.data == "founder_edit_group",
    StateFilter(FounderStates.CONFIRM),
)
async def founder_edit_group(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    try:
        async with async_session() as session:
            draft = await reset_group(
                session,
                founder_user_id=call.from_user.id,
            )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
    await call.answer()
    if call.message is not None:
        await _show_group_step(call.message, state, bot, draft, edit_call=call)


@founder_router.callback_query(
    F.data == "confirm_found",
    StateFilter(FounderStates.CONFIRM),
)
async def confirm_founder(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    await call.answer("در حال تأسیس ملت...")

    try:
        async with async_session() as session:
            nation, group_id = await finalize_draft(
                session,
                founder_user_id=call.from_user.id,
            )
    except ValueError as exc:
        message = str(exc)

        if "کد ارز" in message:
            try:
                async with async_session() as session:
                    async with session.begin():
                        draft = await get_active_draft(
                            session,
                            call.from_user.id,
                            lock=True,
                        )
                        if draft is None or not draft.nation_name:
                            raise ValueError(message)

                        code = await generate_unique_currency_code(
                            draft.nation_name,
                            session,
                        )
                        draft.currency_code = code
                        draft.expires_at = datetime.utcnow() + timedelta(minutes=30)
            except Exception:
                await call.answer(message, show_alert=True)
                return

            await state.set_state(FounderStates.CONFIRM)
            if call.message is not None:
                await _show_review_step(
                    call.message,
                    state,
                    draft,
                    edit_call=call,
                )
            return

        await call.answer(message, show_alert=True)
        return
    except Exception:
        logger.exception(
            "Founder nation finalization failed | founder=%s",
            call.from_user.id,
        )
        await call.answer(
            "⚠️ تأسیس انجام نشد. اطلاعاتت حفظ شده. دوباره امتحان کن.",
            show_alert=True,
        )
        return

    await state.clear()

    if call.message is not None:
        user = await _get_user_or_none(call.from_user.id)
        if user is not None:
            await show_dashboard(
                call.message,
                user,
                replace_inline=True,
                bot=bot,
                display_user=call.from_user,
            )

    try:
        nation_name = html.escape(nation.name)
        flag = html.escape(nation.flag_emoji or DEFAULT_NATION_FLAG)
        await bot.send_message(
            group_id,
            (
                "🎉 <b>ملت جدید تأسیس شد!</b>\n"
                f"{flag} <b>{nation_name}</b>\n"
                f"💱 ارز رسمی: <b>{html.escape(nation.currency_code)}</b>\n"
                f"👑 بنیان‌گذار: {html.escape(call.from_user.first_name or 'بنیان‌گذار')}\n"
                "این گروه حالا پایتخت این ملت است."
            ),
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning(
            "Could not announce nation %s in group %s",
            nation.nation_id,
            group_id,
        )


@founder_router.callback_query(
    F.data == "cancel_founder",
    StateFilter(
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_NATION_NAME,
        FounderStates.SELECT_FLAG,
        FounderStates.CONFIRM,
    ),
)
async def cancel_founder(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()

    async with async_session() as session:
        try:
            await cancel_draft(session, call.from_user.id)
        except Exception:
            logger.exception(
                "Could not cancel founding draft | founder=%s",
                call.from_user.id,
            )

    if call.message is not None:
        try:
            await call.message.delete()
        except Exception:
            logger.debug("Could not delete cancelled founder panel", exc_info=True)

        user = await _get_user_or_none(call.from_user.id)
        if user is not None:
            await show_dashboard(call.message, user)

    await call.answer("❌ تأسیس ملت لغو شد.")


@founder_router.message(
    FounderStates.WAITING_GROUP_ADMIN,
    F.text,
)
async def founder_waiting_group_fallback(
    message: Message,
) -> None:
    await message.answer(
        "👆 اول ربات رو به گروه اضافه و ادمین کن، بعد «بررسی گروه» رو بزن."
    )


@founder_router.message(
    FounderStates.SELECT_FLAG,
    F.text,
)
async def founder_flag_fallback(
    message: Message,
) -> None:
    await message.answer(
        "🚩 پرچم رو فقط از دکمه‌های همین صفحه انتخاب کن."
    )


@founder_router.message(
    FounderStates.CONFIRM,
    F.text,
)
async def founder_review_fallback(
    message: Message,
) -> None:
    await message.answer(
        "📜 از دکمه‌های تأیید یا ویرایش همین صفحه استفاده کن."
    )
