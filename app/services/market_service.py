from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation


@dataclass(frozen=True)
class MarketHolding:
    currency_code: str
    nation_name: str
    amount: Decimal
    is_active: bool


async def get_user_sell_holdings(
    session: AsyncSession,
    user_id: int,
) -> tuple[list[MarketHolding], list[MarketHolding]]:
    """Return sellable holdings and inactive-nation holdings separately."""
    rows = (
        await session.execute(
            select(CurrencyHolding, Nation)
            .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
            .where(
                CurrencyHolding.user_id == user_id,
                CurrencyHolding.amount > 0,
            )
            .order_by(CurrencyHolding.amount.desc(), Nation.nation_id.asc())
        )
    ).all()

    active: list[MarketHolding] = []
    inactive: list[MarketHolding] = []
    for holding, nation in rows:
        item = MarketHolding(
            currency_code=nation.currency_code,
            nation_name=nation.name,
            amount=holding.amount,
            is_active=bool(nation.is_active),
        )
        (active if nation.is_active else inactive).append(item)

    return active, inactive
