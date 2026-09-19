from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationRank,
    Transaction,
    User,
    UserActivity,
)
from app.services.user_service import sync_user_balance
from app.utils.formatting import gregorian_to_jalali, to_fa


def _created_at_jalali(created_at) -> str:
    y, m, d = gregorian_to_jalali(
        created_at.year,
        created_at.month,
        created_at.day,
    )
    return to_fa(f"{y:04d}/{m:02d}/{d:02d}")


async def get_settings_data(session: AsyncSession, user_id: int) -> dict:
    result = await session.execute(
        select(
            User.username,
            User.home_nation_id,
            User.created_at,
            Nation.name,
        )
        .join(
            Nation,
            Nation.nation_id == User.home_nation_id,
            isouter=True,
        )
        .where(User.user_id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        raise ValueError(f"User {user_id} not found")

    return {
        "username": row.username,
        "nation_name": row.name,
        "created_at_jalali": _created_at_jalali(row.created_at),
        "user_id": user_id,
    }


async def get_user_stats(session: AsyncSession, user_id: int) -> dict:
    result = await session.execute(
        select(
            func.count(Transaction.id).label("tx_count"),
            func.coalesce(
                func.sum(
                    case(
                        (Transaction.transaction_type == "buy", Transaction.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("buy_volume"),
            func.coalesce(
                func.sum(
                    case(
                        (Transaction.transaction_type == "sell", Transaction.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("sell_volume"),
            func.count(Transaction.id)
            .filter(Transaction.created_at >= func.current_date())
            .label("today_count"),
        )
        .where(Transaction.user_id == user_id)
    )
    tx_row = result.one()

    active_days = await session.scalar(
        select(
            func.count(
                func.distinct(func.date(UserActivity.created_at))
            )
        ).where(UserActivity.user_id == user_id)
    )

    return {
        "tx_count": int(tx_row.tx_count or 0),
        "buy_volume": Decimal(str(tx_row.buy_volume or Decimal("0"))),
        "sell_volume": Decimal(str(tx_row.sell_volume or Decimal("0"))),
        "today_count": int(tx_row.today_count or 0),
        # NationRank stores the current rank per nation, not per-user rank history.
        "best_rank": None,
        "active_days": int(active_days or 0),
    }


async def is_username_taken(
    session: AsyncSession,
    username: str,
    exclude_user_id: int,
) -> bool:
    count = await session.scalar(
        select(func.count(User.user_id)).where(
            User.username == username,
            User.user_id != exclude_user_id,
        )
    )
    return bool(count)


async def change_username(
    session: AsyncSession,
    user_id: int,
    new_username: str,
) -> None:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    user.username = new_username


async def change_home_nation(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
) -> None:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    nation = await session.scalar(
        select(Nation).where(
            Nation.nation_id == nation_id,
            Nation.is_active.is_(True),
        )
    )
    if nation is None:
        raise ValueError("Active nation not found")

    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user_id,
            CurrencyHolding.nation_id == nation_id,
        )
        .with_for_update()
    )
    if holding is None:
        session.add(
            CurrencyHolding(
                user_id=user_id,
                nation_id=nation_id,
                amount=Decimal("0"),
            )
        )
        await session.flush()

    user.home_nation_id = nation_id
    await sync_user_balance(session, user_id)

    # ActivityType has no dedicated nation-change value; LOGIN is the closest
    # existing activity category and keeps the event auditable without a new enum/migration.
    session.add(
        UserActivity(
            user_id=user_id,
            nation_id=nation_id,
            activity_type=ActivityType.LOGIN,
        )
    )


async def get_active_nations(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        select(
            Nation.nation_id,
            Nation.name,
            Nation.currency_code,
        )
        .where(Nation.is_active.is_(True))
        .order_by(Nation.name.asc())
    )
    return [
        {
            "id": row.nation_id,
            "name": row.name,
            "currency_code": row.currency_code,
        }
        for row in result.all()
    ]
