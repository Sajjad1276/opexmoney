from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, User
from app.database.session import async_session
from app.handlers.start import start as restart_flow, user_mention
from app.keyboards.inline import cancel_keyboard, nation_selection_keyboard
from app.services.draft_service import clear_draft, save_draft
from app.services.keyboard_state import keyboard_manager
from app.services.intent_router import cancel_current_flow
from app.services.nation_service import get_active_nations
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


USERNAME_CAPTION = rtl_text("""💹 <b>اسم معامله‌گرت رو انتخاب کن.</b>

اسم انتخابی تو روی تابلوی معاملات OPEX نمایش داده میشه.

بنویس:
· ۳ تا ۱۵ کاراکتر
· فقط حروف انگلیسی و عدد
· بدون فاصله، @ و علامت خاص""")

INVALID_NAME = rtl_text("""🔴 <b>این نام قابل قبول نیست.</b>

فقط ۳ تا ۱۵ کاراکتر انگلیسی وارد کن.
حروف و عدد مجازه، اما فاصله و علامت خاص نه.
مثال: <code>Arman7</code>""")

BLOCKED_NAME = rtl_text("""🔴 <b>این نام قابل قبول نیست.</b>

این نام شامل عبارت نامناسب یا مستهجن است.
یک نام صحیح و مناسب برای معامله‌گر انتخاب کن.""")

NAME_ACCEPTED_TEXT = rtl_text("""🎉 <b>تبریک! «{username}» با موفقیت ثبت شد.</b>

اسم معامله‌گری تو آماده است و از این به بعد در OPEX با همین نام شناخته میشی.""")

NO_NATION_TEXT = rtl_text("""🌍 <b>هنوز هیچ ملتی تأسیس نشده!</b>

تو می‌تونی اولین بنیان‌گذار تاریخ باشی
و اولین ملت OPEX MONEY رو بسازی.""")


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
        await keyboard_manager.send(
            message,
            "🌍 هنوز هیچ ملتی تأسیس نشده!\n"
            "تو می‌تونی اولین بنیان‌گذار باشی.\n"
            "اولین ملت رو از همین‌جا بساز.",
            kind="inline:nation-selection",
            markup=nation_selection_keyboard([]),
        )
        return

    lines = ["🌍 <b>ملت خودت رو انتخاب کن:</b>", "هر ملت یه اقتصاد مستقله."]
    lines.extend(
        f"🏴 {html.escape(nation.name)} ({html.escape(nation.currency_code)}) · {to_fa(nation.member_count)} نفر"
        for nation in nations[:4]
    )
    lines.append("ملت جدید هم می‌تونی تأسیس کنی.")
    await keyboard_manager.send(
        message,
        "\n".join(lines),
        kind="inline:nation-selection",
        markup=nation_selection_keyboard(nations[:4]),
        parse_mode="HTML",
    )

def clean_nation_list_text(user, trader_name: str, nations) -> str:
    """Render nation selection as clean RTL paragraphs without separators."""
    lines = [
        f"🌍 <b>«{html.escape(trader_name)}»، حالا یک ملت انتخاب کن.</b>",
        "",
        f"ارز ملتی که انتخاب می‌کنی پول اصلی حسابت میشه، {user_mention(user)}.",
        "هر معامله‌ات مستقیم روی نرخ اون ارز اثر میذاره.",
        "",
        "<b>ملت‌های فعال</b>",
        "",
    ]

    for index, nation in enumerate(nations, start=1):
        change = get_rate_change(nation)
        lines.extend([
            f"🏛 <b>{html.escape(nation.name)} · {html.escape(nation.currency_code)}</b>",
            f"{get_rate_emoji(change)} <b>{fmt_rate(nation.exchange_rate)} ΩXR</b> · <i>{fmt_pct(change)} امروز</i>",
            f"👥 {to_fa(nation.active_members_24h)} عضو · 🏆 رتبه #{to_fa(nation.nation_rank or 0)}",
        ])
        if index != len(nations):
            lines.append("")

    lines.extend([
        "",
        "نرخ‌ها هر ۱۵ دقیقه آپدیت میشن.",
    ])
    return rtl_text("\n".join(lines))


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

    duplicate = False
    async with async_session() as session:
        async with session.begin():
            if await username_exists(session, username):
                existing = await session.get(User, message.from_user.id)
                duplicate = existing is None or existing.username != username
            if not duplicate:
                user = await session.get(User, message.from_user.id)
                if user is None:
                    session.add(User(
                        user_id=message.from_user.id,
                        username=username,
                        home_nation_id=None,
                        balance=0,
                        xr_balance=0,
                        role="player",
                    ))
                    await session.flush()
                else:
                    user.username = username

                await ensure_temporal_profile(
                    session,
                    message.from_user.id,
                )

    if duplicate:
        duplicate_text = rtl_text(
            f"🔴 <b>«{html.escape(username)}» قبلاً ثبت شده.</b>\n\nیک اسم دیگه برای معامله‌گرت انتخاب کن."
        )
        await message.answer(duplicate_text, parse_mode="HTML")
        return

    await state.update_data(username=username)

    async with async_session() as session:
        async with session.begin():
            await save_draft(
                message.from_user.id,
                "onboarding.select_nation",
                {"username": username},
                session=session,
            )

    confirmation = NAME_ACCEPTED_TEXT.format(username=html.escape(username))
    await keyboard_manager.send(
        message,
        confirmation,
        kind="none",
        markup=None,
        parse_mode="HTML",
    )

    async with async_session() as session:
        await show_nation_selection(message, session, state)


@router.callback_query(F.data == "cancel_start", StateFilter(OnboardingStates.SET_USERNAME_PLAYER))
async def cancel_start_fix(call: CallbackQuery, state: FSMContext) -> None:
    if call.message:
        await cancel_current_flow(call.message, state)
    await call.answer("ثبت‌نام لغو شد.")
