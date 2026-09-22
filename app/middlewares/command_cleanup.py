from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from app.utils.ui import close_inline_panel, hide_reply_keyboard


class CommandPanelCleanupMiddleware(BaseMiddleware):
    """Close stale bot panels whenever a Telegram command starts a new flow."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        text = getattr(event, "text", None)
        if isinstance(text, str) and text.lstrip().startswith("/"):
            state = data.get("state")
            bot = data.get("bot") or getattr(event, "bot", None)
            chat = getattr(event, "chat", None)
            if state is not None and bot is not None and chat is not None:
                try:
                    await close_inline_panel(state, bot)
                except Exception:
                    pass
                try:
                    await hide_reply_keyboard(bot, int(chat.id))
                except Exception:
                    pass

        return await handler(event, data)
