"""Build a compact, DB-backed context for the OPEX MONEY AI companion."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, NationRank, Transaction, User, UserActivity
from ai.cache import get_last_bot_message


def _decimal(value: Decimal | int | float | None) -> str:
    """Convert numeric values to a stable string representation."""
    if value is None:
        return "0"
    return format(Decimal(str(value)), "f")


def _iso(value: datetime | date | None) -> str | None:
    """Serialize a date/time value safely for the AI prompt."""
    return value.isoformat() if value is not None else None


async def _load_last_transactions(user_id: int, db: AsyncSession) -> list[dict[str, Any]]:
    """Return the user's most recent five transactions."""
    rows = (
        await db.execute(
            select(
                Transaction.transaction_type,
                Transaction.spend_xr,
                Transaction.amount,
                Transaction.fee_xr,
                Transaction.rate,
                Transaction.created_at,
                Nation.name,
                Nation.currency_code,
            )
            .join(Nation, Nation.nation_id == Transaction.nation_id)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(5)
        )
    ).all()

    return [
        {
            "type": str(transaction_type),
            "nation": str(nation_name),
            "currency": str(currency_code),
            "spend_xr": _decimal(spend_xr),
            "amount": _decimal(amount),
            "fee_xr": _decimal(fee_xr),
            "rate": _decimal(rate),
            "created_at": _iso(created_at),
        }
        for (
            transaction_type,
            spend_xr,
            amount,
            fee_xr,
            rate,
            created_at,
            nation_name,
            currency_code,
        ) in rows
    ]


async def _load_nation_status(nation_id: int | None, db: AsyncSession) -> dict[str, Any] | None:
    """Return the current status of the player's home nation."""
    if nation_id is None:
        return None

    nation = await db.get(Nation, nation_id)
    if nation is None:
        return None

    rank_row = await db.scalar(
        select(NationRank.rank).where(NationRank.nation_id == nation_id)
    )

    rank = rank_row if rank_row is not None else nation.nation_rank

    return {
        "id": int(nation.nation_id),
        "name": nation.name,
        "currency": nation.currency_code,
        "rank": int(rank) if rank is not None else None,
        "exchange_rate": _decimal(nation.exchange_rate),
        "members": int(nation.member_count),
        "active": bool(nation.is_active),
    }


async def _load_active_days(user_id: int, db: AsyncSession) -> int:
    """Count calendar days on which the player has recorded activity."""
    value = await db.scalar(
        select(func.count(func.distinct(func.date(UserActivity.created_at))))
        .where(UserActivity.user_id == user_id)
    )
    return int(value or 0)


async def _load_relations(user_id: int, db: AsyncSession) -> dict[str, Any]:
    """Return social relations only when they exist in the current schema."""
    del user_id, db
    return {
        "enemies": [],
        "allies": [],
        "available": False,
        "source": "not_available_in_current_schema",
    }


async def build_user_context(user_id: int, db: AsyncSession) -> dict[str, Any]:
    """
    Build the AI context for one player.

    The context contains user data, the latest five transactions, home-nation
    economy status, active-day count, social relation placeholders, and the
    latest bot message tracked by Redis.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} does not exist")

    relations = await _load_relations(user_id, db)
    return {
        "user": {
            "id": int(user.user_id),
            "name": user.username,
            "role": user.role,
            "balance_xr": _decimal(user.balance),
            "xr_balance": _decimal(user.xr_balance),
        },
        "home_nation": await _load_nation_status(user.home_nation_id, db),
        "transactions": await _load_last_transactions(user_id, db),
        "relations": relations,
        "enemies": relations["enemies"],
        "allies": relations["allies"],
        "active_days": await _load_active_days(user_id, db),
        "last_bot_message": await get_last_bot_message(user_id),
    }


__all__ = ["build_user_context"]