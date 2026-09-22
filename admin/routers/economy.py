from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import (
    BehaviorSnapshotItem,
    MarketStateItem,
    RateHistoryPoint,
    WorldEventItem,
)
from app.database.models import (
    BehaviorSnapshot,
    CurrencyMarketState,
    Nation,
    RateHistory,
    WorldEvent,
)
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/economy", tags=["economy"])


async def _run_all(statement: Any) -> list[Any]:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.all()


async def _run_first(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.first()


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


def _enum_value(value: Any) -> str:
    return getattr(value, "value", str(value)) if value is not None else ""


@router.get("/market-states", response_model=list[MarketStateItem])
@cached(30, "economy:market-states")
async def market_states(
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[MarketStateItem]:
    rate_change = case(
        (CurrencyMarketState.previous_rate != 0,
         (CurrencyMarketState.calculated_rate - CurrencyMarketState.previous_rate)
         / CurrencyMarketState.previous_rate * 100),
        else_=0.0,
    ).label("rate_change")

    stmt = (
        select(
            CurrencyMarketState.currency_code,
            Nation.name.label("nation_name"),
            Nation.flag_emoji.label("nation_flag"),
            CurrencyMarketState.buy_pressure,
            CurrencyMarketState.sell_pressure,
            CurrencyMarketState.liquidity,
            CurrencyMarketState.confidence,
            CurrencyMarketState.volatility,
            CurrencyMarketState.foreign_demand,
            CurrencyMarketState.calculated_rate,
            CurrencyMarketState.previous_rate,
            rate_change,
            CurrencyMarketState.updated_at,
        )
        .outerjoin(Nation, Nation.currency_code == CurrencyMarketState.currency_code)
        .order_by(CurrencyMarketState.calculated_rate.desc())
    )

    rows = await _run_all(stmt)
    return [
        MarketStateItem(
            currency_code=row.currency_code,
            nation_name=row.nation_name or "",
            nation_flag=row.nation_flag,
            buy_pressure=float(row.buy_pressure or 0),
            sell_pressure=float(row.sell_pressure or 0),
            liquidity=float(row.liquidity or 0),
            confidence=float(row.confidence or 0),
            volatility=float(row.volatility or 0),
            foreign_demand=float(row.foreign_demand or 0),
            calculated_rate=float(row.calculated_rate or 0),
            previous_rate=float(row.previous_rate or 0),
            rate_change=float(row.rate_change or 0),
            updated_at=_iso(row.updated_at),
        )
        for row in rows
    ]


@router.get("/rate-history/{nation_id}", response_model=list[RateHistoryPoint])
@cached(60, "economy:ratehist")
async def rate_history(
    nation_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[RateHistoryPoint]:
    nation_stmt = select(Nation.nation_id).where(
        Nation.nation_id == nation_id,
        Nation.deleted_at.is_(None),
    ).limit(1)
    rows_stmt = (
        select(
            RateHistory.rate,
            RateHistory.volume,
            RateHistory.calculated_at,
            RateHistory.dominant_cause,
            RateHistory.pressure_signal,
        )
        .where(
            RateHistory.nation_id == nation_id,
            RateHistory.calculated_at > datetime.now(timezone.utc) - timedelta(hours=hours),
        )
        .order_by(RateHistory.calculated_at.asc(), RateHistory.id.asc())
        .limit(10000)
    )

    nation_row, rows = await __import__("asyncio").gather(
        _run_first(nation_stmt),
        _run_all(rows_stmt),
    )

    if nation_row is None:
        raise HTTPException(status_code=404, detail="Nation not found")

    return [
        RateHistoryPoint(
            rate=float(row.rate or 0),
            volume=float(row.volume or 0),
            calculated_at=_iso(row.calculated_at),
            dominant_cause=row.dominant_cause,
            pressure_signal=float(row.pressure_signal) if row.pressure_signal is not None else None,
        )
        for row in rows
    ]


@router.get("/behavior-snapshots", response_model=list[BehaviorSnapshotItem])
@cached(120, "economy:behavior")
async def behavior_snapshots(
    limit: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[BehaviorSnapshotItem]:
    stmt = (
        select(
            BehaviorSnapshot.at,
            BehaviorSnapshot.active_players_count,
            BehaviorSnapshot.buy_tx_count,
            BehaviorSnapshot.sell_tx_count,
            BehaviorSnapshot.total_volume,
            BehaviorSnapshot.avg_net_worth,
            BehaviorSnapshot.gini_coefficient,
            BehaviorSnapshot.top10_wealth_share,
        )
        .order_by(BehaviorSnapshot.at.desc(), BehaviorSnapshot.id.desc())
        .limit(limit)
    )
    rows = await _run_all(stmt)
    return [
        BehaviorSnapshotItem(
            at=_iso(row.at),
            active_players_count=int(row.active_players_count),
            buy_tx_count=int(row.buy_tx_count),
            sell_tx_count=int(row.sell_tx_count),
            total_volume=float(row.total_volume or 0),
            avg_net_worth=float(row.avg_net_worth or 0),
            gini_coefficient=float(row.gini_coefficient or 0),
            top10_wealth_share=float(row.top10_wealth_share or 0),
        )
        for row in rows
    ]


@router.get("/world-events", response_model=list[WorldEventItem])
@cached(30, "economy:world-events")
async def world_events(
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[WorldEventItem]:
    stmt = (
        select(
            WorldEvent.event_id,
            WorldEvent.event_type,
            WorldEvent.scope,
            WorldEvent.title,
            WorldEvent.description,
            WorldEvent.effect_type,
            WorldEvent.effect_magnitude,
            WorldEvent.started_at,
            WorldEvent.ends_at,
            WorldEvent.is_active,
            Nation.name.label("affected_nation_name"),
            WorldEvent.affected_currency,
        )
        .outerjoin(Nation, Nation.nation_id == WorldEvent.affected_nation_id)
        .order_by(WorldEvent.started_at.desc(), WorldEvent.event_id.desc())
        .limit(50)
    )
    if active_only:
        stmt = stmt.where(WorldEvent.is_active.is_(True))

    rows = await _run_all(stmt)
    return [
        WorldEventItem(
            event_id=str(row.event_id),
            event_type=_enum_value(row.event_type),
            scope=_enum_value(row.scope),
            title=row.title,
            description=row.description,
            effect_type=_enum_value(row.effect_type),
            effect_magnitude=float(row.effect_magnitude or 0),
            started_at=_iso(row.started_at),
            ends_at=_iso(row.ends_at),
            is_active=bool(row.is_active),
            affected_nation_name=row.affected_nation_name,
            affected_currency=row.affected_currency,
        )
        for row in rows
    ]


# ── END OF economy.py ──
