from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database.models import Mission, UserMissionProgress
from app.database.session import async_session
from app.services.mission_service import (
    MissionStatus,
    claim_reward,
    get_user_missions,
)
from app.utils.formatting import fmt_amount, progress_bar, to_fa

router = Router(name="missions")
logger = logging.getLogger(__name__)

RLM = "\u200f"
MISSIONS_ERROR = "خطا در بارگذاری مأموریت‌ها"


def missions_keyboard(has_claimable: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_claimable:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎁 دریافت جوایز",
                    callback_data="missions_claim_all",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 بروزرسانی",
                callback_data="missions_refresh",
            ),
            InlineKeyboardButton(
                text="↩️ بازگشت",
                callback_data="back_to_dashboard",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _reward_text(reward_xr) -> str:
    return to_fa(fmt_amount(reward_xr))


def _progress_text(status: MissionStatus) -> str:
    progress = to_fa(status.progress)
    target = to_fa(status.target_count)
    reward = _reward_text(status.reward_xr)

    if status.completed and status.claimed:
        return f"✅ {html.escape(status.title_fa)} — <i>دریافت شد ✓</i>"

    if status.completed:
        return (
            f"🎁 {html.escape(status.title_fa)} — "
            f"<u>آماده دریافت!</u> +{reward} ΩXR"
        )

    line = (
        f"⬜ {html.escape(status.title_fa)} — "
        f"{progress}/{target} — جایزه: {reward} ΩXR"
    )
    if status.progress > 0:
        line += f"\n  {progress_bar(status.progress, status.target_count)}"
    return line


def _permanent_text(status: MissionStatus) -> str:
    progress = to_fa(status.progress)
    target = to_fa(status.target_count)

    if status.completed:
        return f"✅ {html.escape(status.title_fa)} — <i>انجام شد</i>"

    line = (
        f"🔒 {html.escape(status.title_fa)} — "
        f"{progress}/{target}"
    )
    if status.progress > 0:
        line += f"\n  {progress_bar(status.progress, status.target_count)}"
    return line


def build_missions_text(data: dict[str, list[MissionStatus]]) -> str:
    lines = [
        "⚡ <b>مأموریت‌ها</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🌅 <b>مأموریت‌های امروز</b>",
    ]

    daily = data["daily"]
    if daily:
        for status in daily:
            lines.append(_progress_text(status))
    else:
        lines.append("مأموریت فعالی برای امروز وجود ندارد.")

    lines.extend(
        [
            "",
            "📅 <b>مأموریت‌های هفتگی</b>",
        ]
    )

    weekly = data["weekly"]
    if weekly:
        for status in weekly:
            lines.append(_progress_text(status))
    else:
        lines.append("مأموریت فعالی برای این هفته وجود ندارد.")

    lines.extend(
        [
            "",
            "🏅 <b>دستاوردها</b>",
        ]
    )

    permanent = data["permanent"]
    if permanent:
        for status in permanent:
            lines.append(_permanent_text(status))
    else:
        lines.append("دستاورد فعالی وجود ندارد.")

    return "\n".join(f"{RLM}{line}" for line in lines)


def _has_claimable(data: dict[str, list[MissionStatus]]) -> bool:
    return any(
        status.completed and not status.claimed
        for statuses in data.values()
        for status in statuses
    )


async def _load_missions(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with async_session() as session:
        async with session.begin():
            data = await get_user_missions(session, user_id)
    return build_missions_text(data), missions_keyboard(_has_claimable(data))


async def show_missions(message: Message) -> None:
    try:
        text, markup = await _load_missions(message.from_user.id)
    except Exception:
        logger.exception(
            "Failed to load missions for user %s",
            message.from_user.id,
        )
        await message.answer(MISSIONS_ERROR, parse_mode=ParseMode.HTML)
        return

    await message.answer(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def refresh_missions(callback: CallbackQuery) -> None:
    try:
        text, markup = await _load_missions(callback.from_user.id)
    except Exception:
        logger.exception(
            "Failed to refresh missions for user %s",
            callback.from_user.id,
        )
        await callback.answer(MISSIONS_ERROR, show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            await callback.answer("همین لحظه به‌روز است ✓")
            return
        logger.exception(
            "Failed to edit missions message for user %s",
            callback.from_user.id,
        )
        await callback.answer(MISSIONS_ERROR, show_alert=True)


async def claim_all_rewards(callback: CallbackQuery) -> None:
    results = []
    try:
        async with async_session() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(UserMissionProgress.mission_id)
                        .join(
                            Mission,
                            Mission.id == UserMissionProgress.mission_id,
                        )
                        .where(
                            UserMissionProgress.user_id == callback.from_user.id,
                            UserMissionProgress.completed.is_(True),
                            UserMissionProgress.claimed.is_(False),
                            Mission.is_active.is_(True),
                        )
                        .order_by(UserMissionProgress.mission_id.asc())
                    )
                ).scalars().all()

                for mission_id in rows:
                    result = await claim_reward(
                        session,
                        callback.from_user.id,
                        mission_id,
                    )
                    if result.get("ok"):
                        results.append(result)

                data = await get_user_missions(
                    session,
                    callback.from_user.id,
                )
    except Exception:
        logger.exception(
            "Failed to claim mission rewards for user %s",
            callback.from_user.id,
        )
        await callback.answer(MISSIONS_ERROR, show_alert=True)
        return

    if results:
        result_text = "🎁 جوایز دریافت شد:\n" + "\n".join(
            (
                f"+{_reward_text(result['reward_xr'])} ΩXR — "
                f"{html.escape(result['mission_title'])}"
            )
            for result in results
        )
    else:
        result_text = "هیچ جایزه‌ای برای دریافت وجود ندارد"

    if len(result_text) > 200:
        result_text = result_text[:197].rstrip() + "…"

    await callback.answer(result_text, show_alert=True)

    if callback.message is None:
        return

    try:
        await callback.message.edit_text(
            build_missions_text(data),
            reply_markup=missions_keyboard(_has_claimable(data)),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.exception(
                "Failed to refresh missions after claiming rewards for user %s",
                callback.from_user.id,
            )


router.message.register(show_missions, F.text == "⚡ مأموریت‌ها")
router.message.register(show_missions, F.text == "⚡ مأموریت")
router.callback_query.register(
    refresh_missions,
    F.data == "missions_refresh",
)
router.callback_query.register(
    claim_all_rewards,
    F.data == "missions_claim_all",
)
