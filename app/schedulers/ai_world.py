from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database.session import async_session
from app.services.ai_world import run_ai_world_cycle

logger = logging.getLogger(__name__)


async def run_ai_world_job() -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                acted = await run_ai_world_cycle(session)
        logger.info("AI world cycle completed | acted=%s", acted)
    except Exception:
        logger.exception("AI world cycle failed")


def register_ai_world_job(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        run_ai_world_job,
        "cron",
        minute="*/5",
        id="ai_world_5m",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
