"""OPEX MONEY AI companion.

Pipeline:
context -> prompt -> Redis cache -> Gemini API -> parser -> fallback.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message
from google import genai
from google.genai import types
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
if not any(
    isinstance(handler, logging.handlers.RotatingFileHandler)
    for handler in logger.handlers
):
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "ai_calls.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(stream_handler)

logger.setLevel(logging.INFO)
logger.propagate = False


class AICompanion:
    """High-level AI service for conversational OPEX MONEY responses."""

    def __init__(self) -> None:
        self._client: genai.Client | None = None

    @staticmethod
    def _api_key() -> str | None:
        """Read the configured API key with environment fallbacks."""
        candidates = (
            settings.gemini_api_key,
            os.getenv("GEMINI_API_KEY"),
            os.getenv("GOOGLE_API_KEY"),
            os.getenv("GOOGLE_GEMINI_API_KEY"),
        )
        for value in candidates:
            if value and value.strip():
                return value.strip()
        return None

    def _get_client(self) -> genai.Client | None:
        """Create the Gemini client only when AI is enabled and credentials exist."""
        api_key = self._api_key()
        if not settings.ai_enabled or not api_key:
            return None

        if self._client is None:
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=settings.ai_timeout_seconds,
                ),
            )
        return self._client

    def _candidate_models(self) -> list[str]:
        """Return the configured model first, then known current fallbacks."""
        candidates = [
            settings.ai_model,
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
        ]
        unique: list[str] = []
        for model in candidates:
            if model and model not in unique:
                unique.append(model)
        return unique

    async def health_check(self) -> bool:
        """Verify the API key by making a real, tiny Gemini generation call."""
        if not settings.ai_enabled:
            logger.warning("health_check status=disabled reason=AI_ENABLED=false")
            return False

        if not self._api_key():
            logger.error(
                "health_check status=failed reason=missing_api_key "
                "expected=GEMINI_API_KEY"
            )
            return False

        client = self._get_client()
        if client is None:
            logger.error("health_check status=failed reason=client_not_created")
            return False

        for model_name in self._candidate_models():
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents="پاسخ فقط با کلمه «فعال» بده.",
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=8,
                    ),
                )
                if response.text:
                    if model_name != settings.ai_model:
                        logger.warning(
                            "health_check fallback_model=%s configured_model=%s",
                            model_name,
                            settings.ai_model,
                        )
                    else:
                        logger.info(
                            "health_check status=success provider=gemini model=%s",
                            model_name,
                        )
                    return True
            except Exception as exc:
                logger.warning(
                    "health_check model_failed model=%s error_type=%s error=%s",
                    model_name,
                    type(exc).__name__,
                    str(exc)[:1000],
                )
                continue

        logger.error(
            "health_check status=failed provider=gemini models=%s",
            ",".join(self._candidate_models()),
        )
        return False

    async def reply(
        self,
        user_id: int,
        user_message: str,
        db: AsyncSession,
    ) -> str:
        """
        Generate one safe AI response.

        Any failure in context loading, Redis, Gemini, or parsing falls back
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
                reason = (
                    "disabled"
                    if not settings.ai_enabled
                    else "missing_api_key"
                    if not self._api_key()
                    else "client_not_created"
                )
                logger.warning(
                    "call user_id=%s status=fallback reason=%s latency_ms=%.1f",
                    user_id,
                    reason,
                    (time.perf_counter() - started) * 1000,
                )
                return AI_FALLBACK_MESSAGE

            prompt = build_dynamic_prompt(
                context=context,
                user_message=clean_message,
            )

            last_error: Exception | None = None
            response = None
            used_model = settings.ai_model
            for model_name in self._candidate_models():
                try:
                    response = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PERSONALITY,
                            temperature=0.7,
                            max_output_tokens=300,
                        ),
                    )
                    used_model = model_name
                    if model_name != settings.ai_model:
                        logger.warning(
                            "call user_id=%s model_fallback from=%s to=%s",
                            user_id,
                            settings.ai_model,
                            model_name,
                        )
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "call user_id=%s model_failed model=%s error_type=%s error=%s",
                        user_id,
                        model_name,
                        type(exc).__name__,
                        str(exc)[:1000],
                    )

            if response is None:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("No Gemini model produced a response")

            raw_output = response.text or ""
            parsed = parse_ai_response(raw_output)

            await set_cached_response(cache_key, parsed.reply)
            await remember_bot_message(user_id, parsed.reply)
            logger.info(
                "call user_id=%s status=success provider=gemini model=%s latency_ms=%.1f",
                user_id,
                used_model,
                (time.perf_counter() - started) * 1000,
            )
            return parsed.reply
        except Exception as exc:
            logger.exception(
                "call user_id=%s status=fallback provider=gemini model=%s "
                "error_type=%s latency_ms=%.1f",
                user_id,
                settings.ai_model,
                type(exc).__name__,
                (time.perf_counter() - started) * 1000,
            )
            return AI_FALLBACK_MESSAGE

    async def close(self) -> None:
        """Close the Gemini async client and Redis resources."""
        if self._client is not None:
            await self._client.aio.aclose()
            self._client = None
        await close_cache()


companion = AICompanion()


async def get_ai_reply(
    user_id: int,
    user_message: str,
    db: AsyncSession,
) -> str:
    """Convenience facade for handlers."""
    return await companion.reply(user_id, user_message, db)


async def remember_bot_reply(user_id: int, message: str) -> None:
    """Track a non-AI bot message for future continuity."""
    await remember_bot_message(user_id, message)


ai_router = Router(name="ai")


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
