from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.filters.state import StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, User
from app.database.session import async_session
from app.handlers.start import _safe_edit_caption, _safe_edit_text, start as restart_flow, user_mention
from app.keyboards.inline import cancel_keyboard, nation_selection_keyboard
from app.services.temporal_service import ensure_temporal_profile
from app.services.user_service import get_user, is_fully_registered, username_exists
from app.states.onboarding import OnboardingStates
from app.utils.formatting import fmt_pct, fmt_rate, get_rate_change, get_rate_emoji, to_fa
from app.utils.name_filter import is_blocked_trader_name, is_valid_trader_name

router = Router(name="onboarding_fix")

# Telegram does not expose a text-align control for bot messages. RLM markers
# keep mixed Persian/Latin/emoji lines in an RTL paragraph direction and reduce
# the visual jump to the left caused by mentions, numbers and symbols.
RLM = "\u200f"


def rtl_text(text: str) -> str:
    return "\n".join(f"{RLM}{line}" if line else "" for line in text.split("\n"))


USERNAME_CAPTION = rtl_text("""<b>👤 نام معامله‌گرت رو انتخاب کن</b>

نام نمایش داده می‌شه و بقیه بازیکن‌ها تو رو با همین نام می‌بینن.
۳ تا ۲۰ کاراکتر: فارسی، انگلیسی، عدد و خط تیره.
<u>فاصله و علامت‌های دیگر مجاز نیست.</u>""")

INVALID_NAME = rtl_text("""<b>🔴 نام قابل قبول نیست</b>

طول نام باید بین ۳ تا ۲۰ کاراکتر باشه.
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


async def show_nation_selection(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    async with session.begin():
        result = await session.execute(
            select(Nation)
            .where(Nation.is_active.is_(True))
            .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
            .limit(10)
        )
        nations = list(result.scalars().all())

    await state.set_state(OnboardingStates.SELECT_NATION)

    if not nations:
        await message.answer(
            NO_NATION_TEXT,
            reply_markup=onboarding_back_keyboard(),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    trader_name = (data.get("username") or "").strip()

    await message.answer(
        clean_nation_list_text(message.from_user, trader_name, nations[:4]),
        reply_markup=nation_selection_keyboard(nations[:4]),
        parse_mode="HTML",
    )

def clean_nation_list_text(user, trader_name: str, nations) -> str:
    """Render the onboarding nation list as a compact RTL message."""
    lines = [
        "🌍 <b>ملت خودت رو انتخاب کن</b>",
        f"«{html.escape(trader_name)}»، یک ملت انتخاب کن. <u>نرخ‌ها هر ۱۵ دقیقه آپدیت می‌شن.</u>",
    ]

    for nation in nations[:4]:
        lines.append(
            f"🏴 <b>{html.escape(nation.name)}</b> | "
            f"💰 {fmt_rate(nation.exchange_rate)} ΩXR | "
            f"👥 {to_fa(nation.member_count)} نفر"
        )

    return rtl_text("\n".join(lines))




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
async def reject_non_english_name(message: Message, state: FSMContext) -> None:
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
        from app.handlers.start import show_dashboard
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
                session.add(
                    User(
                        user_id=message.from_user.id,
                        username=username,
                        home_nation_id=None,
                        balance=0,
                        xr_balance=0,
                        role="player",
                    )
                )
                await session.flush()
            else:
                user.username = username

            await ensure_temporal_profile(
                session,
                message.from_user.id,
            )

    await state.update_data(username=username)

    await message.answer(
        NAME_ACCEPTED_TEXT.format(username=html.escape(username)),
        parse_mode="HTML",
    )

    async with async_session() as session:
        await show_nation_selection(message, session, state)



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


@router.callback_query(F.data == "cancel_start", StateFilter(OnboardingStates.SET_USERNAME_PLAYER))
async def cancel_start_fix(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_name = html.escape(call.from_user.first_name or "معامله‌گر")
    text = CANCEL_TEXT.format(user_name=user_name)
    try:
        if call.message and getattr(call.message, "photo", None):
            await _safe_edit_caption(call, text)
        else:
            await _safe_edit_text(call, text)
        await call.answer()
    except TelegramBadRequest:
        await call.answer("ثبت‌نام لغو شد.", show_alert=False)
