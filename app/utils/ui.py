from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message


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
