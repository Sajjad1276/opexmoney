from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import (
    NationDetail,
    NationListItem,
    NationLogItem,
    NationMemberItem,
    NationTreasuryDetail,
    PaginatedResponse,
    RateHistoryPoint,
    WarListItem,
)
from app.database.models import (
    Nation,
    NationLog,
    NationMembership,
    NationWar,
    NationTreasury,
    RateHistory,
    TreasuryLog,
    User,
    WarStatus,
)
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nations", tags=["nations"])


async def _run_all(statement: Any) -> list[Any]:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.all()


async def _run_first(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.first()


async def _run_scalar(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.scalar_one_or_none()


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


def _enum_value(value: Any) -> str:
    return getattr(value, "value", str(value)) if value is not None else ""


@router.get("", response_model=PaginatedResponse[NationListItem])
@cached(30, "nations")
async def list_nations(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="member_count"),
    order: str = Query(default="desc"),
    filter: str = Query(default="active"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PaginatedResponse[NationListItem]:
    war_count = func.count(NationWar.id)
    rate_change = case(
        (Nation.rate_24h_open != 0,
         (Nation.exchange_rate - Nation.rate_24h_open) / Nation.rate_24h_open * 100),
        else_=0.0,
    ).label("rate_change_24h")

    sort_columns = {
        "member_count": Nation.member_count,
        "exchange_rate": Nation.exchange_rate,
        "trade_volume_24h": Nation.trade_volume_24h,
        "treasury": Nation.treasury,
        "created_at": Nation.created_at,
        "nation_rank": Nation.nation_rank,
    }
    sort_column = sort_columns.get(sort, Nation.member_count)
    descending = order.lower() != "asc"

    conditions: list[Any] = []
    normalized_filter = filter.lower()
    if normalized_filter == "active":
        conditions.extend([Nation.is_active.is_(True), Nation.deleted_at.is_(None)])
    elif normalized_filter == "ai":
        conditions.append(Nation.is_ai.is_(True))
    elif normalized_filter != "all":
        conditions.extend([Nation.is_active.is_(True), Nation.deleted_at.is_(None)])

    count_stmt = select(func.count(Nation.nation_id)).where(*conditions)
    data_stmt = (
        select(
            Nation.nation_id,
            Nation.name,
            Nation.flag_emoji,
            Nation.currency_code,
            Nation.exchange_rate,
            rate_change,
            Nation.member_count,
            Nation.treasury,
            Nation.trade_volume_24h,
            Nation.nation_rank,
            Nation.is_active,
            Nation.join_policy,
            Nation.is_ai,
            Nation.created_at,
            Nation.active_members_24h,
            case((war_count > 0, "at_war"), else_="at_peace").label("war_status"),
        )
        .outerjoin(
            NationWar,
            (or_(
                NationWar.nation_id == Nation.nation_id,
                NationWar.opponent_nation_id == Nation.nation_id,
            ))
            & (NationWar.status == WarStatus.ACTIVE),
        )
        .where(*conditions)
        .group_by(
            Nation.nation_id,
            Nation.name,
            Nation.flag_emoji,
            Nation.currency_code,
            Nation.exchange_rate,
            Nation.rate_24h_open,
            Nation.member_count,
            Nation.treasury,
            Nation.trade_volume_24h,
            Nation.nation_rank,
            Nation.is_active,
            Nation.join_policy,
            Nation.is_ai,
            Nation.created_at,
            Nation.active_members_24h,
        )
        .order_by(
            sort_column.desc() if descending else sort_column.asc(),
            Nation.nation_id.asc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
    )

    total, rows = await asyncio.gather(
        _run_scalar(count_stmt),
        _run_all(data_stmt),
    )

    total_int = int(total or 0)
    items = [
        NationListItem(
            nation_id=int(row.nation_id),
            name=row.name,
            flag_emoji=row.flag_emoji,
            currency_code=row.currency_code,
            exchange_rate=float(row.exchange_rate or 0),
            rate_change_24h=float(row.rate_change_24h or 0),
            member_count=int(row.member_count or 0),
            treasury=float(row.treasury or 0),
            trade_volume_24h=float(row.trade_volume_24h or 0),
            nation_rank=int(row.nation_rank) if row.nation_rank is not None else None,
            is_active=bool(row.is_active),
            join_policy=row.join_policy,
            is_ai=bool(row.is_ai),
            created_at=_iso(row.created_at),
            active_members_24h=int(row.active_members_24h or 0),
            war_status=row.war_status,
        )
        for row in rows
    ]
    return PaginatedResponse(
        items=items,
        total=total_int,
        page=page,
        pages=page_count(total_int, limit),
    )


@router.get("/{nation_id}", response_model=NationDetail)
@cached(20, "nations:detail")
async def nation_detail(
    nation_id: int,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> NationDetail:
    nation_stmt = select(
        Nation.nation_id,
        Nation.name,
        Nation.flag_emoji,
        Nation.currency_code,
        Nation.exchange_rate,
        Nation.rate_24h_open,
        Nation.member_count,
        Nation.treasury,
        Nation.trade_volume_24h,
        Nation.nation_rank,
        Nation.is_active,
        Nation.join_policy,
        Nation.is_ai,
        Nation.created_at,
        Nation.active_members_24h,
        Nation.deleted_at,
    ).where(
        Nation.nation_id == nation_id,
        Nation.deleted_at.is_(None),
    ).limit(1)

    history_stmt = (
        select(
            RateHistory.rate,
            RateHistory.volume,
            RateHistory.calculated_at,
            RateHistory.dominant_cause,
            RateHistory.pressure_signal,
        )
        .where(RateHistory.nation_id == nation_id)
        .order_by(RateHistory.calculated_at.desc(), RateHistory.id.desc())
        .limit(20)
    )

    members_stmt = (
        select(
            User.user_id,
            User.username,
            NationMembership.role,
            User.balance,
            NationMembership.joined_at,
        )
        .join(User, User.user_id == NationMembership.user_id)
        .where(
            NationMembership.nation_id == nation_id,
            NationMembership.is_active.is_(True),
        )
        .order_by(User.balance.desc(), User.user_id.asc())
        .limit(5)
    )

    treasury_stmt = (
        select(
            NationTreasury.balance_xr,
            NationTreasury.balance_local,
            NationTreasury.total_deposited,
            NationTreasury.last_deposit_at,
        )
        .where(NationTreasury.nation_id == nation_id)
        .limit(1)
    )

    nation_logs_stmt = (
        select(
            NationLog.action_type,
            User.username,
            NationLog.created_at,
        )
        .outerjoin(User, User.user_id == NationLog.actor_id)
        .where(NationLog.nation_id == nation_id)
        .order_by(NationLog.created_at.desc(), NationLog.id.desc())
        .limit(10)
    )

    treasury_logs_stmt = (
        select(
            TreasuryLog.action,
            User.username,
            TreasuryLog.created_at,
            TreasuryLog.id,
        )
        .outerjoin(User, User.user_id == TreasuryLog.actor_id)
        .where(TreasuryLog.nation_id == nation_id)
        .order_by(TreasuryLog.created_at.desc(), TreasuryLog.id.desc())
        .limit(10)
    )

    nation_alias = aliased(Nation)
    opponent_alias = aliased(Nation)
    wars_stmt = (
        select(
            NationWar.id,
            NationWar.status,
            NationWar.declared_at,
            NationWar.ends_at,
            NationWar.ended_at,
            nation_alias.name,
            nation_alias.flag_emoji,
            nation_alias.member_count,
            opponent_alias.name,
            opponent_alias.flag_emoji,
            opponent_alias.member_count,
        )
        .join(nation_alias, NationWar.nation_id == nation_alias.nation_id)
        .join(opponent_alias, NationWar.opponent_nation_id == opponent_alias.nation_id)
        .where(
            or_(
                NationWar.nation_id == nation_id,
                NationWar.opponent_nation_id == nation_id,
            ),
            NationWar.status == WarStatus.ACTIVE,
        )
        .order_by(NationWar.declared_at.desc(), NationWar.id.desc())
        .limit(100)
    )

    (
        nation_row,
        history_rows,
        member_rows,
        treasury_row,
        nation_log_rows,
        treasury_log_rows,
        war_rows,
    ) = await asyncio.gather(
        _run_first(nation_stmt),
        _run_all(history_stmt),
        _run_all(members_stmt),
        _run_first(treasury_stmt),
        _run_all(nation_logs_stmt),
        _run_all(treasury_logs_stmt),
        _run_all(wars_stmt),
    )

    if nation_row is None:
        raise HTTPException(status_code=404, detail="Nation not found")

    history = [
        RateHistoryPoint(
            rate=float(row.rate or 0),
            volume=float(row.volume or 0),
            calculated_at=_iso(row.calculated_at),
            dominant_cause=row.dominant_cause,
            pressure_signal=float(row.pressure_signal) if row.pressure_signal is not None else None,
        )
        for row in history_rows
    ]

    members = [
        NationMemberItem(
            user_id=int(row.user_id),
            username=row.username or "",
            role=_enum_value(row.role),
            balance=float(row.balance or 0),
            joined_at=_iso(row.joined_at),
        )
        for row in member_rows
    ]

    logs = [
        NationLogItem(
            action_type=row.action_type or "",
            actor_username=row.username,
            created_at=_iso(row.created_at),
        )
        for row in nation_log_rows
    ]
    logs.extend(
        NationLogItem(
            action_type=row.action or "",
            actor_username=row.username,
            created_at=_iso(row.created_at),
        )
        for row in treasury_log_rows
    )
    logs.sort(key=lambda item: item.created_at, reverse=True)
    logs = logs[:10]

    active_wars = []
    for row in war_rows:
        active_wars.append(
            WarListItem(
                war_id=int(row[0]),
                nation_name=row[5],
                nation_flag=row[6],
                opponent_name=row[8],
                opponent_flag=row[9],
                status=_enum_value(row[1]),
                declared_at=_iso(row[2]),
                ends_at=_iso(row[3]),
                ended_at=_iso(row[4]) if row[4] is not None else None,
                nation_member_count=int(row[7] or 0),
                opponent_member_count=int(row[10] or 0),
            )
        )

    rate_open = float(nation_row.rate_24h_open or 0)
    rate_current = float(nation_row.exchange_rate or 0)
    rate_change = (rate_current - rate_open) / rate_open * 100 if rate_open else 0.0

    return NationDetail(
        nation_id=int(nation_row.nation_id),
        name=nation_row.name,
        flag_emoji=nation_row.flag_emoji,
        currency_code=nation_row.currency_code,
        exchange_rate=rate_current,
        rate_change_24h=rate_change,
        member_count=int(nation_row.member_count or 0),
        treasury=float(nation_row.treasury or 0),
        trade_volume_24h=float(nation_row.trade_volume_24h or 0),
        nation_rank=int(nation_row.nation_rank) if nation_row.nation_rank is not None else None,
        is_active=bool(nation_row.is_active),
        join_policy=nation_row.join_policy,
        is_ai=bool(nation_row.is_ai),
        created_at=_iso(nation_row.created_at),
        active_members_24h=int(nation_row.active_members_24h or 0),
        war_status="at_war" if active_wars else "at_peace",
        rate_history=history,
        top_members=members,
        treasury_detail=(
            NationTreasuryDetail(
                balance_xr=float(treasury_row.balance_xr or 0),
                balance_local=float(treasury_row.balance_local or 0),
                total_deposited=float(treasury_row.total_deposited or 0),
                last_deposit_at=(
                    treasury_row.last_deposit_at.isoformat()
                    if treasury_row.last_deposit_at is not None
                    else None
                ),
            )
            if treasury_row is not None
            else None
        ),
        active_wars=active_wars,
        recent_logs=logs,
    )


# ── END OF nations.py ──
