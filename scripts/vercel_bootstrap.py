from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _production_base_url(settings) -> str:
    hostname = (
        os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
        or os.getenv("VERCEL_URL")
    )
    configured = settings.telegram_webhook_url
    if not hostname:
        if configured:
            return configured.rstrip("/")
        raise RuntimeError(
            "Vercel production URL is unavailable. Set TELEGRAM_WEBHOOK_URL."
        )

    if not hostname:
        raise RuntimeError(
            "Vercel production URL is unavailable. Set TELEGRAM_WEBHOOK_URL."
        )

    if not hostname.startswith(("http://", "https://")):
        hostname = f"https://{hostname}"

    return hostname.rstrip("/")


async def _verify_redis(settings) -> None:
    if not (settings.upstash_redis_rest_url and settings.upstash_redis_rest_token):
        raise RuntimeError(
            "Upstash REST credentials are missing. Connect Upstash Redis to "
            "the Production Vercel environment."
        )

    from upstash_redis.asyncio import Redis

    redis = Redis(
        url=settings.upstash_redis_rest_url,
        token=settings.upstash_redis_rest_token,
        allow_telemetry=False,
    )
    try:
        pong = await redis.ping()
        if pong != "PONG":
            raise RuntimeError(f"Upstash Redis ping failed: {pong!r}")
    finally:
        close = getattr(redis, "aclose", None) or getattr(redis, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


async def _configure_telegram(settings) -> None:
    from aiogram import Bot

    webhook_url = f"{_production_base_url(settings)}/api/telegram/webhook"
    bot = Bot(token=settings.bot_token)
    try:
        me = await bot.get_me()
        if me.username is None:
            raise RuntimeError("Telegram bot has no username.")

        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=[],
            drop_pending_updates=False,
        )
        info = await bot.get_webhook_info()
        if info.url != webhook_url:
            raise RuntimeError(
                f"Telegram webhook mismatch: expected {webhook_url}, got {info.url}"
            )
        print(
            "VERCEL_BOOTSTRAP|telegram=ok|"
            f"bot=@{me.username}|webhook={webhook_url}",
            flush=True,
        )
    finally:
        await bot.session.close()


def _run_migrations() -> None:
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=False,
        timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic migration failed with exit code {result.returncode}."
        )
    print("VERCEL_BOOTSTRAP|database=migrated|alembic=head", flush=True)


def _verify_runtime_imports() -> None:
    # Import the same FastAPI entrypoint Vercel invokes. This catches missing
    # Python dependencies and import-time configuration failures during build.
    from admin.main import app

    if app.title != "OPEX MONEY Admin API":
        raise RuntimeError("Unexpected FastAPI application was imported.")
    print("VERCEL_BOOTSTRAP|runtime_import=ok", flush=True)


async def _main() -> None:
    if os.getenv("VERCEL_ENV") != "production":
        print(
            f"VERCEL_BOOTSTRAP|skip|environment={os.getenv('VERCEL_ENV')}",
            flush=True,
        )
        return

    from config import settings

    _verify_runtime_imports()
    _run_migrations()

    from sqlalchemy import text
    from app.database.session import async_session

    async with async_session() as session:
        await session.execute(text("SELECT 1"))
    print("VERCEL_BOOTSTRAP|database=ok", flush=True)

    await _verify_redis(settings)
    print("VERCEL_BOOTSTRAP|redis=ok", flush=True)

    await _configure_telegram(settings)
    print("VERCEL_BOOTSTRAP|complete", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
