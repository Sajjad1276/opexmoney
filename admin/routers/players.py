from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import PaginatedResponse, PlayerDetail, PlayerListItem, TransactionItem
from app.database.models import (
    AIUsageLog,
    CurrencyHolding,
    Nation,
    NationMembership,
    Transaction,
    User,
    UserMissionProgress,
    UserXP,
)
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/players", tags=["players"])


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


def _enum_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return getattr(value, "value", str(value))


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


async def _player_list_rows(
    conditions: list[Any],
    sort_column: Any,
    descending: bool,
    page: int,
    limit: int,
) -> list[Any]:
    tx_count = (
        select(func.count(Transaction.id))
        .where(Transaction.user_id == User.user_id)
        .correlate(User)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(UserActivity.created_at))
        .where(UserActivity.user_id == User.user_id)
        .correlate(User)
        .scalar_subquery()
    )
    stmt = (
        select(
            User.user_id,
            User.username,
            User.balance,
            User.xr_balance,
            User.role,
            User.ai_tier,
            Nation.name.label("home_nation_name"),
            Nation.flag_emoji.label("home_nation_flag"),
            User.created_at,
            User.is_ai,
            tx_count.label("total_transactions"),
            last_activity.label("last_activity_at"),
        )
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .where(*conditions)
        .order_by(
            sort_column.desc() if descending else sort_column.asc(),
            User.user_id.asc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return await _run_all(stmt)


from app.database.models import UserActivity


@router.get("", response_model=PaginatedResponse[PlayerListItem])
@cached(30, "players")
async def list_players(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="balance"),
    order: str = Query(default="desc"),
    search: str = Query(default=""),
    filter: str = Query(default="active"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PaginatedResponse[PlayerListItem]:
    sort_columns = {
        "balance": User.balance,
        "xr_balance": User.xr_balance,
        "created_at": User.created_at,
        "username": User.username,
    }
    sort_column = sort_columns.get(sort, User.balance)
    descending = order.lower() != "asc"

    conditions: list[Any] = []
    normalized_filter = filter.lower()
    if normalized_filter == "active":
        conditions.extend([User.deleted_at.is_(None), User.is_ai.is_(False)])
    elif normalized_filter == "banned":
        conditions.append(User.deleted_at.is_not(None))
    elif normalized_filter == "ai":
        conditions.append(User.is_ai.is_(True))
    elif normalized_filter != "all":
        conditions.extend([User.deleted_at.is_(None), User.is_ai.is_(False)])

    if search.strip():
        conditions.append(User.username.ilike(f"%{search.strip()}%"))

    count_stmt = select(func.count(User.user_id)).where(*conditions)
    rows_stmt_task = _player_list_rows(
        conditions,
        sort_column,
        order.lower() == "desc",
        page,
        limit,
    )
    total, rows = await asyncio.gather(
        _run_scalar(count_stmt),
        rows_stmt_task,
    )

    items = [
        PlayerListItem(
            user_id=int(row.user_id),
            username=row.username or "",
            balance=float(row.balance or 0),
            xr_balance=float(row.xr_balance or 0),
            role=_enum_value(row.role, "player"),
            ai_tier=_enum_value(row.ai_tier, "bronze"),
            home_nation_name=row.home_nation_name,
            home_nation_flag=row.home_nation_flag,
            created_at=_iso(row.created_at),
            is_ai=bool(row.is_ai),
            total_transactions=int(row.total_transactions or 0),
            last_activity_at=row.last_activity_at.isoformat() if row.last_activity_at else None,
        )
        for row in rows
    ]
    total_int = int(total or 0)
    return PaginatedResponse(
        items=items,
        total=total_int,
        page=page,
        pages=page_count(total_int, limit),
    )


@router.get("/{user_id}", response_model=PlayerDetail)
@cached(15, "players:detail")
async def player_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PlayerDetail:
    user_stmt = (
        select(
            User.user_id,
            User.username,
            User.balance,
            User.xr_balance,
            User.role,
            User.ai_tier,
            User.created_at,
            User.is_ai,
            User.deleted_at,
            Nation.name.label("home_nation_name"),
            Nation.flag_emoji.label("home_nation_flag"),
        )
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .where(User.user_id == user_id)
        .limit(1)
    )
    xp_stmt = select(UserXP.total_xp, UserXP.level).where(UserXP.user_id == user_id).limit(1)
    membership_stmt = (
        select(NationMembership.role)
        .where(
            NationMembership.user_id == user_id,
            NationMembership.is_active.is_(True),
        )
        .order_by(NationMembership.id.desc())
        .limit(1)
    )
    tx_stmt = (
        select(
            Transaction.id,
            Transaction.transaction_type,
            Transaction.spend_xr,
            Transaction.amount,
            Transaction.fee_xr,
            Transaction.rate,
            Nation.name,
            Nation.flag_emoji,
            Transaction.created_at,
        )
        .join(Nation, Nation.nation_id == Transaction.nation_id)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(10)
    )
    mission_stmt = select(func.count(UserMissionProgress.id)).where(
        UserMissionProgress.user_id == user_id,
        UserMissionProgress.completed.is_(True),
    )
    usage_stmt = select(
        AIUsageLog.advisor_questions_used,
        AIUsageLog.portfolio_scans_used,
        AIUsageLog.war_analysis_used,
    ).where(
        AIUsageLog.user_id == user_id,
        AIUsageLog.date == date.today(),
    ).limit(1)

    user_row, xp_row, membership_row, tx_rows, mission_count, usage_row = await asyncio.gather(
        _run_first(user_stmt),
        _run_first(xp_stmt),
        _run_first(membership_stmt),
        _run_all(tx_stmt),
        _run_scalar(mission_stmt),
        _run_first(usage_stmt),
    )

    if user_row is None:
        raise HTTPException(status_code=404, detail="Player not found")

    transactions = [
        TransactionItem(
            id=int(row.id),
            transaction_type=row.transaction_type,
            spend_xr=float(row.spend_xr or 0),
            amount=float(row.amount or 0),
            fee_xr=float(row.fee_xr or 0),
            rate=float(row.rate or 0),
            nation_name=row.name or "",
            nation_flag=row.flag_emoji,
            created_at=_iso(row.created_at),
        )
        for row in tx_rows
    ]

    return PlayerDetail(
        user_id=int(user_row.user_id),
        username=user_row.username or "",
        balance=float(user_row.balance or 0),
        xr_balance=float(user_row.xr_balance or 0),
        role=_enum_value(user_row.role, "player"),
        ai_tier=_enum_value(user_row.ai_tier, "bronze"),
        home_nation_name=user_row.home_nation_name,
        home_nation_flag=user_row.home_nation_flag,
        created_at=_iso(user_row.created_at),
        is_ai=bool(user_row.is_ai),
        total_transactions=len(transactions),
        last_activity_at=None,
        xp_total=int(getattr(xp_row, "total_xp", 0) or 0),
        xp_level=getattr(xp_row, "level", "beginner") or "beginner",
        active_nation_role=_enum_value(
            getattr(membership_row, "role", None),
            "",
        ) or None,
        last_10_transactions=transactions,
        mission_completed_count=int(mission_count or 0),
        ai_usage_today={
            "advisor": int(getattr(usage_row, "advisor_questions_used", 0) or 0),
            "portfolio": int(getattr(usage_row, "portfolio_scans_used", 0) or 0),
            "war": int(getattr(usage_row, "war_analysis_used", 0) or 0),
        },
        is_banned=user_row.deleted_at is not None,
    )


# ── END OF players.py ──
