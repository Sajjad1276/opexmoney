"""OPEX MONEY AI companion facade.

Pipeline:
context -> prompt -> Redis cache -> OpenAI -> parser -> fallback.
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_session
from ai.cache import (
    close_cache,
    get_cached_response,
    remember_bot_message,
    set_cached_response,
)
from ai.context_builder import build_user_context
from ai.personality import AI_FALLBACK_MESSAGE, SYSTEM_PERSONALITY
from ai.prompt_engine import build_cache_key, build_dynamic_prompt
from ai.response_parser import parse_ai_response
from config import settings

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("opex.ai")
if not any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logger.handlers):
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "ai_calls.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class AICompanion:
    """High-level AI service for conversational OPEX MONEY responses."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI | None:
        """Create the async OpenAI client only when credentials exist."""
        if not settings.ai_enabled or not settings.openai_api_key:
            return None
        if self._client is None:
            client_kwargs: dict[str, object] = {
                "api_key": settings.openai_api_key,
                "timeout": settings.ai_timeout_seconds,
            }
            if settings.openai_base_url:
                client_kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    async def reply(self, user_id: int, user_message: str, db: AsyncSession) -> str:
        """
        Generate one safe AI response.

        Any failure in context loading, Redis, OpenAI, or parsing falls back
        to a static message. The exception never reaches the Telegram handler.
        """
        started = time.perf_counter()
        clean_message = user_message.strip()
        if not clean_message:
            return AI_FALLBACK_MESSAGE

        try:
            context = await build_user_context(user_id, db)
            cache_key = build_cache_key(
                model=settings.ai_model,
                context=context,
                user_message=clean_message,
            )
            cached = await get_cached_response(cache_key)
            if cached:
                logger.info(
                    "call user_id=%s status=cache_hit latency_ms=%.1f",
                    user_id,
                    (time.perf_counter() - started) * 1000,
                )
                return cached

            client = self._get_client()
            if client is None:
                logger.info(
                    "call user_id=%s status=disabled latency_ms=%.1f",
                    user_id,
                    (time.perf_counter() - started) * 1000,
                )
                return AI_FALLBACK_MESSAGE

            prompt = build_dynamic_prompt(context=context, user_message=clean_message)
            response = await client.responses.create(
                model=settings.ai_model,
                instructions=SYSTEM_PERSONALITY,
                input=prompt,
            )
            parsed = parse_ai_response(response.output_text)

            await set_cached_response(cache_key, parsed.reply)
            await remember_bot_message(user_id, parsed.reply)
            logger.info(
                "call user_id=%s status=success model=%s latency_ms=%.1f",
                user_id,
                settings.ai_model,
                (time.perf_counter() - started) * 1000,
            )
            return parsed.reply
        except Exception:
            logger.exception(
                "call user_id=%s status=fallback latency_ms=%.1f",
                user_id,
                (time.perf_counter() - started) * 1000,
            )
            return AI_FALLBACK_MESSAGE

    async def close(self) -> None:
        """Close OpenAI and Redis resources."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        await close_cache()


companion = AICompanion()


async def get_ai_reply(user_id: int, user_message: str, db: AsyncSession) -> str:
    """Convenience facade for handlers."""
    return await companion.reply(user_id, user_message, db)


async def remember_bot_reply(user_id: int, message: str) -> None:
    """Track a non-AI bot message for future continuity."""
    await remember_bot_message(user_id, message)



ai_router = Router(name="ai_companion")


@ai_router.message(F.text)
async def ai_companion_message(message: Message) -> None:
    """Answer unmatched private chat text with the AI companion."""
    if message.chat.type != "private":
        return

    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    async with async_session() as db:
        reply = await get_ai_reply(message.from_user.id, text, db)

    sent = await message.answer(reply)
    await remember_bot_reply(
        message.from_user.id,
        sent.html_text if getattr(sent, "html_text", None) else reply,
    )


__all__ = [
    "AICompanion",
    "companion",
    "ai_router",
    "get_ai_reply",
    "remember_bot_reply",
]