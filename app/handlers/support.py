from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.database.models import User
from app.database.session import async_session
from app.keyboards.inline import support_panel_keyboard, support_result_keyboard
from app.services.support.support_service import support_service
from app.states.support import SupportStates
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="support")

INTRO_TEXT = (
    "🛟 <b>پشتیبانی هوشمند</b>\n"
    "\n"
    "مشکلت رو به زبان خودت بنویس.\n"
    "سیستم وضعیت حساب، مسیر آخر و خطاهای اخیر رو بررسی می‌کنه.\n\n"
    "<i>مثال: بازار برای من باز نمی‌شه یا بعد از زدن دکمه خطا می‌ده.</i>"
)

PROMPT_TEXT = (
    "✍️ <b>مشکل رو توضیح بده</b>\n"
    "هرچی دقیق‌تر بگی، تشخیص دقیق‌تر می‌شه."
)


async def _open_support_from_message(message: Message, state: FSMContext) -> None:
    await support_service.begin(state)
    panel = await send_submenu_panel(
        message,
        INTRO_TEXT,
        reply_markup=support_panel_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, panel)


async def _open_support_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    await support_service.begin(state)
    await callback.message.edit_text(
        INTRO_TEXT,
        reply_markup=support_panel_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, callback.message)


@router.message(F.text == "🛟 پشتیبانی هوشمند")
async def open_support(message: Message, state: FSMContext) -> None:
    await _open_support_from_message(message, state)


@router.callback_query(F.data == "support_open")
async def open_support_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await _open_support_from_callback(callback, state)


@router.callback_query(F.data == "support_check_recent")
async def check_recent_issue(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    async with async_session() as session:
        async with session.begin():
            result = await support_service.analyze(
                session,
                state,
                user_id=callback.from_user.id,
                report="",
            )

    result = await support_service.finalize(
        user_id=callback.from_user.id,
        report="",
        result=result,
    )
    await state.clear()
    await callback.message.edit_text(
        result.response_text,
        reply_markup=support_result_keyboard(
            market_retry=(
                result.diagnosis.market_retry
                and result.repair.applied
                and result.repair.verified
            ),
        ),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("بررسی انجام شد")


@router.callback_query(F.data == "support_new_report")
async def new_support_report(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    await state.clear()
    await support_service.begin(state)
    await callback.message.edit_text(
        PROMPT_TEXT,
        parse_mode=ParseMode.HTML,
    )
    await remember_inline_panel(state, callback.message)
    await callback.answer()


@router.callback_query(F.data == "support_back")
async def support_back(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await close_inline_panel(state, callback.bot)
    await state.clear()
    if callback.message is None:
        await callback.answer()
        return

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, callback.from_user.id)

    if user is None:
        await callback.answer("⚠️ حساب پیدا نشد.", show_alert=True)
        return

    from app.handlers.dashboard import show_dashboard

    await show_dashboard(
        callback.message,
        user,
        replace_inline=True,
        bot=callback.bot,
        display_user=callback.from_user,
    )
    await callback.answer()


@router.message(SupportStates.WAITING_REPORT, F.text)
async def submit_support_report(
    message: Message,
    state: FSMContext,
) -> None:
    report = (message.text or "").strip()
    if not report:
        await message.answer(PROMPT_TEXT, parse_mode=ParseMode.HTML)
        return

    await close_inline_panel(state, message.bot)
    async with async_session() as session:
        async with session.begin():
            result = await support_service.analyze(
                session,
                state,
                user_id=message.from_user.id,
                report=report,
            )

    result = await support_service.finalize(
        user_id=message.from_user.id,
        report=report,
        result=result,
    )
    await state.clear()
    await message.answer(
        result.response_text,
        reply_markup=support_result_keyboard(
            market_retry=(
                result.diagnosis.market_retry
                and result.repair.applied
                and result.repair.verified
            ),
        ),
        parse_mode=ParseMode.HTML,
    )


__all__ = ["router"]
