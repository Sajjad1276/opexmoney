from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.database.models import Nation, User
from app.database.session import async_session
from app.handlers.start import show_dashboard
from app.keyboards.inline import nation_panel_keyboard
from app.services.user_service import is_fully_registered

nation_router = Router(name="nation")


def _soon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")]
    ])


@nation_router.message(F.text == "🌍 ملت‌ها")
async def open_nations(message: Message) -> None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id)
            if not await is_fully_registered(session, message.from_user.id):
                await message.answer(
                    "⚠️ اول باید وارد بازی بشی.\n"
                    "برای شروع، /start رو بزن."
                )
                return
            is_founder = user is not None and user.role == "founder"

    await message.answer(
        "🌍 <b>ملت‌ها</b>\n"
        "اینجا می‌تونی ملت‌ها رو بررسی کنی.\n"
        "از گزینه‌ها برای ادامه استفاده کن.",
        reply_markup=nation_panel_keyboard(is_founder),
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
        await show_dashboard(call.message, user)
    await call.answer()


@nation_router.callback_query(F.data == "my_nations")
async def my_nations(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            if not await is_fully_registered(session, call.from_user.id):
                await call.answer("⚠️ اول باید وارد بازی بشی.", show_alert=True)
                return
            user = await session.get(User, call.from_user.id)
            nations = []
            if user and user.home_nation_id:
                nation = await session.get(Nation, user.home_nation_id)
                if nation:
                    nations.append(nation)

    if not nations:
        text = "🌍 <b>ملت‌های من</b>\nهنوز عضو هیچ ملتی نیستی."
    else:
        nation = nations[0]
        text = (
            "🌍 <b>ملت‌های من</b>\n"
            f"🏛 {nation.name} · {nation.currency_code}\n"
            f"👥 {nation.member_count} نفر\n"
            "برای جزئیات بیشتر، این بخش در حال توسعه است."
        )
    await call.message.edit_text(
        text,
        reply_markup=_soon_keyboard(),
        parse_mode="HTML",
    ) if call.message else None
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
            text=f"🏴 {nation.name} · {nation.currency_code} · {nation.member_count} نفر",
            callback_data=f"join_nation:{nation.nation_id}",
        )]
        for nation in nations
    ]
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")])
    if call.message:
        await call.message.edit_text(
            "🔍 <b>کاوش ملت‌ها</b>\n"
            "ملت فعال موردنظرت رو انتخاب کن.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await call.answer()


@nation_router.callback_query(F.data.in_({"founder_panel", "create_nation"}))
async def unavailable_nation_panel(call: CallbackQuery) -> None:
    await call.answer(
        "ℹ️ این بخش هنوز فعال نشده.",
        show_alert=True,
    )


@nation_router.callback_query(F.data.in_({"confirm_trade", "cancel_trade"}))
async def unavailable_trade_confirmation(call: CallbackQuery) -> None:
    await call.answer(
        "ℹ️ این تأیید در نسخه فعلی استفاده نمی‌شود.",
        show_alert=True,
    )
