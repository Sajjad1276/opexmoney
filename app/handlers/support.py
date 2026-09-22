from __future__ import annotations

import logging

from aiogram import F, Router\nfrom aiogram.filters import StateFilter
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database.models import User
from app.database.session import async_session
from app.handlers.dashboard import show_dashboard
from app.keyboards.inline import support_panel_keyboard, support_result_keyboard
from app.services.support.models import SupportResult
from app.services.support.support_service import support_service
from app.states.support import SupportStates
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="support")
logger = logging.getLogger(__name__)

INTRO_TEXT = (
    "🛟 <b>پشتیبانی هوشمند</b>\n"
    "\n"
    "مشکلت رو به زبان خودت بنویس.\n"
    "سیستم وضعیت حساب، مسیر آخر و خطاهای اخیر رو بررسی می‌کنه.\n\n"
    "<i>مثال: بازار برای من باز نمی‌شه یا بعد از زدن دکمه خطا می‌ده.</i>"
)

PROMPT_TEXT = (
    "🛟 <b>مرکز پشتیبانی OPEX</b>\n\n"
    "مشکل را همان‌طور که تجربه‌اش کردی بنویس.\n"
    "اوپکس نشانه‌ها را بررسی می‌کند، ریشه مشکل را پیدا می‌کند و تا جای ممکن مسیر اصلاح را پیشنهاد می‌دهد.\n\n"
    "<i>مثال: بازار باز می‌شود ولی فهرست ارزها نمایش داده نمی‌شود.</i>"
)


async def _open_support_panel(message: Message, state: FSMContext) -> None:
    await support_service.begin(state)
    panel = await send_submenu_panel(
        message,
        PROMPT_TEXT,
        reply_markup=support_panel_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, panel)


async def _run_support_check(
    *,
    state: FSMContext,
    user_id: int,
    report: str,
) -> SupportResult:
    async with async_session() as session:
        async with session.begin():
            result = await support_service.analyze(
                session,
                state,
                user_id=user_id,
                report=report,
            )
    return await support_service.finalize(
        user_id=user_id,
        report=report,
        result=result,
    )


async def _render_support_result(
    message: Message,
    state: FSMContext,
    result: SupportResult,
) -> None:
    await close_inline_panel(state, message.bot)

    panel = await message.answer(
        result.response_text,
        reply_markup=support_result_keyboard(
            market_retry=result.diagnosis.market_retry,
        ),
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, panel)
    await state.clear()


@router.message(F.text == "🛟 پشتیبانی هوشمند")
async def open_support(message: Message, state: FSMContext) -> None:
    try:
        await _open_support_panel(message, state)
    except Exception:
        logger.exception("Could not open support panel user=%s", message.from_user.id)
        await state.clear()
        await message.answer(
            "⚠️ مرکز پشتیبانی موقتاً در دسترس نیست. دوباره تلاش کن.",
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == "support_check_recent")
async def support_check_recent(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("در حال بررسی آخرین وضعیت…")
    if callback.message is None:
        return

    current_state = await state.get_state()
    if current_state != SupportStates.WAITING_REPORT.state:
        await support_service.begin(state)

    try:
        result = await _run_support_check(
            state=state,
            user_id=callback.from_user.id,
            report="آخرین مشکل ثبت‌شده و وضعیت فعلی حسابم را بررسی کن.",
        )
        await _render_support_result(callback.message, state, result)
    except Exception:
        logger.exception(
            "Recent support check failed user=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "⚠️ بررسی پشتیبانی ناموفق بود. دوباره تلاش کن.",
            show_alert=True,
        )


@router.callback_query(F.data == "support_new_report")
async def support_new_report(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        await support_service.begin(state)
        if callback.message is None:
            return
        await callback.message.edit_text(
            PROMPT_TEXT,
            reply_markup=support_panel_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await remember_inline_panel(state, callback.message)
    except Exception:
        logger.exception(
            "Could not open new support report user=%s",
            callback.from_user.id,
        )
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                "⚠️ مرکز پشتیبانی موقتاً در دسترس نیست.",
                parse_mode=ParseMode.HTML,
            )


@router.callback_query(F.data == "support_back")
async def support_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        await state.clear()
        return

    await close_inline_panel(state, callback.message.bot)
    await state.clear()

    async with async_session() as session:
        user = await session.get(User, callback.from_user.id)

    if user is None:
        await callback.message.answer("🔴 حساب پیدا نشد. /start بزن.")
        return

    await show_dashboard(callback.message, user)


@router.message(SupportStates.WAITING_REPORT, F.text)
async def support_report(message: Message, state: FSMContext) -> None:
    report = (message.text or "").strip()
    if not report:
        await message.answer(
            "متن مشکل خالی است. یک توضیح کوتاه از چیزی که خراب شده بنویس.",
        )
        return

    try:
        result = await _run_support_check(
            state=state,
            user_id=message.from_user.id,
            report=report,
        )
        await _render_support_result(message, state, result)
    except Exception:
        logger.exception(
            "Support report failed user=%s",
            message.from_user.id,
        )
        await message.answer(
            "⚠️ بررسی مشکل ناموفق بود. جزئیات را دوباره بفرست.",
            parse_mode=ParseMode.HTML,
        )


__all__ = ["router", "open_support", "support_check_recent", "support_new_report", "support_back", "support_report"]
