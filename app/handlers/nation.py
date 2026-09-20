from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.database.models import Nation, NationMemberRole, User
from app.database.session import async_session
from app.handlers.start import show_dashboard
from app.keyboards.inline import nation_panel_keyboard
from app.services.user_service import is_fully_registered
from app.services.nation_service import get_user_active_nation_context

nation_router = Router(name="nation")

def _can_manage(user: User | None) -> bool:
    return user is not None and user.role in {
        NationMemberRole.FOUNDER.value,
        NationMemberRole.MINISTER.value,
    }


@nation_router.message(F.text == "🌍 ملت‌ها")
async def open_nations(message: Message) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id)
            registered = await is_fully_registered(session, message.from_user.id)
            context = (
                await get_user_active_nation_context(
                    session,
                    message.from_user.id,
                    repair=True,
                )
                if registered
                else None
            )
            active_nation = context[0] if context is not None else None
            active_role = context[1] if context is not None else None
            is_manager = active_role in {
                NationMemberRole.FOUNDER.value,
                NationMemberRole.MINISTER.value,
            }
            nation_id = active_nation.nation_id if active_nation is not None else None

    if not registered:
        await message.answer(
            "⚠️ اول باید وارد بازی بشی.\n"
            "برای شروع، /start رو بزن."
        )
        return

    await message.answer(
        "🌍 <b>ملت‌ها</b>\n"
        "اینجا می‌تونی ملت‌ها رو بررسی کنی.\n"
        "از گزینه‌ها برای ادامه استفاده کن.",
        reply_markup=nation_panel_keyboard(is_manager, nation_id),
        parse_mode="HTML",
    )


@nation_router.callback_query(F.data == "back_to_dashboard")
async def back_to_dashboard(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            if user is None or not await is_fully_registered(session, call.from_user.id):
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
    if call.message:
        await show_dashboard(
            call.message,
            user,
            replace_inline=True,
            bot=call.bot,
            display_user=call.from_user,
        )
    await call.answer()


@nation_router.callback_query(F.data == "back_to_nations_panel")
async def back_to_nations_panel(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            if user is None or not await is_fully_registered(session, call.from_user.id):
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
            is_manager = _can_manage(user)
            nation_id = user.home_nation_id
    if call.message:
        await call.message.edit_text(
            "🌍 <b>ملت‌ها</b>\nاینجا می‌تونی ملت‌ها رو بررسی کنی.\nاز گزینه‌ها برای ادامه استفاده کن.",
            reply_markup=nation_panel_keyboard(is_manager, nation_id),
            parse_mode="HTML",
        )
    await call.answer()


@nation_router.callback_query(F.data == "my_nations")
async def my_nations(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            if not await is_fully_registered(session, call.from_user.id):
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
            )
            user = await session.get(User, call.from_user.id)
            nations = [context[0]] if context is not None else []

    if not nations:
        text = (
            "🌍 <b>ملت‌های من</b>\n"
            "هنوز عضو هیچ ملتی نیستی.\n\n"
            "می‌تونی یک ملت موجود را بررسی کنی یا ملت خودت را تأسیس کنی."
        )
        markup = nation_panel_keyboard(False, None)
    else:
        nation = nations[0]
        manager = user is not None and _can_manage(user)
        text = (
            f"{nation.flag_emoji or '🏴'} <b>{nation.name}</b>\n"
            "<blockquote>⁠</blockquote>\n"
            f"💱 ارز: <b>{nation.currency_code}</b>\n"
            f"📈 نرخ: <b>{nation.exchange_rate}</b> ΩXR\n"
            f"👥 اعضا: <b>{nation.member_count}</b>\n"
            f"🏦 خزانه: <b>{nation.treasury}</b> ΩXR\n\n"
            "از دکمه‌های زیر وارد بخش‌های ملت شو."
        )
        markup = nation_panel_keyboard(manager, nation.nation_id)

    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )
    await call.answer()


@nation_router.callback_query(F.data == "explore_nations")
async def explore_nations(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            if not await is_fully_registered(session, call.from_user.id):
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
            nations = (
                await session.execute(
                    select(Nation)
                    .where(Nation.is_active.is_(True))
                    .order_by(Nation.member_count.desc(), Nation.nation_id.asc())
                    .limit(10)
                )
            ).scalars().all()

    if not nations:
        await call.message.edit_text(
            "🌍 <b>هنوز ملتی وجود نداره.</b>\n"
            "تو می‌تونی اولین ملت رو تأسیس کنی.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏛 تأسیس اولین ملت", callback_data="found_nation")]
            ]),
            parse_mode="HTML",
        ) if call.message else None
        await call.answer()
        return

    rows = [
        [InlineKeyboardButton(
            text=f"{nation.flag_emoji or '🏴'} {nation.name} · {nation.currency_code} · {nation.member_count} نفر",
            callback_data=f"join_nation:{nation.nation_id}",
        )]
        for nation in nations
    ]
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_nations_panel")])
    if call.message:
        await call.message.edit_text(
            "🔍 <b>کاوش ملت‌ها</b>\n"
            "ملت فعال موردنظرت رو انتخاب کن.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await call.answer()

