from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_admin_user
from admin.cache import TTL_TRANSACTIONS, cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import TransactionItem, TransactionsPage
from app.database.models import Nation, Transaction, User
from redis.asyncio import Redis

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _parse_datetime(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("", response_model=TransactionsPage)
@cached(TTL_TRANSACTIONS, "transactions")
async def list_transactions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    nation_id: int | None = Query(default=None),
    user_id: int | None = Query(default=None),
    type: str | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> TransactionsPage:
    parsed_from = _parse_datetime(from_date, "from_date")
    parsed_to = _parse_datetime(to_date, "to_date")

    if parsed_from is None and parsed_to is None:
        parsed_from = datetime.now(timezone.utc) - timedelta(hours=24)

    conditions = []
    if nation_id is not None:
        conditions.append(Transaction.nation_id == nation_id)
    if user_id is not None:
        conditions.append(Transaction.user_id == user_id)
    if type:
        conditions.append(Transaction.transaction_type == type)
    if parsed_from is not None:
        conditions.append(Transaction.created_at >= parsed_from)
    if parsed_to is not None:
        conditions.append(Transaction.created_at <= parsed_to)

    total = int(await db.scalar(select(func.count(Transaction.id)).where(*conditions)) or 0)

    rows = (
        await db.execute(
            select(Transaction, User.username, Nation.name)
            .outerjoin(User, User.user_id == Transaction.user_id)
            .outerjoin(Nation, Nation.nation_id == Transaction.nation_id)
            .where(*conditions)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()

    items = [
        TransactionItem(
            id=tx.id,
            user_id=tx.user_id,
            username=username,
            nation_id=tx.nation_id,
            nation_name=nation_name,
            transaction_type=tx.transaction_type,
            spend_xr=tx.spend_xr,
            amount=tx.amount,
            fee_xr=tx.fee_xr,
            rate=tx.rate,
            created_at=tx.created_at,
        )
        for tx, username, nation_name in rows
    ]
    return TransactionsPage(
        items=items,
        total=total,
        page=page,
        pages=page_count(total, limit),
    )
