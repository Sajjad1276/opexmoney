from __future__ import annotations

import logging
from aiogram import BaseMiddleware
from typing import Any, Awaitable, Callable

logger = logging.getLogger("opexmoney.flow")

class FlowTraceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", "unknown")
        callback = getattr(event, "data", None)
        text = getattr(event, "text", None)
        event_name = f"callback:{callback}" if callback else f"message:{text!r}"
        logger.info("FLOW|BEGIN|user=%s|%s", user_id, event_name)
        try:
            result = await handler(event, data)
            logger.info("FLOW|PASS|user=%s|%s", user_id, event_name)
            return result
        except Exception:
            logger.exception("FLOW|FAIL|user=%s|%s", user_id, event_name)
            raise
