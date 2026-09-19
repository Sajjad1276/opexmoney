from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database.session import async_session
from app.services.alert_service import check_price_alerts

logger = logging.getLogger(__name__)


async def run_price_alert_checker(bot: Bot) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                triggered = await check_price_alerts(session, bot)

        logger.info(
            "Price alert checker completed | triggered=%s",
            triggered,
        )
    except Exception:
        logger.exception("Price alert checker failed")


def register_price_alert_job(
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    scheduler.add_job(
        run_price_alert_checker,
        "cron",
        minute="*/5",
        args=[bot],
        id="price_alert_checker_5m",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
