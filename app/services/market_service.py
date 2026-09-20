from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation


@dataclass(frozen=True)
class MarketHolding:
    currency_code: str
    nation_name: str
    amount: Decimal
    is_active: bool


LISTED_CURRENCIES_PAGE_SIZE = 100


@dataclass(frozen=True)
class ListedCurrencyPage:
    currency_codes: tuple[str, ...]
    page: int
    total_pages: int
    total_count: int


async def get_listed_currencies_page(
    session: AsyncSession,
    page: int = 0,
) -> ListedCurrencyPage:
    """Return up to 100 active currency codes ordered by current price.

    The lowest exchange rate is treated as the best acquisition price.
    Nation ID provides a deterministic tie-breaker.
    """
    total_count = int(
        await session.scalar(
            select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))
        )
        or 0
    )
    total_pages = max(1, ceil(total_count / LISTED_CURRENCIES_PAGE_SIZE))
    safe_page = max(0, min(int(page), total_pages - 1))
    offset = safe_page * LISTED_CURRENCIES_PAGE_SIZE

    rows = (
        await session.execute(
            select(Nation.currency_code)
            .where(Nation.is_active.is_(True))
            .order_by(Nation.exchange_rate.asc(), Nation.nation_id.asc())
            .offset(offset)
            .limit(LISTED_CURRENCIES_PAGE_SIZE)
        )
    ).scalars().all()

    return ListedCurrencyPage(
        currency_codes=tuple(rows),
        page=safe_page,
        total_pages=total_pages,
        total_count=total_count,
    )


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
