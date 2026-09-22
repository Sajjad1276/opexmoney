from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import PaginatedResponse, TransactionItem
from app.database.models import Nation, Transaction
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


async def _run_all(statement: Any) -> list[Any]:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.all()


async def _run_scalar(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.scalar_one_or_none()


def _parse_date(value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format, use YYYY-MM-DD",
        ) from exc


@router.get("", response_model=PaginatedResponse[TransactionItem])
@cached(15, "transactions")
async def list_transactions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    nation_id: int | None = Query(default=None),
    user_id: int | None = Query(default=None),
    type: str | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PaginatedResponse[TransactionItem]:
    parsed_from = _parse_date(from_date)
    parsed_to = _parse_date(to_date)

    now = datetime.now(timezone.utc)
    if parsed_from is None and parsed_to is None:
        start_dt = now - timedelta(hours=24)
        end_dt = None
    else:
        start_dt = (
            datetime.combine(parsed_from, time.min, tzinfo=timezone.utc)
            if parsed_from is not None
            else None
        )
        end_dt = (
            datetime.combine(parsed_to, time.max, tzinfo=timezone.utc)
            if parsed_to is not None
            else None
        )

    conditions: list[Any] = []
    if nation_id is not None:
        conditions.append(Transaction.nation_id == nation_id)
    if user_id is not None:
        conditions.append(Transaction.user_id == user_id)
    if type is not None:
        conditions.append(Transaction.transaction_type == type)
    if start_dt is not None:
        conditions.append(Transaction.created_at >= start_dt)
    if end_dt is not None:
        conditions.append(Transaction.created_at <= end_dt)

    count_stmt = select(func.count(Transaction.id)).where(*conditions)
    data_stmt = (
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
        .where(*conditions)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    total, rows = await __import__("asyncio").gather(
        _run_scalar(count_stmt),
        _run_all(data_stmt),
    )

    total_int = int(total or 0)
    items = [
        TransactionItem(
            id=int(row.id),
            transaction_type=row.transaction_type,
            spend_xr=float(row.spend_xr or 0),
            amount=float(row.amount or 0),
            fee_xr=float(row.fee_xr or 0),
            rate=float(row.rate or 0),
            nation_name=row.name or "",
            nation_flag=row.flag_emoji,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total_int,
        page=page,
        pages=page_count(total_int, limit),
    )


# ── END OF transactions.py ──
