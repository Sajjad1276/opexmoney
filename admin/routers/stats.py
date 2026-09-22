from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import EconomyHealth, OverviewStats
from app.database.models import (
    BehaviorSnapshot,
    CurrencyMarketState,
    Nation,
    NationWar,
    Proposal,
    Transaction,
    User,
    UserActivity,
    WarStatus,
)
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])


async def _run_scalar(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.scalar_one_or_none()


async def _run_row(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.first()


@router.get("/overview", response_model=OverviewStats)
@cached(60, "stats:overview")
async def overview(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> OverviewStats:
    now = datetime.now(timezone.utc)
    today_midnight_utc = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    q1 = select(func.count(User.user_id)).where(
        User.deleted_at.is_(None),
        User.is_ai.is_(False),
    )
    q2 = select(func.count(func.distinct(UserActivity.user_id))).where(
        UserActivity.created_at > now - timedelta(hours=24),
    )
    q3 = select(func.count(Nation.nation_id)).where(
        Nation.is_active.is_(True),
        Nation.deleted_at.is_(None),
    )
    q4 = select(func.count(NationWar.id)).where(
        NationWar.status == WarStatus.ACTIVE,
    )
    q5 = select(func.count(Transaction.id)).where(
        Transaction.created_at >= today_midnight_utc,
    )
    q6 = select(func.coalesce(func.sum(Transaction.spend_xr), 0)).where(
        Transaction.created_at >= today_midnight_utc,
    )
    q7 = select(func.coalesce(func.sum(Nation.trade_volume_24h), 0)).where(
        Nation.is_active.is_(True),
        Nation.deleted_at.is_(None),
    )
    q8 = select(func.count(Proposal.id)).where(
        Proposal.status == "voting",
    )
    q9 = select(func.count(User.user_id)).where(
        User.created_at > now - timedelta(days=7),
        User.is_ai.is_(False),
        User.deleted_at.is_(None),
    )
    q10 = (
        select(
            BehaviorSnapshot.gini_coefficient,
            BehaviorSnapshot.top10_wealth_share,
        )
        .order_by(BehaviorSnapshot.at.desc(), BehaviorSnapshot.id.desc())
        .limit(1)
    )

    (
        total_players,
        active_players_24h,
        total_nations,
        active_wars,
        transactions_today,
        transaction_volume_today,
        total_market_volume,
        pending_proposals,
        new_players_7d,
        latest_behavior,
    ) = await asyncio.gather(
        _run_scalar(q1),
        _run_scalar(q2),
        _run_scalar(q3),
        _run_scalar(q4),
        _run_scalar(q5),
        _run_scalar(q6),
        _run_scalar(q7),
        _run_scalar(q8),
        _run_scalar(q9),
        _run_row(q10),
    )

    gini = float(getattr(latest_behavior, "gini_coefficient", 0) or 0)
    top10 = float(getattr(latest_behavior, "top10_wealth_share", 0) or 0)

    return OverviewStats(
        total_players=int(total_players or 0),
        active_players_24h=int(active_players_24h or 0),
        total_nations=int(total_nations or 0),
        active_wars=int(active_wars or 0),
        transactions_today=int(transactions_today or 0),
        transaction_volume_today=float(transaction_volume_today or 0),
        total_market_volume=float(total_market_volume or 0),
        pending_proposals=int(pending_proposals or 0),
        new_players_7d=int(new_players_7d or 0),
        gini_coefficient=gini,
        top10_wealth_share=top10,
    )


@router.get("/economy-health", response_model=EconomyHealth)
@cached(120, "stats:economy")
async def economy_health(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> EconomyHealth:
    now = datetime.now(timezone.utc)

    market_stmt = select(
        func.avg(CurrencyMarketState.confidence).label("avg_confidence"),
        func.avg(CurrencyMarketState.liquidity).label("avg_liquidity"),
        func.avg(CurrencyMarketState.volatility).label("avg_volatility"),
    )
    current_avg_stmt = select(func.avg(Nation.exchange_rate)).where(
        Nation.is_active.is_(True),
        Nation.deleted_at.is_(None),
    )
    week_ago_stmt = select(func.avg(RateHistory.rate)).where(
        RateHistory.calculated_at >= now - timedelta(days=8),
        RateHistory.calculated_at <= now - timedelta(days=7),
    )
    behavior_stmt = (
        select(BehaviorSnapshot.top10_wealth_share)
        .order_by(BehaviorSnapshot.at.desc(), BehaviorSnapshot.id.desc())
        .limit(1)
    )

    market_row, current_avg, week_ago_avg, latest_wealth_share = await asyncio.gather(
        _run_row(market_stmt),
        _run_scalar(current_avg_stmt),
        _run_scalar(week_ago_stmt),
        _run_scalar(behavior_stmt),
    )

    avg_confidence = float(getattr(market_row, "avg_confidence", 0) or 0)
    avg_liquidity = float(getattr(market_row, "avg_liquidity", 0) or 0)
    avg_volatility = float(getattr(market_row, "avg_volatility", 0) or 0)
    current_rate = float(current_avg or 0)
    week_rate = float(week_ago_avg or 0)

    inflation = 0.0
    if week_rate:
        inflation = (current_rate - week_rate) / week_rate * 100

    wealth_concentration = float(latest_wealth_share or 0)

    return EconomyHealth(
        inflation_rate=round(inflation, 2),
        market_confidence=round(avg_confidence * 100, 1),
        total_liquidity=round(avg_liquidity * 100, 1),
        avg_volatility=round(avg_volatility * 100, 1),
        wealth_concentration=round(wealth_concentration * 100, 1),
        market_stability=round(max(0.0, min(100.0, 100.0 - avg_volatility * 100)), 1),
    )


from app.database.models import RateHistory


# ── END OF stats.py ──
