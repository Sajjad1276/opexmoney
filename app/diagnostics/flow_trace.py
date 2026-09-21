from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware

from app.diagnostics.support_telemetry import record_error, record_event

logger = logging.getLogger("opexmoney.flow")


class FlowTraceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        callback = getattr(event, "data", None)
        text = getattr(event, "text", None)

        if callback:
            event_name = f"callback:{callback}"
        elif text:
            event_name = "message:text"
        else:
            event_name = "event"

        fsm_state = None
        state = data.get("state")
        if state is not None:
            try:
                fsm_state = await state.get_state()
            except Exception:
                fsm_state = None

        if user_id is not None:
            try:
                await record_event(user_id, event_name, fsm_state, "begin")
            except Exception:
                logger.debug("Support event telemetry failed", exc_info=True)

        logger.info(
            "FLOW|BEGIN|user=%s|%s|state=%s",
            user_id if user_id is not None else "unknown",
            event_name,
            fsm_state or "-",
        )

        try:
            result = await handler(event, data)
            if user_id is not None:
                try:
                    await record_event(user_id, event_name, fsm_state, "pass")
                except Exception:
                    logger.debug("Support event telemetry failed", exc_info=True)
            logger.info(
                "FLOW|PASS|user=%s|%s",
                user_id if user_id is not None else "unknown",
                event_name,
            )
            return result
        except Exception as exc:
            if user_id is not None:
                try:
                    await record_event(user_id, event_name, fsm_state, "fail")
                    await record_error(user_id, "flow", exc, event_name)
                except Exception:
                    logger.debug("Support failure telemetry failed", exc_info=True)
            logger.exception(
                "FLOW|FAIL|user=%s|%s",
                user_id if user_id is not None else "unknown",
                event_name,
            )
            raise
