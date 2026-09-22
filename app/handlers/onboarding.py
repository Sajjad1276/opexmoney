from __future__ import annotations

import html
import time
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database.models import CurrencyHolding, Nation, Transaction, User
from app.database.session import async_session
from app.utils.ui import (
    close_inline_panel,
    remember_inline_panel,
    safe_edit_caption as _safe_edit_caption,
    safe_edit_text as _safe_edit_text,
    user_mention,
)
from app.keyboards.inline import cancel_keyboard, first_trade_keyboard, nation_selection_keyboard, welcome_keyboard
from app.services.membership_service import sync_registered_user_memberships
from app.handlers.start import cmd_start as restart_flow
from app.handlers.nation_management import _join_user
from app.services.temporal_service import ensure_temporal_profile
from app.services.nation_service import get_nation_rank
from app.services.user_service import get_user, is_fully_registered, username_exists
from app.states.onboarding import OnboardingStates
from app.utils.formatting import fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa, rtl_html
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name

router = Router(name="onboarding")

# Compatibility fallback for older onboarding state; validation is shared with the primary flow.

# Telegram does not expose a text-align control for bot messages. RLM markers
# keep mixed Persian/Latin/emoji lines in an RTL paragraph direction and reduce
# the visual jump to the left caused by mentions, numbers and symbols.
RLM = "\u200f"
ONBOARDING_TIMEOUT = 300
NATIONS_PER_PAGE = 5


def rtl_text(text: str) -> str:
    return "\n".join(f"{RLM}{line}" if line else "" for line in text.split("\n"))


USERNAME_CAPTION = rtl_text("""<b>👤 نام معامله‌گرت رو انتخاب کن</b>

نام نمایش داده می‌شه و بقیه بازیکن‌ها تو رو با همین نام می‌بینن.
3 تا 20 کاراکتر: فارسی، انگلیسی، عدد و خط تیره.
<u>فاصله و علامت‌های دیگر مجاز نیست.</u>""")

INVALID_NAME = rtl_text("""<b>🔴 نام قابل قبول نیست</b>

طول نام باید بین 3 تا 20 کاراکتر باشه.
<u>فقط فارسی، انگلیسی، عدد و خط تیره مجازه.</u>""")

BLOCKED_NAME = rtl_text("""<b>🔴 نام قابل قبول نیست</b>

این نام شامل عبارت نامناسبه.
<u>یک نام مناسب برای معامله‌گرت انتخاب کن.</u>""")

CANCEL_TEXT = rtl_text("""<b>❌ {user_name}، ثبت‌نام لغو شد.</b>

<u>برای شروع دوباره /start رو بزن.</u>""")

NAME_ACCEPTED_TEXT = rtl_text("""✅ <b>نام «{username}» ثبت شد.</b>""")

NO_NATION_TEXT = rtl_text("""🌍 <b>هنوز هیچ ملتی تأسیس نشده.</b>

<u>برو به منوی ملت‌ها تا اولین ملت رو بسازی.</u>""")



def onboarding_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="onboarding_back_name",
                )
            ]
        ]
    )



@router.message(OnboardingStates.SET_USERNAME_PLAYER, CommandStart())
async def restart_onboarding_with_command(message: Message, state: FSMContext) -> None:
    await restart_flow(message, state)


@router.message(
    OnboardingStates.SET_USERNAME_PLAYER,
    F.text.func(
        lambda value: (value or "").strip().casefold() in {"استارت", "شروع", "start"}
    ),
)
async def restart_onboarding_with_text_command(message: Message, state: FSMContext) -> None:
    await restart_flow(message, state)


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(is_blocked_trader_name))
async def reject_blocked_name(message: Message, state: FSMContext) -> None:
    await message.answer(BLOCKED_NAME, parse_mode="HTML")


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(lambda value: not is_valid_trader_name(value or "")))
async def reject_invalid_trader_name(message: Message, state: FSMContext) -> None:
    await message.answer(INVALID_NAME, parse_mode="HTML")


@router.message(OnboardingStates.SET_USERNAME_PLAYER, F.text.func(is_valid_trader_name))
async def accept_valid_name(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()

    async with async_session() as session:
        async with session.begin():
            if await is_fully_registered(session, message.from_user.id):
                user = await get_user(session, message.from_user.id)
            else:
                user = None

    if user is not None:
        await state.clear()
        from app.handlers.dashboard import show_dashboard
        await show_dashboard(message, user)
        return

    if is_blocked_trader_name(username):
        await message.answer(BLOCKED_NAME, parse_mode="HTML")
        return

    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, username):
                existing = await session.get(User, message.from_user.id)
                if existing is None or existing.username != username:
                    duplicate_text = rtl_text(
                        "🔴 <b>این نام قبلاً ثبت شده.</b>\n\n"
                        "<u>یک نام دیگر برای معامله‌گرت انتخاب کن.</u>"
                    )
                    await message.answer(duplicate_text, parse_mode="HTML")
                    return

            user = await session.get(User, message.from_user.id)
            if user is None:
                user = User(
                    user_id=message.from_user.id,
                    username=username,
                    home_nation_id=None,
                    balance=Decimal("0.00"),
                    xr_balance=Decimal("500.00"),
                    role="player",
                )
                session.add(user)
                await session.flush()
            else:
                user.username = username
                if user.home_nation_id is None and Decimal(str(user.xr_balance or 0)) == 0:
                    user.xr_balance = Decimal("500.00")

            await ensure_temporal_profile(
                session,
                message.from_user.id,
            )

    await state.update_data(username=username)
    await message.answer(
        NAME_ACCEPTED_TEXT.format(username=html.escape(username)),
        parse_mode="HTML",
    )

    # Existing Telegram membership can still be synchronized automatically.
    # Choosing a nation in onboarding is no longer required.
    async with async_session() as session:
        async with session.begin():
            synced_nations = await sync_registered_user_memberships(
                session,
                message.from_user.id,
            )

    async with async_session() as session:
        async with session.begin():
            registered_user = await get_user(
                session,
                message.from_user.id,
            )

    await state.clear()

    if synced_nations:
        await message.answer(
            rtl_text(
                "عضویت تلگرامی‌ات شناسایی شد و ملت مرتبط با حسابت همگام شد."
            ),
            parse_mode="HTML",
        )

    if registered_user is not None:
        from app.handlers.start import show_dashboard
        await show_dashboard(message, registered_user)



@router.callback_query(F.data == "onboarding_back_name", StateFilter(OnboardingStates.SELECT_NATION))
async def onboarding_back_name(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()

    data = await state.get_data()
    username = (data.get("username") or "").strip()

    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)

    text = USERNAME_CAPTION
    if username:
        text = rtl_text(
            "👤 <b>نام معامله‌گرت رو انتخاب کن</b>\n\n"
            f"نام فعلی: <b>{html.escape(username)}</b>\n"
            "<u>برای تغییر، یک نام جدید بفرست.</u>"
        )

    if call.message:
        try:
            await call.message.edit_text(
                text,
                reply_markup=cancel_keyboard(),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await call.message.answer(
                text,
                reply_markup=cancel_keyboard(),
                parse_mode="HTML",
            )



async def _start_timed_state(state: FSMContext, target_state) -> None:
    await state.set_state(target_state)
    await state.update_data(onboarding_started_at=time.time())



async def _state_is_alive(state: FSMContext, target_state) -> bool:
    if await state.get_state() != target_state.state:
        return False

    data = await state.get_data()
    started_at = data.get("onboarding_started_at")
    if not started_at:
        await state.update_data(onboarding_started_at=time.time())
        return True

    if time.time() - float(started_at) <= ONBOARDING_TIMEOUT:
        return True

    await state.clear()
    return False



async def _send_expired(message: Message) -> None:
    await message.answer(
        rtl_html(
            """
⏱ <b>فرآیند منقضی شد</b>

۵ دقیقه برای این مرحله فرصت داری.
برای شروع دوباره، /start رو بزن.
"""
        ),
        parse_mode=ParseMode.HTML,
    )



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

    # Nation membership is optional. If an old record still carries a home
    # nation but lost its holding, repair that holding. Otherwise the player
    # continues directly to the dashboard with no nation.
    if "holding" in missing and user.home_nation_id is not None:
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




async def _begin_registration(
    message: Message,
    state: FSMContext,
    *,
    replace_inline: bool = False,
) -> None:
    data_before_clear = await state.get_data()
    preferred_nation_id = data_before_clear.get("preferred_nation_id")

    async with async_session() as session:
        async with session.begin():
            registered = await is_fully_registered(session, message.from_user.id)
            user = await get_user(session, message.from_user.id) if registered else None

    if user is not None:
        await state.clear()
        if replace_inline:
            await show_dashboard(
                message,
                user,
                replace_inline=True,
                bot=message.bot,
                display_user=message.from_user,
            )
        else:
            await show_dashboard(message, user)
        return

    await state.clear()
    if preferred_nation_id is not None:
        await state.update_data(preferred_nation_id=preferred_nation_id)
    await _start_timed_state(state, OnboardingStates.ONBOARDING_NAME)

    suggested = (message.from_user.first_name or "").strip()[:20]
    if not suggested or not is_valid_trader_name(suggested) or is_blocked_trader_name(suggested):
        suggested = ""

    if suggested:
        await state.update_data(suggested_username=suggested)
        text = (
            "👤 <b>هویت معامله‌گرت</b>\n\n"
            f"برای شروع، می‌تونیم از اسم «{html.escape(suggested)}» استفاده کنیم.\n"
            "اسم بعداً از تنظیمات هم قابل تغییره.\n\n"
            "یا خودت یک نام ۳ تا ۲۰ کاراکتری انتخاب کن."
        )
        markup = suggested_name_keyboard(suggested)
    else:
        text = """
👤 <b>هویت معامله‌گرت رو بساز</b>

یک نام ۳ تا ۲۰ کاراکتری انتخاب کن.
این اسم روی تابلوی معاملات دیده می‌شه.

فارسی، انگلیسی، عدد و خط تیره مجازه.
"""
        markup = cancel_keyboard()

    if replace_inline:
        await message.edit_text(
            rtl_html(text),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        panel = message
    else:
        panel = await message.answer(
            rtl_html(text),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    await remember_inline_panel(state, panel)



@router.callback_query(
    F.data == "cancel_start",
    StateFilter(
        OnboardingStates.ONBOARDING_NAME,
        OnboardingStates.SET_USERNAME_PLAYER,
        OnboardingStates.SELECT_NATION,
    ),
)
async def cancel_onboarding_panel(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await close_inline_panel(state, call.bot)
    await state.clear()

    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        registered = user is not None and await is_fully_registered(
            session,
            call.from_user.id,
        )

    if registered and user is not None and call.message is not None:
        await show_dashboard(call.message, user, replace_inline=False)
    elif call.message is not None:
        await call.message.answer(
            rtl_text(
                "👋 <b>شروع OPEX MONEY</b>\n\n"
                "برای ورود به بازی، روی «شروع بازی» بزن."
            ),
            reply_markup=welcome_keyboard(),
            parse_mode=ParseMode.HTML,
        )

    await call.answer("❌ لغو شد.")


@router.callback_query(
    F.data == "use_suggested_name",
    StateFilter(OnboardingStates.ONBOARDING_NAME),
)
async def use_suggested_name(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    name = (data.get("suggested_username") or "").strip()
    if not name or not is_valid_trader_name(name) or is_blocked_trader_name(name):
        await call.answer("⚠️ این نام قابل استفاده نیست.", show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, name):
                await call.answer("⚠️ این نام قبلاً گرفته شده.", show_alert=True)
                return

    data = await state.get_data()
    await state.update_data(username=name, onboarding_started_at=time.time())
    await call.answer("✅ نام انتخاب شد")
    preferred_id = data.get("preferred_nation_id")
    if call.message and preferred_id is not None:
        async with async_session() as session:
            async with session.begin():
                preferred_nation = await session.get(Nation, int(preferred_id))
        if preferred_nation is not None and preferred_nation.is_active:
            await state.set_state(OnboardingStates.SELECT_NATION)
            await state.update_data(selected_nation_id=preferred_nation.nation_id)
            await _safe_edit_text(
                call,
                await render_nation_profile(preferred_nation),
                _nation_profile_keyboard(preferred_nation.nation_id),
            )
            return
    if call.message:
        await _render_nation_page(call.message, state, page=0, edit=True)



@router.callback_query(
    F.data == "choose_custom_name",
    StateFilter(OnboardingStates.ONBOARDING_NAME),
)
async def choose_custom_name(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.message:
        await call.message.edit_text(
            rtl_html(
                "👤 <b>نام معامله‌گرت رو بنویس</b>\n\n"
                "۳ تا ۲۰ کاراکتر. فارسی، انگلیسی، عدد و خط تیره."
            ),
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )



@router.message(OnboardingStates.ONBOARDING_NAME, F.text)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.ONBOARDING_NAME):
        await _send_expired(message)
        return

    name = (message.text or "").strip()

    # 1. length_filter
    if not 3 <= len(name) <= 20:
        await message.answer(
            rtl_html(
                """
🔴 <b>طول نام قابل قبول نیست</b>

نام معامله‌گر باید بین 3 تا 20 کاراکتر باشه.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. profanity_filter
    if profanity_filter(name):
        await message.answer(
            rtl_html(
                """
🔴 <b>این نام قابل قبول نیست</b>

این نام شامل عبارت نامناسبه.
یک نام مناسب برای معامله‌گر انتخاب کن.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # 3. duplicate_filter
    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, name):
                await message.answer(
                    rtl_html(
                        """
🔴 <b>این نام قبلاً ثبت شده</b>

یک نام دیگر برای معامله‌گرت انتخاب کن.
"""
                    ),
                    parse_mode=ParseMode.HTML,
                )
                return

    # 4. pattern_filter
    if not TRADER_NAME_RE.fullmatch(name):
        await message.answer(
            rtl_html(
                """
🔴 <b>فرمت نام قابل قبول نیست</b>

فقط حروف فارسی، حروف انگلیسی، عدد و خط تیره مجازه.
فاصله و سایر علامت‌ها مجاز نیستند.
"""
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # FSMStorage uses Redis when REDIS_URL is configured in main.py.
    data = await state.get_data()
    await state.update_data(
        username=name,
        onboarding_started_at=time.time(),
    )
    await close_inline_panel(state, message.bot)

    preferred_id = data.get("preferred_nation_id")
    if preferred_id is not None:
        async with async_session() as session:
            async with session.begin():
                preferred_nation = await session.get(Nation, int(preferred_id))
        if preferred_nation is not None and preferred_nation.is_active:
            await state.set_state(OnboardingStates.SELECT_NATION)
            await state.update_data(
                selected_nation_id=preferred_nation.nation_id,
                onboarding_started_at=time.time(),
            )
            await message.answer(
                await render_nation_profile(preferred_nation),
                reply_markup=_nation_profile_keyboard(preferred_nation.nation_id),
                parse_mode="HTML",
            )
            return

    await _render_nation_page(message, state, page=0)



@router.callback_query(
    F.data.startswith("nation_page:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def nation_page(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        page = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ صفحه معتبر نیست."), show_alert=True)
        return

    await call.answer()
    if call.message:
        await _render_nation_page(call.message, state, page=page, edit=True)


@router.callback_query(
    F.data.startswith("select_nation:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def select_nation(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        nation_id = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ ملت معتبر نیست."), show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)

    if nation is None or not nation.is_active:
        await call.answer(
            rtl_html("⚠️ این ملت دیگر فعال نیست."),
            show_alert=True,
        )
        return

    await state.update_data(
        selected_nation_id=nation_id,
        onboarding_started_at=time.time(),
    )
    await call.answer()

    if call.message:
        profile = await render_nation_profile(nation)
        await _safe_edit_text(
            call,
            profile,
            _nation_profile_keyboard(nation_id),
        )


@router.callback_query(
    F.data == "back_to_nations",
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def back_to_nations(call: CallbackQuery, state: FSMContext) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    await call.answer()
    data = await state.get_data()

    if call.message:
        await _render_nation_page(
            call.message,
            state,
            page=int(data.get("nation_page", 0)),
            edit=True,
        )


@router.callback_query(
    F.data.startswith("confirm_nation:"),
    StateFilter(OnboardingStates.SELECT_NATION),
)
async def confirm_nation(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _state_is_alive(state, OnboardingStates.SELECT_NATION):
        await call.answer()
        if call.message:
            await _send_expired(call.message)
        return

    try:
        nation_id = int(call.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await call.answer(rtl_html("⚠️ ملت معتبر نیست."), show_alert=True)
        return

    data = await state.get_data()
    username = (data.get("username") or "").strip()
    if not username:
        await call.answer(
            rtl_html("⏱ ثبت‌نام منقضی شد. /start بزن."),
            show_alert=True,
        )
        await state.clear()
        return

    try:
        # The canonical join flow owns membership and home_nation_id assignment.
        async with async_session() as session:
            async with session.begin():
                user = await session.get(
                    User,
                    call.from_user.id,
                    with_for_update=True,
                )
                if user is None:
                    user = User(
                        user_id=call.from_user.id,
                        username=username,
                        # SYNC RULE: home_nation_id always mirrors active NationMember wherever you touch these fields
                        home_nation_id=None,
                        balance=Decimal("0.00"),
                        xr_balance=Decimal("0.00"),
                        role="player",
                    )
                    session.add(user)
                    await session.flush()
                else:
                    user.username = username
    except IntegrityError:
        logger.exception(
            "Onboarding registration transaction failed | user_id=%s nation_id=%s",
            call.from_user.id,
            nation_id,
        )
        await call.answer(
            rtl_html(
                """
🔴 <b>این نام در همین لحظه ثبت شد</b>

یک نام دیگر برای معامله‌گرت انتخاب کن.
"""
            ),
            show_alert=True,
        )
        return

    try:
        result_message, nation = await _join_user(
            bot=bot,
            user_id=call.from_user.id,
            nation_id=nation_id,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await state.clear()

    if "درخواست عضویت ارسال شد" in result_message or "قبلاً ثبت شده" in result_message:
        if call.message:
            await call.message.edit_text(
                result_message,
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await call.answer("📝 درخواست عضویت ثبت شد.")
        return

    if nation is None:
        await call.answer("⚠️ اطلاعات ملت در دسترس نیست.", show_alert=True)
        return

    await state.update_data(first_trade_available=True)

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            nation = await session.get(Nation, nation_id)
            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == call.from_user.id,
                    CurrencyHolding.nation_id == nation_id,
                )
            )
    if user is None or nation is None or holding is None:
        await call.answer("⚠️ حساب ملت ناقص است. /start بزن.", show_alert=True)
        return

    text = f"""{html.escape(nation.flag_emoji or "🏴")} <b>به {html.escape(nation.name)} خوش اومدی</b>
\n
{user_mention(call.from_user)}، تو الان شهروند این ملت هستی.

💰 سرمایه شروع: <b>۵۰۰ <code>{html.escape(nation.currency_code)}</code></b>
💎 موجودی دلار: <b>۰</b>
📈 نرخ فعلی: <b>۱ {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} دلار</b>

🎯 <b>چرا این معامله؟</b>
این یک آموزش عملی است: ارز ملتت را به دلار تبدیل می‌کنی تا بعداً بتوانی ارزهای دیگر را معامله کنی.

⚡ <b>اولین حرکتت:</b>
۵۰ واحد از ارزت را بفروش و نتیجه را ببین.

<i>بعد از معامله، بازی قدم بعدی را نشانت می‌دهد.</i>"""
    await _safe_edit_text(call, text, first_trade_keyboard())
    await call.answer()


async def render_nation_profile(nation: Nation) -> str:
    """
    خروجی HTML فرمت، راست‌چین:

    🏴 <b>نام ملت</b>
    \n
    💰 <b>واحد پول:</b> {symbol}
    📈 <b>نرخ ارز:</b> {rate} دلار  <u>(آپدیت 15 دقیقه پیش)</u>
    👥 <b>اعضا:</b> {member_count} نفر
    🏆 <b>رتبه جهانی:</b> #{rank}
    ⚔️ <b>وضعیت:</b> {status_emoji} {status_text}

    📊 <b>وضعیت اقتصادی:</b>
    ▸ فعالیت: {activity_score}/100
    ▸ معاملات امروز: {today_trades}
    ▸ روند: {trend_emoji} {trend_text}
    """
    async with async_session() as session:
        async with session.begin():
            rank = nation.nation_rank or await get_nation_rank(session, nation.nation_id)
            today_start = datetime.combine(datetime.utcnow().date(), dt_time.min)
            today_trades = await session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.nation_id == nation.nation_id,
                    Transaction.created_at >= today_start,
                )
            ) or 0

    update_minutes = 0
    if nation.last_rate_update:
        update_minutes = max(
            0,
            int((datetime.utcnow() - nation.last_rate_update).total_seconds() // 60),
        )

    member_count = max(int(nation.member_count or 0), 0)
    active_members = max(int(nation.active_members_24h or 0), 0)
    activity_score = (
        0
        if member_count == 0
        else min(100, round((active_members / member_count) * 100))
    )

    current_rate = Decimal(str(nation.exchange_rate or Decimal("0")))
    previous_rate = Decimal(str(nation.rate_prev or current_rate))
    delta = (
        Decimal("0")
        if previous_rate == 0
        else (current_rate - previous_rate) / previous_rate * Decimal("100")
    )

    if delta > Decimal("0.10"):
        trend_emoji, trend_text = "📈", "صعودی"
    elif delta < Decimal("-0.10"):
        trend_emoji, trend_text = "📉", "نزولی"
    else:
        trend_emoji, trend_text = "➡️", "باثبات"

    change_24h = get_rate_change(nation)
    if change_24h > 1:
        status_emoji, status_text = "🟢", "قوی"
    elif change_24h < -1:
        status_emoji, status_text = "🔴", "تحت فشار"
    else:
        status_emoji, status_text = "🟡", "پایدار"

    text = """
{0}
\n
💰 <b>واحد پول:</b> {1}
📈 <b>نرخ ارز:</b> {2} دلار  <u>(آپدیت {3} دقیقه پیش)</u>
👥 <b>اعضا:</b> {4} نفر
🏆 <b>رتبه جهانی:</b> #{5}
⚔️ <b>وضعیت:</b> {6} {7}

📊 <b>وضعیت اقتصادی:</b>
▸ فعالیت: {8}/100
▸ معاملات امروز: {9}
▸ روند: {10} {11}
""".format(
        f"{html.escape(nation.flag_emoji or '🏴')} <b>{html.escape(nation.name)}</b>",
        html.escape(nation.currency_code),
        fmt_rate(nation.exchange_rate),
        to_fa(update_minutes),
        to_fa(member_count),
        to_fa(rank),
        status_emoji,
        status_text,
        to_fa(activity_score),
        to_fa(today_trades),
        trend_emoji,
        trend_text,
    )
    return rtl_html(text)



async def _load_nation_page(page: int) -> tuple[list[Nation], bool]:
    page = max(0, page)
    offset = page * NATIONS_PER_PAGE

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(Nation)
                .where(Nation.is_active.is_(True))
                .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
                .offset(offset)
                .limit(NATIONS_PER_PAGE + 1)
            )
            rows = list(result.scalars().all())

    return rows[:NATIONS_PER_PAGE], len(rows) > NATIONS_PER_PAGE



async def _render_nation_page(
    message: Message,
    state: FSMContext,
    page: int = 0,
    *,
    edit: bool = False,
) -> None:
    nations, has_next = await _load_nation_page(page)
    await _start_timed_state(state, OnboardingStates.SELECT_NATION)
    await state.update_data(nation_page=max(0, page))

    if not nations:
        text = """
🌍 <b>هنوز ملتی برای ورود وجود نداره</b>

می‌تونی بعداً برگردی و یک ملت فعال انتخاب کنی.
"""
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏛 تأسیس ملت",
                        callback_data="start_founder",
                        style=ButtonStyle.SUCCESS,
                    )
                ]
            ]
        )
    else:
        text = """
🌍 <b>ملتت رو انتخاب کن</b>

یک گزینه رو بزن و مستقیم وارد بازی شو.
💰 سرمایه شروع: ۵۰۰ واحد از ارز همان ملت
⚡ بعد از ورود، اولین معامله‌ات آماده‌ست.
"""
        keyboard = _nation_page_keyboard(nations, max(0, page), has_next)

    text = rtl_html(text)

    if edit:
        try:
            await message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return
        except TelegramBadRequest:
            pass

    await message.answer(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
