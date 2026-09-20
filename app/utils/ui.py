from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove


PANEL_MESSAGE_ID = "inline_panel_message_id"
PANEL_CHAT_ID = "inline_panel_chat_id"


async def remember_inline_panel(
    state: FSMContext,
    message: Message | None,
) -> None:
    if message is None:
        return
    await state.update_data(
        **{
            PANEL_MESSAGE_ID: message.message_id,
            PANEL_CHAT_ID: message.chat.id,
        }
    )


async def close_inline_panel(
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    message_id = data.get(PANEL_MESSAGE_ID)
    chat_id = data.get(PANEL_CHAT_ID)

    if message_id is not None and chat_id is not None:
        try:
            await bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
        except Exception:
            pass

    await state.update_data(
        **{
            PANEL_MESSAGE_ID: None,
            PANEL_CHAT_ID: None,
        }
    )


async def hide_reply_keyboard(
    bot: Bot,
    chat_id: int,
) -> None:
    """Remove the persistent reply keyboard without leaving a visible message."""
    try:
        marker = await bot.send_message(
            chat_id,
            "\u2060",
            reply_markup=ReplyKeyboardRemove(),
        )
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=marker.message_id,
            )
        except Exception:
            pass
    except Exception:
        pass


async def send_submenu_panel(
    message: Message,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
) -> Message:
    """Show an inline submenu while safely removing the persistent reply keyboard.

    Telegram does not allow converting a message carrying ReplyKeyboardRemove
    into a message carrying InlineKeyboardMarkup. The two UI operations therefore
    use separate messages: an invisible keyboard-removal marker followed by the
    real submenu panel. The marker is deleted immediately after the panel is sent.
    """
    if reply_markup is None:
        return await message.answer(
            text,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=parse_mode,
        )

    marker = await message.answer(
        "\u2060",
        reply_markup=ReplyKeyboardRemove(),
    )
    try:
        panel = await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    finally:
        try:
            await message.bot.delete_message(
                chat_id=marker.chat.id,
                message_id=marker.message_id,
            )
        except Exception:
            pass

    return panel
