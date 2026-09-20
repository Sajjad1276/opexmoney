from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session
from app.services.portfolio_service import get_portfolio_data
from app.handlers.portfolio import build_portfolio_text, portfolio_keyboard

logger = logging.getLogger(__name__)

PORTFOLIO_LIVE_UPDATE_SECONDS = 10


@dataclass(slots=True, frozen=True)
class PortfolioPanel:
    user_id: int
    chat_id: int
    message_id: int


_panels: dict[int, PortfolioPanel] = {}
_panels_lock = asyncio.Lock()


def register_portfolio_panel(*, user_id: int, chat_id: int, message_id: int) -> None:
    _panels[user_id] = PortfolioPanel(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
    )


def unregister_portfolio_panel(user_id: int) -> None:
    _panels.pop(user_id, None)


async def _snapshot_panels() -> list[PortfolioPanel]:
    async with _panels_lock:
        return list(_panels.values())


async def update_open_portfolios(bot: Bot) -> None:
    """Push fresh portfolio state to every currently open dashboard."""
    panels = await _snapshot_panels()
    if not panels:
        return

    async def update_one(panel: PortfolioPanel) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    user = await session.scalar(
                        select(User).where(User.user_id == panel.user_id)
                    )
                    if user is None:
                        unregister_portfolio_panel(panel.user_id)
                        return
                    data = await get_portfolio_data(session, panel.user_id)

            await bot.edit_message_text(
                chat_id=panel.chat_id,
                message_id=panel.message_id,
                text=build_portfolio_text(data),
                reply_markup=portfolio_keyboard(data),
                parse_mode="HTML",
            )
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "message is not modified" in message:
                return
            if "message to edit not found" in message or "message can't be edited" in message:
                unregister_portfolio_panel(panel.user_id)
                return
            logger.warning(
                "Portfolio live update rejected | user_id=%s | error=%s",
                panel.user_id,
                exc,
            )
        except Exception:
            logger.exception(
                "Portfolio live update failed | user_id=%s",
                panel.user_id,
            )

    await asyncio.gather(*(update_one(panel) for panel in panels))


def register_portfolio_live_update_job(
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    scheduler.add_job(
        update_open_portfolios,
        IntervalTrigger(seconds=PORTFOLIO_LIVE_UPDATE_SECONDS),
        args=[bot],
        id="portfolio_live_update_10s",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
