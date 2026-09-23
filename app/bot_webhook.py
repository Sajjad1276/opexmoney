from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, MenuButtonCommands

from app.diagnostics.flow_trace import FlowTraceMiddleware
from app.diagnostics.support_telemetry import close_support_telemetry, record_error
from app.handlers.academy import router as academy_router
from app.handlers.admin_panel import router as admin_panel_router
from app.handlers.chart import router as chart_router
from app.handlers.founder import founder_router
from app.handlers.governance import governance_router
from app.handlers.market import router as market_router
from app.handlers.membership import membership_router
from app.handlers.missions import router as missions_router
from app.handlers.nation import nation_router
from app.handlers.nation_management import nation_management_router
from app.handlers.onboarding import router as onboarding_router
from app.handlers.owner_ai import router as owner_ai_router
from app.handlers.portfolio import router as portfolio_router
from app.handlers.profile import router as profile_router
from app.handlers.ranking import router as ranking_router
from app.handlers.sections import router as sections_router
from app.handlers.settings import router as settings_router
from app.handlers.start import router as start_router
from app.handlers.start_flow import router as start_flow_router
from app.handlers.support import router as support_router
from app.handlers.treasury import router as treasury_router
from app.middlewares.command_cleanup import CommandPanelCleanupMiddleware
from config import settings
from app.core.redis import ServerlessRedis, create_redis_client
from app.storage.upstash_fsm import UpstashFSMStorage

logger = logging.getLogger("opexmoney.webhook")


def _build_dispatcher(storage) -> tuple[Dispatcher, ServerlessRedis | None]:
    dp = Dispatcher(storage=storage)

    ranking_redis = (
        create_redis_client()
    )
    dp["redis"] = ranking_redis

    flow_trace = FlowTraceMiddleware()
    dp.message.middleware(CommandPanelCleanupMiddleware())
    dp.message.middleware(flow_trace)
    dp.callback_query.middleware(flow_trace)

    dp.include_router(onboarding_router)
    dp.include_router(founder_router)
    dp.include_router(start_router)
    dp.include_router(start_flow_router)
    dp.include_router(market_router)
    dp.include_router(membership_router)
    dp.include_router(chart_router)
    dp.include_router(missions_router)
    dp.include_router(portfolio_router)
    dp.include_router(profile_router)
    dp.include_router(ranking_router)
    dp.include_router(settings_router)
    dp.include_router(nation_management_router)
    dp.include_router(treasury_router)
    dp.include_router(nation_router)
    dp.include_router(governance_router)
    dp.include_router(sections_router)
    dp.include_router(support_router)
    dp.include_router(admin_panel_router)
    dp.include_router(owner_ai_router)
    dp.include_router(academy_router)

    return dp, ranking_redis


def build_webhook_runtime() -> tuple[Bot, Dispatcher, UpstashFSMStorage, ServerlessRedis]:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    redis = create_redis_client()
    if redis is None:
        raise RuntimeError("Upstash/Redis credentials are not configured for the Vercel webhook runtime")

    storage = UpstashFSMStorage(redis)
    dp, ranking_redis = _build_dispatcher(storage)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    @dp.errors()
    async def errors_handler(event) -> bool:
        update = event.update
        exception = event.exception
        user = getattr(update, "from_user", None)
        if user is not None:
            try:
                await record_error(
                    user.id,
                    "webhook_dispatcher",
                    exception,
                    "callback" if getattr(update, "callback_query", None) else "message",
                )
            except Exception:
                logger.debug(
                    "Support telemetry error recording failed",
                    exc_info=True,
                )
        logger.error(
            "Webhook update %s caused error %s",
            update,
            exception,
            exc_info=True,
        )
        return True

    return bot, dp, storage, ranking_redis


async def configure_webhook_bot_menu(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="بازگشت به منوی اصلی",
            )
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def close_webhook_runtime(
    bot: Bot,
    storage,
    ranking_redis: ServerlessRedis | None,
) -> None:
    try:
        await storage.close()
    except Exception:
        logger.exception("WEBHOOK_RUNTIME|storage_close_failed")

    if ranking_redis is not None:
        try:
            await ranking_redis.aclose()
        except Exception:
            logger.exception("WEBHOOK_RUNTIME|ranking_redis_close_failed")

    try:
        await bot.session.close()
    except Exception:
        logger.exception("WEBHOOK_RUNTIME|bot_session_close_failed")

    try:
        await close_support_telemetry()
    except Exception:
        logger.exception("WEBHOOK_RUNTIME|telemetry_close_failed")
