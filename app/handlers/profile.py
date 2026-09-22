from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database.models import User
from app.database.session import async_session
from app.handlers.dashboard import show_dashboard
from app.services.player_profile_service import PlayerProfile, get_player_profile
from app.utils.formatting import fmt_amount, to_fa
from app.utils.ui import send_submenu_panel

router = Router(name="profile")
logger = logging.getLogger(__name__)


def _bar(value: int, width: int = 10) -> str:
    filled = round(max(0, min(100, value)) * width / 100)
    return "█" * filled + "░" * (width - filled)


def build_profile_text(profile: PlayerProfile) -> str:
    nation = (
        f"{html.escape(profile.nation_name or '')} · "
        f"{html.escape(profile.nation_currency or '')}"
        if profile.nation_name
        else "بدون ملت"
    )

    rows = [
        ("تجارت", profile.reputation_trade),
        ("حکمرانی", profile.reputation_governance),
        ("دانش", profile.reputation_knowledge),
        ("نظامی", profile.reputation_military),
        ("سازندگی", profile.reputation_builder),
    ]

    scores = [
        ("معامله‌گر", profile.reputation_trade),
        ("حاکم", profile.reputation_governance),
        ("نوآور", profile.reputation_knowledge),
        ("جنگ‌سالار", profile.reputation_military),
        ("سازنده", profile.reputation_builder),
    ]
    scores.sort(key=lambda item: item[1], reverse=True)

    lines = [
        f"👤 <b>{html.escape(profile.username)}</b>",
        f"ملت: <b>{nation}</b>",
        "",
        f"سبک غالب: <b>{profile.archetype}</b>",
        "",
        "<b>گرایش‌های تو</b>",
    ]
    for index, (label, value) in enumerate(scores[:3], start=1):
        lines.append(f"{index}. {label} · {to_fa(value)}/100")
    lines.extend(
        [
            "",
            "<b>شهرت‌های رفتاری</b>",
        ]
    )
    for label, value in rows:
        lines.append(f"{label:<8} {_bar(value)}  {to_fa(value)}/100")

    lines.extend(
        [
            "",
            f"دارایی دلار: <b>{fmt_amount(profile.wealth_xr)}</b>",
            f"معامله در ۳۰ روز: <b>{to_fa(profile.trades)}</b>",
            f"حجم معامله: <b>{fmt_amount(profile.trade_volume)}</b> دلار",
            f"درس تکمیل‌شده: <b>{to_fa(profile.lessons_completed)}</b>",
            f"جنگ‌های ملت در ۳۰ روز: <b>{to_fa(profile.wars_seen)}</b>",
            "",
            "<i>سبک بازی از رفتار واقعی تو استخراج می‌شود.</i>",
        ]
    )
    return "\n".join(lines)


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="profile:back",
                    style=ButtonStyle.DANGER,
                )
            ]
        ]
    )


@router.message(F.text == "👤 پروفایل")
async def show_profile(message: Message) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                profile = await get_player_profile(session, message.from_user.id)
    except Exception:
        logger.exception(
            "Failed to load player profile for user %s",
            message.from_user.id,
        )
        await message.answer("خطا در بارگذاری پروفایل.", parse_mode=ParseMode.HTML)
        return

    await send_submenu_panel(
        message,
        build_profile_text(profile),
        reply_markup=profile_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "profile:back")
async def profile_back(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return

    await callback.answer()
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, callback.from_user.id)

    if user is None:
        return

    await show_dashboard(
        callback.message,
        user,
        replace_inline=True,
        bot=callback.bot,
        display_user=callback.from_user,
    )
