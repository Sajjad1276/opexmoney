from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import Message

logger = logging.getLogger(__name__)

WARNING_DELETE_DELAY = 10.0

_WARNING_PREFIXES = (
    "🔴", "⚠️", "⚠", "❌", "⛔", "🚫", "❗", "‼️",
    "خطا:", "خطا ", "هشدار:", "هشدار ",
)


def is_warning_text(text: str | None) -> bool:
    """Return True when a bot message is explicitly presented as a warning/error."""
    if not text:
        return False
    value = text.lstrip().casefold()
    return value.startswith(tuple(prefix.casefold() for prefix in _WARNING_PREFIXES))


async def delete_message_later(
    bot: Bot,
    chat_id: int,
    message_id: int,
    delay: float = WARNING_DELETE_DELAY,
) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        # Already deleted, not deletable, or insufficient Telegram permissions.
        return
    except Exception:
        logger.exception(
            "Failed to auto-delete warning message %s in chat %s",
            message_id,
            chat_id,
        )


def schedule_warning_deletion(
    message: Message,
    delay: float = WARNING_DELETE_DELAY,
) -> None:
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
    """Globally watch Telegram method execution so Message.answer() is covered too.

    In aiogram 3, Message.answer() creates a SendMessage method object and the
    Bot executes it through Bot.__call__. Patching only Bot.send_message misses
    that path. This hook therefore sits at the common execution point and
    catches both bot.send_message(...) and message.answer(...).
    """
    if getattr(Bot, "_opex_warning_auto_delete_installed", False):
        return

    original_call = Bot.__call__

    async def call_with_warning_auto_delete(self: Bot, method: Any, request_timeout: int | None = None):
        result = await original_call(self, method, request_timeout=request_timeout)

        if isinstance(result, Message):
            text: str | None = None
            if isinstance(method, SendMessage):
                text = method.text
            elif isinstance(method, SendPhoto):
                text = method.caption

            if is_warning_text(text):
                schedule_warning_deletion(result)

        return result

    Bot.__call__ = call_with_warning_auto_delete  # type: ignore[method-assign]
    Bot._opex_warning_auto_delete_installed = True


install_warning_auto_delete()
