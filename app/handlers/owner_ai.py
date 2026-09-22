from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.owner_ai_service import is_owner, run_owner_ai
from app.states.owner_ai import OwnerAIStates

router = Router(name="owner_ai")


def owner_ai_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧠 وضعیت پروژه",
                    callback_data="owner_ai_status",
                ),
                InlineKeyboardButton(
                    text="❌ بستن",
                    callback_data="owner_ai_close",
                ),
            ]
        ]
    )


@router.message(Command("ai"))
async def owner_ai_command(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None or not is_owner(user.id):
        await message.answer("⛔️ این دستور فقط برای مالک پروژه فعال است.")
        return

    await state.set_state(OwnerAIStates.CHAT)
    await state.update_data(history=[])
    await message.answer(
        "<b>دستیار مالک OPEX MONEY</b>\n\n"
        "درخواستت را طبیعی بنویس. من کد واقعی پروژه، CI و وضعیت deploy را بررسی می‌کنم "
        "و برای تغییرات کد، ابتدا روی branch کار می‌کنم و فقط بعد از سبز شدن CI merge می‌کنم.\n\n"
        "<i>مثال:</i>\n"
        "«مشکل دکمه تأسیس ملت را پیدا کن و رفعش کن.»\n"
        "«متن بخش خرید ارز را عوض کن و تست‌هایش را هم درست کن.»\n"
        "«آخرین crash را بررسی کن.»",
        reply_markup=owner_ai_keyboard(),
    )


@router.callback_query(F.data == "owner_ai_close")
async def owner_ai_close(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        if call.message:
            await call.message.edit_text("دستیار مالک بسته شد.")
    finally:
        await call.answer()


@router.callback_query(F.data == "owner_ai_status")
async def owner_ai_status(call: CallbackQuery, state: FSMContext) -> None:
    user = call.from_user
    if user is None or not is_owner(user.id):
        await call.answer("دسترسی ندارید.", show_alert=True)
        return

    status = await run_owner_ai(
        user.id,
        "وضعیت واقعی پروژه را بررسی کن؛ فقط گزارش بده و هیچ فایلی را تغییر نده.",
        history=[],
    )
    await call.message.answer(status.summary)
    await call.answer()


@router.message(OwnerAIStates.CHAT, F.text)
async def owner_ai_message(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None or not is_owner(user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        return

    data = await state.get_data()
    history = list(data.get("history") or [])
    progress_message = await message.answer("در حال بررسی پروژه...")

    async def progress(update: str) -> None:
        try:
            await progress_message.edit_text(f"🧠 {update}")
        except Exception:
            pass

    result = await run_owner_ai(
        user.id,
        text,
        history=history,
        progress=progress,
    )

    history.append({"role": "owner", "content": text})
    history.append({"role": "agent", "content": result.summary})
    await state.update_data(history=history[-10:])

    try:
        await progress_message.edit_text(
            f"<b>نتیجه</b>\n\n{result.summary}",
            reply_markup=owner_ai_keyboard(),
        )
    except Exception:
        await message.answer(result.summary, reply_markup=owner_ai_keyboard())
