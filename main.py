from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
    Message,
)
from aiogram.types.error_event import ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ai import companion
from app.database.models import User
from app.database.session import async_session, engine
from app.diagnostics.flow_trace import FlowTraceMiddleware
from app.diagnostics.support_telemetry import close_support_telemetry, record_error
from app.diagnostics.self_test import run_startup_smoke_test
from app.handlers.founder import founder_router
from app.handlers.governance import governance_router
from app.handlers.academy import router as academy_router
from app.handlers.admin_panel import router as admin_panel_router
from app.handlers.chart import router as chart_router
from app.handlers.market import router as market_router
from app.handlers.membership import membership_router
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
from app.handlers.onboarding import router as onboarding_router
from app.handlers.start_flow import router as start_flow_router
from app.handlers.sections import router as sections_router
from app.handlers.start import router as start_router
from app.handlers.support import router as support_router
from app.handlers.treasury import router as treasury_router
from app.services.economic_engine import (
    create_behavior_snapshot,
    reset_daily_metrics,
    update_nation_rates,
    update_nation_ranks,
)
from app.services.governance_service import governance_cycle
from app.services.membership_service import reconcile_human_nation_member_counts
from app.services.war_service import resolve_expired_wars
from app.services.ai_world import ensure_ai_world, run_ai_world_cycle
from app.schedulers.alert_checker import register_price_alert_job
from app.schedulers.ai_world import register_ai_world_job
from app.services.portfolio_live import register_portfolio_live_update_job
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


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(
            command="start",
            description="بازگشت به منوی اصلی",
        )
    ]


async def configure_bot_menu(bot: Bot) -> None:
    await bot.set_my_commands(
        build_bot_commands(),
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_chat_menu_button(
        menu_button=MenuButtonCommands(),
    )


async def ensure_ai_population() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                result = await ensure_ai_world(session)
        logger.info(
            "AI world ready | nations=%s users=%s holdings=%s memberships=%s",
            result["nations"],
            result["users"],
            result["holdings"],
            result["memberships"],
        )
    except Exception:
        logger.exception("AI world seeding failed")
        raise


async def run_ai_warmup() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                acted = await run_ai_world_cycle(session, warmup=True)
        logger.info("AI world warmup completed | acted=%s", acted)
    except Exception:
        logger.exception("AI world warmup failed")


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


async def run_membership_reconciliation(bot: Bot) -> None:
    try:
        updated = await reconcile_human_nation_member_counts(bot)
        logger.info(
            "Nation membership reconciliation completed | updated=%s",
            updated,
        )
    except Exception:
        logger.exception("Nation membership reconciliation failed")


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
        run_membership_reconciliation,
        CronTrigger(minute="*/15"),
        args=[bot],
        id="nation_membership_reconciliation_15m",
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
    # AI civilization is opt-in. Startup must never create nations or users.
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await configure_bot_menu(bot)
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
        user = getattr(update, "from_user", None)
        if user is not None:
            try:
                await record_error(
                    user.id,
                    "dispatcher",
                    exception,
                    "callback" if getattr(update, "callback_query", None) else "message",
                )
            except Exception:
                logger.debug("Support telemetry error recording failed", exc_info=True)
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

    dp.include_router(onboarding_router)
    # Founder must precede the generic /start router so group deep-links
    # (/start founder_<token>) reach the founder flow instead of being
    # consumed as a normal start command.
    dp.include_router(founder_router)
    dp.include_router(start_router)
    dp.include_router(start_flow_router)
    dp.include_router(market_router)
    dp.include_router(membership_router)
    dp.include_router(chart_router)
    dp.include_router(missions_router)
    dp.include_router(portfolio_router)
    dp.include_router(ranking_router)
    dp.include_router(settings_router)
    dp.include_router(nation_management_router)
    dp.include_router(treasury_router)
    dp.include_router(nation_router)
    dp.include_router(governance_router)
    dp.include_router(sections_router)
    dp.include_router(support_router)
    # Add admin Mini App router beside the other top-level private-chat routers.
    dp.include_router(admin_panel_router)
    dp.include_router(academy_router)

    ai_ok = await companion.health_check()
    if not ai_ok:
        logger.error(
            "AI|Gemini health check failed; private-chat AI will use fallback "
            "until the API configuration is fixed"
        )

    scheduler = build_scheduler(bot)
    register_price_alert_job(scheduler, bot)
    register_ai_world_job(scheduler)
    register_portfolio_live_update_job(scheduler, bot)
    await run_ai_warmup()
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
        await close_support_telemetry()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())