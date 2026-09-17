from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

logger = logging.getLogger(__name__)

WARNING_DELETE_DELAY = 10.0
_WARNING_MARKERS = ("🔴", "⚠️", "⚠", "❌")


def is_warning_text(text: str | None) -> bool:
    """Return True for bot messages explicitly presented as warnings/errors."""
    if not text:
        return False
    value = text.lstrip()
    if value.startswith(_WARNING_MARKERS):
        return True
    lowered = value.casefold()
    return lowered.startswith(("خطا:", "خطا ", "هشدار:", "هشدار "))


async def delete_message_later(bot: Bot, chat_id: int, message_id: int, delay: float = WARNING_DELETE_DELAY) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return
    except Exception:
        logger.exception("Failed to auto-delete warning message %s in chat %s", message_id, chat_id)


def schedule_warning_deletion(message: Message, delay: float = WARNING_DELETE_DELAY) -> None:
    if message.chat is None or not message.message_id:
        return
    asyncio.create_task(
        delete_message_later(
            message.bot,
            message.chat.id,
            message.message_id,
            delay,
        )
    )


def install_warning_auto_delete() -> None:
    """Install one global hook for warning/error messages sent by the bot."""
    if getattr(Bot, "_opex_warning_auto_delete_installed", False):
        return

    original_send_message: Callable[..., Awaitable[Message]] = Bot.send_message
    original_send_photo: Callable[..., Awaitable[Message]] = Bot.send_photo

    async def send_message_with_auto_delete(self: Bot, *args, **kwargs):
        message = await original_send_message(self, *args, **kwargs)
        text = kwargs.get("text")
        if text is None and len(args) > 1:
            text = args[1]
        if is_warning_text(text):
            schedule_warning_deletion(message)
        return message

    async def send_photo_with_auto_delete(self: Bot, *args, **kwargs):
        message = await original_send_photo(self, *args, **kwargs)
        caption = kwargs.get("caption")
        if caption is None and len(args) > 2:
            caption = args[2]
        if is_warning_text(caption):
            schedule_warning_deletion(message)
        return message

    Bot.send_message = send_message_with_auto_delete  # type: ignore[method-assign]
    Bot.send_photo = send_photo_with_auto_delete  # type: ignore[method-assign]
    Bot._opex_warning_auto_delete_installed = True


install_warning_auto_delete()
