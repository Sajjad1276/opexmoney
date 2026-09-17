from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.database.models import Base
from app.database.session import async_session, engine
from app.handlers.market import router as market_router
from app.handlers.start import router as start_router
from app.services.economic_engine import reset_daily_metrics, update_nation_rates, update_nation_ranks
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
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

        statements = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS xr_balance NUMERIC(14,2) NOT NULL DEFAULT 0",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS rate_prev NUMERIC(10,4) NOT NULL DEFAULT 1.0000",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS rate_24h_open NUMERIC(10,4) NOT NULL DEFAULT 1.0000",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS trade_volume_24h NUMERIC(18,4) NOT NULL DEFAULT 0",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS active_members_24h INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS nation_rank INTEGER",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS last_rate_update TIMESTAMP",
            "ALTER TABLE nations ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE nations ALTER COLUMN exchange_rate TYPE NUMERIC(12,4)",
            """
            DO $$
            BEGIN
                -- Telegram user IDs are 64-bit values. Older deployments created
                -- these columns as INTEGER (int4), which fails for IDs > 2,147,483,647.
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'users'
                      AND column_name = 'user_id'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE currency_holdings DROP CONSTRAINT IF EXISTS currency_holdings_user_id_fkey;
                    ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_user_id_fkey;
                    ALTER TABLE user_activities DROP CONSTRAINT IF EXISTS user_activities_user_id_fkey;
                    ALTER TABLE trade_previews DROP CONSTRAINT IF EXISTS trade_previews_user_id_fkey;

                    ALTER TABLE users ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
                    ALTER TABLE currency_holdings ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
                    ALTER TABLE transactions ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
                    ALTER TABLE user_activities ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
                    ALTER TABLE trade_previews ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;

                    ALTER TABLE currency_holdings
                        ADD CONSTRAINT currency_holdings_user_id_fkey
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;
                    ALTER TABLE transactions
                        ADD CONSTRAINT transactions_user_id_fkey
                        FOREIGN KEY (user_id) REFERENCES users(user_id);
                    ALTER TABLE user_activities
                        ADD CONSTRAINT user_activities_user_id_fkey
                        FOREIGN KEY (user_id) REFERENCES users(user_id);
                    ALTER TABLE trade_previews
                        ADD CONSTRAINT trade_previews_user_id_fkey
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;
                END IF;

                -- Telegram chat IDs and founder user IDs are also 64-bit identifiers.
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'nations'
                      AND column_name = 'group_id'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE nations ALTER COLUMN group_id TYPE BIGINT USING group_id::BIGINT;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'nations'
                      AND column_name = 'founder_user_id'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE nations ALTER COLUMN founder_user_id TYPE BIGINT USING founder_user_id::BIGINT;
                END IF;
            END $$;
            """,
            "INSERT INTO currency_holdings (user_id,nation_id,amount) SELECT user_id,home_nation_id,balance FROM users WHERE home_nation_id IS NOT NULL ON CONFLICT (user_id,nation_id) DO NOTHING",
        ]
        for statement in statements:
            await connection.execute(text(statement))


async def run_rate_job() -> None:
    try:
        async with async_session() as session:
            await update_nation_rates(session)
        logger.info("Nation rate engine completed")
    except Exception:
        logger.exception("Nation rate engine failed")


async def run_rank_job() -> None:
    try:
        async with async_session() as session:
            await update_nation_ranks(session)
        logger.info("Nation rank update completed")
    except Exception:
        logger.exception("Nation rank update failed")


async def run_daily_reset() -> None:
    try:
        async with async_session() as session:
            await reset_daily_metrics(session)
        logger.info("Daily market metrics reset completed")
    except Exception:
        logger.exception("Daily market reset failed")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_rate_job, CronTrigger(minute="*/15"), id="rate_engine_15m", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(run_rank_job, CronTrigger(minute=0), id="nation_rank_hourly", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(run_daily_reset, CronTrigger(hour=0, minute=0), id="daily_market_reset", replace_existing=True, max_instances=1, coalesce=True)
    return scheduler


async def main() -> None:
    await prepare_database()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=build_storage())
    dp.include_router(start_router)
    dp.include_router(market_router)
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("OPEX MONEY is online")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
