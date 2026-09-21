from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ButtonStyle
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy import select

from app.database.models import Nation, User
from app.database.session import async_session
from app.handlers.start import show_dashboard
from app.keyboards.inline import (
    add_to_group_keyboard,
    founder_cancel_keyboard,
    founder_currency_confirm_keyboard,
    founder_flag_selection_keyboard,
    founder_name_step_keyboard,
    nation_founder_announcement_keyboard,
    founder_review_keyboard,
)
from app.services.nation.founder_service import (
    bind_group,
    cancel_draft,
    finalize_draft,
    get_active_draft,
    get_draft_by_token,
    get_or_create_draft,
    reset_group,
    set_currency_code,
    set_flag,
    set_nation_name,
)
from app.services.user_service import get_user, username_exists
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name
from app.utils.validators import validate_nation_name

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


class FounderPanel:
    _STATE_KEY = "founder_panel_message_id"

    def __init__(self, state: FSMContext, bot: Bot) -> None:
        self.state = state
        self.bot = bot

    async def delete(self, chat_id: int) -> None:
        data = await self.state.get_data()
        message_id = data.get(self._STATE_KEY)
        if not message_id:
            return
        try:
            await self.bot.delete_message(chat_id, int(message_id))
        except Exception:
            logger.debug("Could not delete founder panel", exc_info=True)
        await self.state.update_data(**{self._STATE_KEY: None})

    async def send(self, message: Message, text: str, reply_markup) -> Message:
        await self.delete(message.chat.id)
        panel = await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        await self.state.update_data(**{self._STATE_KEY: panel.message_id})
        return panel

    async def edit(self, call: CallbackQuery, text: str, reply_markup) -> None:
        if call.message is None:
            return
        try:
            await call.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            await self.state.update_data(
                **{self._STATE_KEY: call.message.message_id}
            )
            return
        except TelegramBadRequest:
            logger.debug("Could not edit founder panel", exc_info=True)
        await self.send(call.message, text, reply_markup)


async def _send_panel(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup,
) -> Message:
    return await FounderPanel(state, message.bot).send(message, text, reply_markup)


async def _edit_panel(
    call: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup,
) -> None:
    if call.message is None:
        return
    await FounderPanel(state, call.message.bot).edit(call, text, reply_markup)


async def _set_state_for_draft(state: FSMContext, status: str) -> None:
    mapping = {
        "WAITING_GROUP": FounderStates.WAITING_GROUP_ADMIN,
        "GROUP_READY": FounderStates.SET_NATION_NAME,
        "NAMING": FounderStates.SET_CURRENCY_CODE,
        "FLAG": FounderStates.SELECT_FLAG,
        "REVIEW": FounderStates.CONFIRM,
    }
    target = mapping.get(status)
    if target is None:
        logger.error("Unsupported founder draft status: %r", status)
        raise ValueError(f"Unsupported founder draft status: {status!r}")
    await state.set_state(target)


def _group_link(bot_username: str, token: str) -> str:
    return (
        f"https://t.me/{bot_username}"
        f"?startgroup=founder_{token}"
        f"&admin={_ADMIN_LINK_RIGHTS}"
    )


def _founder_intro_text(user_mention: str) -> str:
    return (
        f"{user_mention}، داری تاریخ میسازی.\n"
        "\n"
        "هر ملت یه گروه تلگرامیه.\n"
        "هر گروه یه اقتصاد مستقل داره.\n"
        "اعضای گروهت شهروندان ملتت میشن.\n\n"
        "<blockquote>⁠</blockquote>\n"
        "<b>قبل از شروع بدون:</b>\n\n"
        "👑 تو به عنوان بنیان‌گذار ثبت میشی\n"
        "💰 موجودی اولیه: <b>۱,۰۰۰ واحد</b> ارز ملت\n"
        "🎖 نشان «بنیان‌گذار» برای همیشه روی پروفایلت\n"
        "📊 کنترل کامل تنظیمات اقتصادی ملت\n\n"
        "<blockquote>⁠</blockquote>\n"
        "<i>گروه تلگرامی داری؟</i>"
    )


def _no_group_text(user_mention: str) -> str:
    return (
        f"{user_mention}، دو راه داری:\n\n"
        "<blockquote>⁠</blockquote>\n"
        "<b>راه اول — همین الان گروه بساز:</b>\n"
        "تلگرام رو باز کن، یه گروه جدید بساز،\n"
        "بعد برگرد اینجا.\n\n"
        "<b>راه دوم — فعلاً به عنوان Player شروع کن:</b>\n"
        "بعداً که گروه ساختی میتونی از تنظیمات\n"
        "ملت بسازی."
    )


def _username_step_text(user_mention: str) -> str:
    return (
        f"{user_mention}، اول یه اسم معامله‌گر\n"
        "شخصی برای خودت انتخاب کن.\n\n"
        "این اسم جداست از اسم ملتت.\n\n"
        "<b>اسم معامله‌گرت رو بنویس:</b>\n"
        "<i>بین ۳ تا ۲۰ کاراکتر، بدون فاصله و @</i>"
    )


def _group_identified_text(draft, member_count: int) -> str:
    return (
        "✅ <b>گروه شناسایی شد!</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📍 گروه: <b>{html.escape(draft.group_title or 'گروه')}</b>\n"
        f"👥 اعضا: <b>{member_count}</b> نفر\n"
        f"🆔 شناسه: <code>{draft.group_id}</code>\n\n"
        "<blockquote>⁠</blockquote>\n"
        "حالا باید واحد پول ملتت رو نام‌گذاری کنی.\n\n"
        "<b>یه کد کوتاه ۳ تا ۴ حرفی انگلیسی:</b>\n"
        "<i>مثال: AZD — IRN — PRS — GLX — VLT — GOLD</i>\n\n"
        "⚠️ بعد از ثبت قابل تغییر نیست."
    )


def _currency_step_text(draft) -> str:
    return (
        "💰 <b>کد ارز ملت</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📍 گروه: <b>{html.escape(draft.group_title or 'گروه')}</b>\n\n"
        "یه کد کوتاه ۳ تا ۴ حرفی انگلیسی انتخاب کن.\n"
        "<i>مثال: AZD — IRN — PRS — GLX — VLT — GOLD</i>\n\n"
        "⚠️ بعد از ثبت قابل تغییر نیست."
    )


def _f5_text(draft, founder_mention: str) -> str:
    return (
        "💰 <b>تأیید کد ارز</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"{founder_mention}:\n\n"
        f"کد انتخابی: <code>{html.escape(draft.currency_code or '---')}</code>\n"
        f"ملت: <b>{html.escape(draft.group_title or '---')}</b>\n\n"
        "<blockquote>⁠</blockquote>\n"
        "<b>بعد از تأیید:</b>\n"
        f"- ارز <code>{html.escape(draft.currency_code or '---')}</code> در بازارهای OPEX ثبت میشه\n"
        "- نرخ اولیه: <b>۱.۰۰۰۰ دلار</b>\n"
        f"- موجودی اولیه تو: <b>۱,۰۰۰ {html.escape(draft.currency_code or '---')}</b>\n\n"
        "⚠️ بعد از تأیید کد قابل تغییر نیست."
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
    F.data.in_({"start_founder", "found_nation"}),
)
async def start_founder(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    await state.clear()
    await call.answer()
    if call.message is None:
        return

    mention = f'<a href="tg://user?id={call.from_user.id}">{html.escape(call.from_user.first_name or "بنیان‌گذار")}</a>'
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ بله، گروه دارم",
                    callback_data="founder_has_group",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="❌ گروه ندارم",
                    callback_data="founder_no_group",
                    style=ButtonStyle.DANGER,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_to_dashboard",
                )
            ],
        ]
    )
    await call.message.edit_text(
        "🏛 <b>ساخت ملت جدید</b>\n"
        "<blockquote>⁠</blockquote>\n"
        + _founder_intro_text(mention),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@founder_router.callback_query(F.data == "founder_no_group", StateFilter(None))
async def founder_no_group(call: CallbackQuery) -> None:
    mention = f'<a href="tg://user?id={call.from_user.id}">{html.escape(call.from_user.first_name or "بنیان‌گذار")}</a>'
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 گروه ساختم، ادامه بده",
                    callback_data="founder_has_group",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="💹 فعلاً Player میشم",
                    callback_data="start_player",
                    style=ButtonStyle.PRIMARY,
                ),
            ]
        ],
    )
    if call.message:
        await call.message.edit_text(
            "💡 <b>بدون گروه هم میشه شروع کرد!</b>\n"
            "<blockquote>⁠</blockquote>\n"
            + _no_group_text(mention),
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await call.answer()


@founder_router.callback_query(F.data == "founder_has_group", StateFilter(None))
async def founder_has_group(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    user = await _get_user_or_none(call.from_user.id)
    if user is not None and (user.username or "").strip():
        try:
            async with async_session() as session:
                draft = await get_or_create_draft(session, call.from_user.id)
        except ValueError as exc:
            await call.answer(str(exc), show_alert=True)
            return
        await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
        await call.answer()
        if call.message:
            await _show_group_step(call.message, state, bot, draft, edit_call=call)
        return

    await state.set_state(FounderStates.SET_USERNAME_FOUNDER)
    await state.update_data(founder_pending_group=True)
    if call.message:
        await call.message.edit_text(
            "👑 <b>ثبت‌نام بنیان‌گذار</b>\n"
            "<blockquote>⁠</blockquote>\n"
            + _username_step_text(
                f'<a href="tg://user?id={call.from_user.id}">{html.escape(call.from_user.first_name or "بنیان‌گذار")}</a>'
            ),
            reply_markup=founder_cancel_keyboard(),
            parse_mode="HTML",
        )
    await call.answer()


@founder_router.callback_query(F.data == "start_player", StateFilter(None))
async def founder_start_player(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await _get_user_or_none(call.from_user.id)
    if user is None:
        from app.handlers.start import start_game_button
        # Reuse the canonical player onboarding path.
        await start_game_button(call.message, state)
    elif call.message:
        await show_dashboard(
            call.message,
            user,
            replace_inline=True,
            bot=call.bot,
            display_user=call.from_user,
        )
    await call.answer()


@founder_router.my_chat_member()
async def founder_group_status_changed(
    event: ChatMemberUpdated,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return

    actor = event.from_user
    if actor is None:
        return

    new_status = _status_value(event.new_chat_member)

    async with async_session() as session:
        draft = await get_active_draft(session, actor.id, lock=False)

    if draft is None:
        return

    if draft.group_id not in (None, event.chat.id):
        return

    if draft.group_id == event.chat.id and draft.status == "NAMING":
        return

    if new_status not in {"administrator", "creator"}:
        if draft.group_id in (None, event.chat.id):
            try:
                await bot.send_message(
                    event.chat.id,
                    f"⚠️ برای راه‌اندازی OPEX MONEY باید ربات را ادمین کنی.",
                )
            except Exception:
                logger.debug("Could not send non-admin group warning", exc_info=True)
        return

    if draft.founder_user_id != actor.id:
        return

    ok, error = await _verify_group(
        bot,
        group_id=event.chat.id,
        founder_user_id=actor.id,
    )
    if not ok:
        try:
            await bot.send_message(event.chat.id, error)
        except Exception:
            logger.debug("Could not send founder group verification error", exc_info=True)
        return

    try:
        async with async_session() as session:
            bound = await bind_group(
                session,
                founder_user_id=actor.id,
                token=draft.launch_token,
                group_id=event.chat.id,
                group_title=event.chat.title or "گروه بدون نام",
                group_username=getattr(event.chat, "username", None),
                group_type=event.chat.type,
            )
            bound.nation_name = bound.group_title or "گروه"
            bound.status = "NAMING"
            await session.flush()

        member_count = await bot.get_chat_member_count(event.chat.id)
    except Exception as exc:
        logger.exception("Founder auto-bind failed | founder=%s group=%s", actor.id, event.chat.id)
        try:
            await bot.send_message(event.chat.id, f"⚠️ راه‌اندازی ملت انجام نشد: {html.escape(str(exc))}", parse_mode="HTML")
        except Exception:
            logger.debug("Could not report founder auto-bind failure", exc_info=True)
        return

    private_state = dispatcher.fsm.get_context(
        bot=bot,
        chat_id=actor.id,
        user_id=actor.id,
    )
    await private_state.set_state(FounderStates.SET_CURRENCY_CODE)
    await private_state.update_data(founder_group_id=event.chat.id)
    await FounderPanel(private_state, bot).delete(actor.id)

    try:
        await bot.send_message(
            actor.id,
            _group_identified_text(bound, member_count),
            reply_markup=founder_cancel_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        logger.debug("Could not notify founder after group promotion", exc_info=True)


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

    if draft.group_id == message.chat.id and draft.status == "NAMING":
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
    await private_state.set_state(FounderStates.SET_CURRENCY_CODE)
    await private_state.update_data(founder_group_id=bound.group_id)

    try:
        member_count = await bot.get_chat_member_count(bound.group_id)
    except Exception:
        member_count = 0

    try:
        await bot.send_message(
            message.from_user.id,
            _group_identified_text(bound, member_count),
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
    data = await state.get_data()
    group_id = data.get("founder_group_id")

    if group_id is None:
        async with async_session() as session:
            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=False,
            )
            if draft is None or draft.group_id is None:
                await call.answer(
                    "⚠️ هنوز گروهی به این فرآیند متصل نشده.",
                    show_alert=True,
                )
                return
            group_id = int(draft.group_id)
    else:
        group_id = int(group_id)

    ok, error = await _verify_group(
        bot,
        group_id=group_id,
        founder_user_id=call.from_user.id,
    )
    if not ok:
        await call.answer(error, show_alert=True)
        return

    conflict_message = None

    async with async_session() as session:
        async with session.begin():
            draft = await get_active_draft(
                session,
                call.from_user.id,
                lock=True,
            )
            if draft is None:
                conflict_message = "⚠️ فرآیند تأسیس منقضی شده. دوباره شروع کن."
            elif draft.group_id != group_id:
                conflict_message = "⚠️ گروه این فرآیند تغییر کرده. دوباره از لینک تأسیس استفاده کن."
            else:
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
                    draft.status = "CANCELLED"
                    await session.flush()
                    conflict_message = "⚠️ این گروه قبلاً پایتخت یک ملت شده."
                else:
                    draft.nation_name = draft.group_title or "گروه"
                    draft.status = "NAMING"
                    draft.expires_at = datetime.utcnow() + timedelta(minutes=30)
                    await session.flush()

    if conflict_message is not None:
        await call.answer(conflict_message, show_alert=True)
        return

    await state.set_state(FounderStates.SET_CURRENCY_CODE)
    await state.update_data(founder_group_id=group_id)
    await call.answer("✅ گروه شناسایی شد.")

    try:
        member_count = await bot.get_chat_member_count(group_id)
    except Exception:
        member_count = 0

    if call.message:
        await _edit_panel(
            call,
            state,
            _group_identified_text(draft, member_count),
            founder_cancel_keyboard(),
        )


@founder_router.message(
    StateFilter(
        FounderStates.WAITING_GROUP_ADMIN,
        FounderStates.SET_USERNAME_FOUNDER,
        FounderStates.SET_NATION_NAME,
        FounderStates.SET_CURRENCY_CODE,
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
    FounderStates.SET_USERNAME_FOUNDER,
    F.text,
)
async def receive_founder_username(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    value = (message.text or "").strip()
    if " " in value or "@" in value or not is_valid_trader_name(value) or is_blocked_trader_name(value):
        await _send_panel(
            message,
            state,
            "🔴 <b>نام قابل قبول نیست.</b>\n\n۳ تا ۲۰ کاراکتر، بدون فاصله و @.",
            founder_cancel_keyboard(),
        )
        return

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id, with_for_update=True)
            if await username_exists(session, value) and (
                user is None or user.username != value
            ):
                await message.answer(
                    "🔴 <b>این نام قبلاً ثبت شده.</b>\n\nیک نام دیگر برای معامله‌گرت انتخاب کن.",
                    reply_markup=founder_cancel_keyboard(),
                    parse_mode="HTML",
                )
                return

            if user is None:
                user = User(
                    user_id=message.from_user.id,
                    username=value,
                    home_nation_id=None,
                    balance=0,
                    xr_balance=0,
                    role="player",
                )
                session.add(user)
                await session.flush()
            else:
                user.username = value

    await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
    await state.update_data(founder_pending_group=None)
    try:
        async with async_session() as session:
            draft = await get_or_create_draft(session, message.from_user.id)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=founder_cancel_keyboard(), parse_mode="HTML")
        return
    await _show_group_step(message, state, bot, draft)


@founder_router.message(
    FounderStates.SET_CURRENCY_CODE,
    F.text,
)
async def receive_currency_code(
    message: Message,
    state: FSMContext,
) -> None:
    value = (message.text or "").strip()
    async with async_session() as session:
        try:
            draft = await set_currency_code(
                session,
                founder_user_id=message.from_user.id,
                currency_code=value,
            )
        except ValueError as exc:
            await message.answer(
                str(exc),
                reply_markup=founder_cancel_keyboard(),
                parse_mode="HTML",
            )
            return

    await state.clear()
    await message.answer(
        _f5_text(
            draft,
            f'<a href="tg://user?id={message.from_user.id}">{html.escape(message.from_user.first_name or "بنیان‌گذار")}</a>',
        ),
        reply_markup=founder_currency_confirm_keyboard(),
        parse_mode="HTML",
    )


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
            draft = await set_nation_name(
                session,
                founder_user_id=message.from_user.id,
                nation_name=value,
            )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await state.set_state(FounderStates.SET_CURRENCY_CODE)
    await _send_panel(
        message,
        state,
        _currency_step_text(draft),
        founder_cancel_keyboard(),
    )


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
    StateFilter(None, FounderStates.SELECT_FLAG, FounderStates.CONFIRM),
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
    StateFilter(None, FounderStates.CONFIRM),
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
    StateFilter(None, FounderStates.CONFIRM),
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
    F.data == "founder_recode",
    StateFilter(None),
)
async def founder_recode(
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
            if draft is None:
                await call.answer("⚠️ اطلاعات تأسیس پیدا نشد.", show_alert=True)
                return
            draft.status = "NAMING"
            draft.expires_at = datetime.utcnow() + timedelta(minutes=30)
    await state.set_state(FounderStates.SET_CURRENCY_CODE)
    await call.answer()
    if call.message:
        await call.message.edit_text(
            _currency_step_text(draft),
            reply_markup=founder_cancel_keyboard(),
            parse_mode="HTML",
        )


@founder_router.callback_query(
    F.data == "confirm_found",
    StateFilter(None, FounderStates.CONFIRM),
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
        await call.answer(str(exc), show_alert=True)
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
        f6_text = (
            f"👑 <b>ملت «{html.escape(nation.name)}» ساخته شد!</b>\n"
            "<blockquote>⁠</blockquote>\n"
            f"👤 بنیان‌گذار: <b>{html.escape(call.from_user.first_name or 'بنیان‌گذار')}</b>\n\n"
            "<blockquote>⁠</blockquote>\n"
            f"🏛 ملت: <b>{html.escape(nation.name)}</b>\n"
            f"💰 ارز: <code>{html.escape(nation.currency_code)}</code>\n"
            "📈 نرخ اولیه: <b>۱.۰۰۰۰ دلار</b>\n"
            f"👑 بنیان‌گذار: <b>{html.escape(call.from_user.first_name or 'بنیان‌گذار')}</b>\n"
            f"📅 تاریخ تأسیس: <b>{datetime.utcnow().strftime('%Y/%m/%d')}</b>\n\n"
            "💰 <b>موجودی اولیه‌ات:</b>\n"
            f"<b>۱,۰۰۰ <code>{html.escape(nation.currency_code)}</code></b>\n\n"
            "🎖 نشان «بنیان‌گذار» به پروفایلت اضافه شد — برای همیشه."
        )
        await call.message.edit_text(
            f6_text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📢 اطلاع‌رسانی به گروه",
                            callback_data="founder_announce",
                            style=ButtonStyle.PRIMARY,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⚙️ تنظیمات ملت",
                            callback_data=f"nm:panel:{nation.nation_id}",
                        ),
                        InlineKeyboardButton(
                            text="⚡ شروع معامله",
                            callback_data="market_main",
                            style=ButtonStyle.SUCCESS,
                        ),
                    ],
                ]
            ),
            parse_mode="HTML",
        )

    try:
        me = await bot.get_me()
        announcement = await bot.send_message(
            group_id,
            (
                f"🏛 <b>ملت «{html.escape(nation.name)}» در OPEX ثبت شد!</b>\n"
                "<blockquote>⁠</blockquote>\n"
                "از این لحظه این گروه یه ملت مستقل در بازارهای جهانی OPEX است.\n\n"
                f"💰 واحد پول: <code>{html.escape(nation.currency_code)}</code>\n"
                "📈 نرخ امروز: <b>۱.۰۰۰۰ دلار</b>\n"
                f"👑 بنیان‌گذار: <a href='tg://user?id={call.from_user.id}'>{html.escape(call.from_user.first_name or 'بنیان‌گذار')}</a>\n"
                "<blockquote>⁠</blockquote>\n"
                "<b>چطور بازی کنم؟</b>\n"
                "۱. روی دکمه زیر بزن\n"
                "۲. ربات رو استارت بزن\n"
                f"۳. به ملت «{html.escape(nation.name)}» بپیوند\n"
                "۴. معامله کن و ارزش ارزت رو بالا ببر!"
            ),
            reply_markup=nation_founder_announcement_keyboard(
                me.username or "",
                nation.nation_id,
            ),
            parse_mode="HTML",
        )
        try:
            await bot.pin_chat_message(group_id, announcement.message_id, disable_notification=True)
        except Exception:
            logger.info(
                "Could not pin nation founding announcement | nation=%s group=%s",
                nation.nation_id,
                group_id,
            )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning(
            "Could not announce nation %s in group %s",
            nation.nation_id,
            group_id,
        )

    await asyncio.sleep(1)

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


@founder_router.callback_query(
    F.data == "founder_announce",
    StateFilter(None),
)
async def founder_announce_ack(call: CallbackQuery) -> None:
    await call.answer("📢 اطلاع‌رسانی ملت در گروه انجام شده.", show_alert=True)


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
