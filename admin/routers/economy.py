from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_admin_user
from admin.cache import (
    TTL_BEHAVIOR,
    TTL_MARKET_STATES,
    TTL_RATE_HISTORY,
    TTL_WORLD_EVENTS,
    cached,
)
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import (
    BehaviorSnapshotItem,
    MarketStateItem,
    RateHistoryItem,
    WorldEventItem,
)
from app.database.models import (
    BehaviorSnapshot,
    CurrencyMarketState,
    Nation,
    RateHistory,
    WorldEvent,
)
from redis.asyncio import Redis

router = APIRouter(prefix="/api/economy", tags=["economy"])


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@router.get("/market-states", response_model=list[MarketStateItem])
@cached(TTL_MARKET_STATES, "economy-market-states")
async def market_states(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[MarketStateItem]:
    rows = (
        await db.execute(
            select(CurrencyMarketState, Nation.name)
            .outerjoin(Nation, Nation.currency_code == CurrencyMarketState.currency_code)
            .order_by(CurrencyMarketState.currency_code.asc())
        )
    ).all()
    return [
        MarketStateItem(
            currency_code=state.currency_code,
            nation_name=nation_name,
            buy_pressure=state.buy_pressure,
            sell_pressure=state.sell_pressure,
            liquidity=state.liquidity,
            confidence=state.confidence,
            volatility=state.volatility,
            foreign_demand=state.foreign_demand,
            national_activity=state.national_activity,
            calculated_rate=state.calculated_rate,
            previous_rate=state.previous_rate,
            updated_at=state.updated_at,
        )
        for state, nation_name in rows
    ]


@router.get("/rate-history/{nation_id}", response_model=list[RateHistoryItem])
@cached(TTL_RATE_HISTORY, "economy-rate-history")
async def rate_history(
    nation_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[RateHistoryItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    nation = await db.scalar(select(Nation).where(Nation.nation_id == nation_id))
    if nation is None:
        raise HTTPException(status_code=404, detail="Nation not found")

    rows = (
        await db.execute(
            select(RateHistory)
            .where(
                RateHistory.nation_id == nation_id,
                RateHistory.calculated_at >= cutoff,
            )
            .order_by(RateHistory.calculated_at.desc())
        )
    ).scalars().all()
    return [
        RateHistoryItem(
            id=item.id,
            nation_id=item.nation_id,
            nation_name=nation.name,
            rate=item.rate,
            volume=item.volume,
            active_members=item.active_members,
            calculated_at=item.calculated_at,
            dominant_cause=item.dominant_cause,
            pressure_signal=item.pressure_signal,
            foreign_signal=item.foreign_signal,
            activity_score=item.activity_score,
            trade_score=item.trade_score,
            growth_score=item.growth_score,
        )
        for item in rows
    ]


@router.get("/behavior-snapshots", response_model=list[BehaviorSnapshotItem])
@cached(TTL_BEHAVIOR, "economy-behavior")
async def behavior_snapshots(
    limit: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[BehaviorSnapshotItem]:
    rows = (
        await db.execute(
            select(BehaviorSnapshot)
            .order_by(BehaviorSnapshot.at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        BehaviorSnapshotItem(
            id=item.id,
            at=item.at,
            active_players_count=item.active_players_count,
            buy_tx_count=item.buy_tx_count,
            sell_tx_count=item.sell_tx_count,
            export_tx_count=item.export_tx_count,
            import_tx_count=item.import_tx_count,
            total_volume=item.total_volume,
            avg_net_worth=item.avg_net_worth,
            median_net_worth=item.median_net_worth,
            gini_coefficient=item.gini_coefficient,
            top10_wealth_share=item.top10_wealth_share,
        )
        for item in rows
    ]


@router.get("/world-events", response_model=list[WorldEventItem])
@cached(TTL_WORLD_EVENTS, "economy-world-events")
async def world_events(
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[WorldEventItem]:
    stmt = select(WorldEvent)
    if active_only:
        stmt = stmt.where(WorldEvent.is_active.is_(True))
    rows = (await db.execute(stmt.order_by(WorldEvent.started_at.desc()))).scalars().all()
    return [
        WorldEventItem(
            event_id=item.event_id,
            event_type=_enum_value(item.event_type),
            scope=_enum_value(item.scope),
            affected_nation_id=item.affected_nation_id,
            affected_currency=item.affected_currency,
            title=item.title,
            description=item.description,
            effect_type=_enum_value(item.effect_type),
            effect_magnitude=item.effect_magnitude,
            duration_minutes=item.duration_minutes,
            started_at=item.started_at,
            ends_at=item.ends_at,
            is_active=item.is_active,
            source=_enum_value(item.source),
            announced_in_group=item.announced_in_group,
        )
        for item in rows
    ]
