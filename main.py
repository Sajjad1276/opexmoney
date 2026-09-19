from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types.error_event import ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ai import ai_router, companion
from app.database.models import User
from app.database.session import async_session, engine
from app.diagnostics.flow_trace import FlowTraceMiddleware
from app.diagnostics.self_test import run_startup_smoke_test
from app.handlers.founder import founder_router
from app.handlers.governance import governance_router
from app.handlers.academy import router as academy_router
from app.handlers.chart import router as chart_router
from app.handlers.market import router as market_router
from app.handlers.missions import router as missions_router
from app.handlers.portfolio import router as portfolio_router
from app.handlers.ranking import router as ranking_router
from app.handlers.settings import router as settings_router
from app.handlers.nation import nation_router
from app.handlers.nation_management import (
    expire_join_requests,
    nation_management_router,
    send_weekly_nation_reports,
)
from app.handlers.onboarding_fix import router as onboarding_fix_router
from app.handlers.sections import router as sections_router
from app.handlers.start import router as start_router
from app.handlers.treasury import router as treasury_router
from app.services.economic_engine import (
    create_behavior_snapshot,
    reset_daily_metrics,
    update_nation_rates,
    update_nation_ranks,
)
from app.services.governance_service import governance_cycle
from app.services.war_service import resolve_expired_wars
from app.schedulers.alert_checker import register_price_alert_job
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("opexmoney")


async def ensure_database_schema() -> None:
    """Repair legacy Alembic state and apply all pending migrations before startup."""
    root = Path(__file__).resolve().parent

    repair = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / "repair_alembic_state.py"),
        cwd=str(root),
    )
    repair_code = await repair.wait()
    if repair_code != 0:
        raise RuntimeError(f"Alembic state repair failed with exit code {repair_code}")

    upgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=str(root),
    )
    upgrade_code = await upgrade.wait()
    if upgrade_code != 0:
        raise RuntimeError(f"Alembic upgrade failed with exit code {upgrade_code}")

    logger.info("DATABASE|schema ready|alembic=head")


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

    try:
        async with async_session() as session:
            async with session.begin():
                await create_behavior_snapshot(session)
        logger.info("Behavior snapshot completed")
    except Exception:
        logger.exception("Behavior snapshot failed")


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


async def run_governance_job(bot: Bot) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                result = await governance_cycle(session)
                suspended = result.circuit_suspended
            if suspended:
                founder_ids = list(
                    (
                        await session.execute(
                            select(User.user_id).where(User.role == "founder")
                        )
                    ).scalars().all()
                )
        if suspended:
            for founder_id in founder_ids:
                try:
                    await bot.send_message(
                        founder_id,
                        "🚨 <b>ترمز ایمنی اقتصاد فعال شد.</b>\n"
                        "جهش غیرعادی ثروت شناسایی شد و قوانین فعال برای مدت کوتاه معلق شدند.\n"
                        "بعد از پایدار شدن بازار، قوانین دوباره بررسی میشن.",
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.exception(
                        "Could not notify founder %s about circuit breaker",
                        founder_id,
                    )
        logger.info(
            "Governance cycle completed | activated=%s revoked=%s suspended=%s temporal=%s",
            result.activated,
            result.revoked,
            result.circuit_suspended,
            result.temporal_rotated,
        )
    except Exception:
        logger.exception("Governance cycle failed")



async def run_war_resolution(bot: Bot) -> None:
    try:
        resolved = await resolve_expired_wars(bot)
        logger.info("Nation war resolution completed | resolved=%s", resolved)
    except Exception:
        logger.exception("Nation war resolution failed")


async def run_nation_join_expiration(bot: Bot) -> None:
    try:
        expired = await expire_join_requests(bot)
        logger.info("Nation join-request expiration completed | expired=%s", expired)
    except Exception:
        logger.exception("Nation join-request expiration failed")


async def run_weekly_nation_reports(bot: Bot) -> None:
    try:
        sent = await send_weekly_nation_reports(bot)
        logger.info("Weekly nation AI reports completed | sent=%s", sent)
    except Exception:
        logger.exception("Weekly nation AI reports failed")


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_rate_job,
        CronTrigger(minute="*/15"),
        id="rate_engine_15m",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_rank_job,
        CronTrigger(minute=0),
        id="nation_rank_hourly",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_governance_job,
        CronTrigger(minute=5),
        args=[bot],
        id="governance_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_daily_reset,
        CronTrigger(hour=0, minute=0),
        id="daily_market_reset",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_war_resolution,
        CronTrigger(minute="*/15"),
        args=[bot],
        id="nation_war_resolution_15m",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_nation_join_expiration,
        CronTrigger(minute="*/15"),
        args=[bot],
        id="nation_join_request_expiration",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_weekly_nation_reports,
        CronTrigger(
            day_of_week="mon",
            hour=9,
            minute=0,
            timezone=settings.temporal_timezone,
        ),
        args=[bot],
        id="nation_weekly_ai_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def main() -> None:
    # Intentional: Railway currently has no pre-deploy hook configured, so migrations stay here until one is properly configured.
    await ensure_database_schema()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=build_storage())
    ranking_redis = Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
    dp["redis"] = ranking_redis
    flow_trace = FlowTraceMiddleware()
    dp.message.middleware(flow_trace)
    dp.callback_query.middleware(flow_trace)

    @dp.errors()
    async def errors_handler(event: ErrorEvent):
        update = event.update
        exception = event.exception
        logger.error(
            "Update %s caused error %s",
            update,
            exception,
            exc_info=True,
        )
        try:
            if update.message:
                await update.message.answer(
                    "⚠️ یه مشکل موقت پیش اومد. لطفاً دوباره امتحان کن."
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    "⚠️ خطا، دوباره امتحان کن",
                    show_alert=True,
                )
        except Exception:
            logger.exception("Failed to send user-facing error message")
        return True

    dp.include_router(onboarding_fix_router)
    dp.include_router(start_router)
    dp.include_router(market_router)
    dp.include_router(chart_router)
    dp.include_router(missions_router)
    dp.include_router(portfolio_router)
    dp.include_router(ranking_router)
    dp.include_router(settings_router)
    dp.include_router(founder_router)
    dp.include_router(nation_management_router)
    dp.include_router(treasury_router)
    dp.include_router(nation_router)
    dp.include_router(governance_router)
    dp.include_router(sections_router)
    dp.include_router(academy_router)
    dp.include_router(ai_router)

    ai_ok = await companion.health_check()
    if not ai_ok:
        logger.error(
            "AI|Gemini health check failed; private-chat AI will use fallback "
            "until the API configuration is fixed"
        )

    scheduler = build_scheduler(bot)
    register_price_alert_job(scheduler, bot)
    smoke_ok = await run_startup_smoke_test(dp, scheduler)
    if not smoke_ok:
        logger.error(
            "Startup smoke test failed; bot will continue only for diagnosis"
        )

    scheduler.start()
    logger.info("OPEX MONEY is online")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        scheduler.shutdown(wait=False)
        if ranking_redis is not None:
            await ranking_redis.aclose()
        await companion.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
