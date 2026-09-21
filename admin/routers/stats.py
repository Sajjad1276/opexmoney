from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_admin_user
from admin.cache import TTL_ECONOMY_HEALTH, TTL_STATS, cached
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import EconomyHealth, StatsOverview
from app.database.models import (
    BehaviorSnapshot,
    CurrencyMarketState,
    Nation,
    NationWar,
    Proposal,
    RateHistory,
    Transaction,
    User,
    UserActivity,
)
from redis.asyncio import Redis

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _scalar_int(db: AsyncSession, stmt) -> int:
    value = await db.scalar(stmt)
    return int(value or 0)


async def _scalar_decimal(db: AsyncSession, stmt):
    value = await db.scalar(stmt)
    return value if value is not None else 0


@router.get("/overview", response_model=StatsOverview)
@cached(TTL_STATS, "stats-overview")
async def overview(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> StatsOverview:
    now = _now()
    day_ago = now - timedelta(hours=24)
    seven_days_ago = now - timedelta(days=7)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_players = await _scalar_int(
        db,
        select(func.count(User.user_id)).where(
            User.deleted_at.is_(None), User.is_ai.is_(False)
        ),
    )
    active_players_24h = await _scalar_int(
        db,
        select(func.count(func.distinct(UserActivity.user_id))).where(
            UserActivity.created_at > day_ago
        ),
    )
    total_nations = await _scalar_int(
        db,
        select(func.count(Nation.nation_id)).where(
            Nation.is_active.is_(True), Nation.deleted_at.is_(None)
        ),
    )
    active_wars = await _scalar_int(
        db,
        select(func.count(NationWar.id)).where(NationWar.status == "active"),
    )
    transactions_today = await _scalar_int(
        db,
        select(func.count(Transaction.id)).where(Transaction.created_at >= midnight),
    )
    transaction_volume_today = await _scalar_decimal(
        db,
        select(func.coalesce(func.sum(Transaction.spend_xr), 0)).where(
            Transaction.created_at >= midnight
        ),
    )
    total_market_volume = await _scalar_decimal(
        db,
        select(func.coalesce(func.sum(Nation.trade_volume_24h), 0)).where(
            Nation.is_active.is_(True), Nation.deleted_at.is_(None)
        ),
    )
    pending_proposals = await _scalar_int(
        db,
        select(func.count(Proposal.id)).where(Proposal.status == "voting"),
    )
    new_players_7d = await _scalar_int(
        db,
        select(func.count(User.user_id)).where(
            User.created_at > seven_days_ago,
            User.is_ai.is_(False),
        ),
    )
    snapshot = await db.execute(
        select(BehaviorSnapshot.gini_coefficient, BehaviorSnapshot.top10_wealth_share)
        .order_by(BehaviorSnapshot.at.desc())
        .limit(1)
    )
    latest = snapshot.one_or_none()

    return StatsOverview(
        total_players=total_players,
        active_players_24h=active_players_24h,
        total_nations=total_nations,
        active_wars=active_wars,
        transactions_today=transactions_today,
        transaction_volume_today=transaction_volume_today,
        total_market_volume=total_market_volume,
        pending_proposals=pending_proposals,
        new_players_7d=new_players_7d,
        gini_coefficient=float(latest[0]) if latest else 0.0,
        top10_wealth_share=float(latest[1]) if latest else 0.0,
    )


@router.get("/economy-health", response_model=EconomyHealth)
@cached(TTL_ECONOMY_HEALTH, "stats-economy-health")
async def economy_health(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> EconomyHealth:
    now = _now()
    seven_days_ago = now - timedelta(days=7)

    current_avg = await db.scalar(
        select(func.avg(Nation.exchange_rate)).where(
            Nation.is_active.is_(True), Nation.deleted_at.is_(None)
        )
    )
    avg_7d = await db.scalar(
        select(func.avg(RateHistory.rate)).where(RateHistory.calculated_at >= seven_days_ago)
    )
    confidence = await db.scalar(select(func.avg(CurrencyMarketState.confidence)))
    liquidity = await db.scalar(select(func.avg(CurrencyMarketState.liquidity)))
    volatility = await db.scalar(select(func.avg(CurrencyMarketState.volatility)))
    latest_snapshot = await db.scalar(
        select(BehaviorSnapshot.top10_wealth_share)
        .order_by(BehaviorSnapshot.at.desc())
        .limit(1)
    )

    current = float(current_avg or 0)
    historical = float(avg_7d or 0)
    inflation_rate = ((current - historical) / historical * 100) if historical else 0.0
    avg_volatility = float(volatility or 0)
    stability = max(0.0, min(100.0, 100.0 - avg_volatility))

    return EconomyHealth(
        inflation_rate=inflation_rate,
        market_confidence=float(confidence or 0),
        total_liquidity=float(liquidity or 0),
        avg_volatility=avg_volatility,
        wealth_concentration=float(latest_snapshot or 0) * 100,
        market_stability=stability,
    )
