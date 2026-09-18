from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types.error_event import ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.database.session import async_session, engine
from app.handlers.market import router as market_router
from app.handlers.founder import founder_router
from app.handlers.nation import nation_router
from app.handlers.onboarding_fix import router as onboarding_fix_router
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


async def run_rate_job() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                await update_nation_rates(session)
        logger.info("Nation rate engine completed")
    except Exception:
        logger.exception("Nation rate engine failed")


async def run_rank_job() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                await update_nation_ranks(session)
        logger.info("Nation rank update completed")
    except Exception:
        logger.exception("Nation rank update failed")


async def run_daily_reset() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                await reset_daily_metrics(session)
        logger.info("Daily market reset completed")
    except Exception:
        logger.exception("Daily market reset failed")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_rate_job, CronTrigger(minute="*/15"), id="rate_engine_15m", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(run_rank_job, CronTrigger(minute=0), id="nation_rank_hourly", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(run_daily_reset, CronTrigger(hour=0, minute=0), id="daily_market_reset", replace_existing=True, max_instances=1, coalesce=True)
    return scheduler


async def main() -> None:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=build_storage())

    @dp.errors()
    async def errors_handler(event: ErrorEvent):
        update = event.update
        exception = event.exception
        logger.error("Update %s caused error %s", update, exception, exc_info=True)
        try:
            if update.message:
                await update.message.answer("⚠️ یه مشکل موقت پیش اومد. لطفاً دوباره امتحان کن.")
            elif update.callback_query:
                await update.callback_query.answer("⚠️ خطا، دوباره امتحان کن", show_alert=True)
        except Exception:
            logger.exception("Failed to send user-facing error message")
        return True
    dp.include_router(onboarding_fix_router)
    dp.include_router(start_router)
    dp.include_router(market_router)
    dp.include_router(founder_router)
    dp.include_router(nation_router)
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
