from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy import text

from app.database.models import Base
from app.database.session import engine
from app.handlers.start import router as start_router
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("opexmoney")


def build_storage():
    if settings.redis_url:
        logger.info("Using Redis FSM storage")
        return RedisStorage.from_url(settings.redis_url)
    logger.warning("REDIS_URL is not configured; using in-memory FSM storage")
    return MemoryStorage()


async def prepare_database() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS xr_balance NUMERIC(14, 2) NOT NULL DEFAULT 0"
            )
        )


async def main() -> None:
    await prepare_database()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=build_storage())
    dp.include_router(start_router)

    logger.info("OPEX MONEY is online")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
