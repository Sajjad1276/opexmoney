from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, Transaction, User
from app.database.session import async_session
from app.services.alert_service import create_price_alert, delete_price_alert, list_price_alerts
from app.services.chart_service import get_chart_data
from app.services.market_intelligence import get_market_overview
from app.services.nation_service import get_user_active_nation_context


@dataclass(frozen=True)
class MarketHolding:
    currency_code: str
    nation_name: str
    amount: Decimal
    is_active: bool


@dataclass(frozen=True)
class ListedCurrencyPage:
    currency_codes: tuple[str, ...]
    page: int
    total_pages: int
    total_count: int


@dataclass(frozen=True)
class MarketPageData:
    user: User | None
    overview: dict
    trade_count: int
    active_members: int


LISTED_CURRENCIES_PAGE_SIZE = 100


async def get_listed_currencies_page(session: AsyncSession, page: int = 0) -> ListedCurrencyPage:
    total_count = int(await session.scalar(
        select(func.count(Nation.nation_id)).where(Nation.is_active.is_(True))
    ) or 0)
    total_pages = max(1, ceil(total_count / LISTED_CURRENCIES_PAGE_SIZE))
    safe_page = max(0, min(int(page), total_pages - 1))
    rows = (await session.execute(
        select(Nation.currency_code)
        .where(Nation.is_active.is_(True))
        .order_by(Nation.exchange_rate.asc(), Nation.nation_id.asc())
        .offset(safe_page * LISTED_CURRENCIES_PAGE_SIZE)
        .limit(LISTED_CURRENCIES_PAGE_SIZE)
    )).scalars().all()
    return ListedCurrencyPage(tuple(rows), safe_page, total_pages, total_count)


async def get_user_sell_holdings(session: AsyncSession, user_id: int) -> tuple[list[MarketHolding], list[MarketHolding]]:
    rows = (await session.execute(
        select(CurrencyHolding, Nation)
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(CurrencyHolding.user_id == user_id, CurrencyHolding.amount > 0)
        .order_by(CurrencyHolding.amount.desc(), Nation.nation_id.asc())
    )).all()
    active: list[MarketHolding] = []
    inactive: list[MarketHolding] = []
    for holding, nation in rows:
        item = MarketHolding(nation.currency_code, nation.name, holding.amount, bool(nation.is_active))
        (active if nation.is_active else inactive).append(item)
    return active, inactive


async def get_market_page_data(user_id: int) -> MarketPageData:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            if user is None:
                return MarketPageData(user, {"currencies": []}, 0, 0)

            context = await get_user_active_nation_context(
                session,
                user_id,
                repair=False,
                lock=False,
            )
            nation_id = context[0].nation_id if context is not None else None
            if nation_id is None:
                return MarketPageData(user, {"currencies": []}, 0, 0)

            overview = await get_market_overview(session, nation_id, limit=20)
            trade_count = int(await session.scalar(
                select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
            ) or 0)
            from app.services.economic_engine import get_active_members
            active = await get_active_members(session, nation_id)
            return MarketPageData(user, overview, trade_count, active)


async def get_active_currency(raw_code: str) -> Nation | None:
    code = raw_code.strip().upper()
    if not code or len(code) > 16:
        return None
    async with async_session() as session:
        async with session.begin():
            return await session.scalar(select(Nation).where(
                Nation.currency_code == code,
                Nation.is_active.is_(True),
            ).limit(1))


async def get_buy_options(user_id: int, limit: int = 3) -> tuple[User | None, list[Nation]]:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            nations = (await session.execute(
                select(Nation).where(Nation.is_active.is_(True))
                .order_by(Nation.exchange_rate.desc()).limit(limit)
            )).scalars().all()
            return user, list(nations)


async def get_buy_currency_data(user_id: int, nation_id: int) -> tuple[Nation | None, User | None]:
    async with async_session() as session:
        async with session.begin():
            return await session.get(Nation, nation_id), await session.get(User, user_id)


async def get_user_xr_balance(user_id: int) -> Decimal | None:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            return None if user is None else Decimal(str(user.xr_balance or 0))


async def get_sell_currency_data(user_id: int, currency_code: str) -> tuple[Nation | None, CurrencyHolding | None]:
    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(select(Nation).where(
                Nation.currency_code == currency_code,
                Nation.is_active.is_(True),
            ))
            holding = None
            if nation is not None:
                holding = await session.scalar(select(CurrencyHolding).where(
                    CurrencyHolding.user_id == user_id,
                    CurrencyHolding.nation_id == nation.nation_id,
                ))
            return nation, holding


async def get_holding_amount(user_id: int, currency_code: str) -> tuple[Nation | None, Decimal | None]:
    async with async_session() as session:
        async with session.begin():
            nation = await session.scalar(select(Nation).where(
                Nation.currency_code == currency_code,
                Nation.is_active.is_(True),
            ))
            if nation is None:
                return None, None
            holding = await session.scalar(select(CurrencyHolding).where(
                CurrencyHolding.user_id == user_id,
                CurrencyHolding.nation_id == nation.nation_id,
            ))
            return nation, None if holding is None else Decimal(str(holding.amount))


async def get_sell_market_data(user_id: int) -> tuple[User | None, list[MarketHolding], list[MarketHolding]]:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            active, inactive = await get_user_sell_holdings(session, user_id)
            return user, active, inactive


async def get_market_history(user_id: int, limit: int = 5) -> tuple[User | None, list[tuple[Transaction, str]]]:
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            rows = (await session.execute(
                select(Transaction, Nation.currency_code)
                .join(Nation, Nation.nation_id == Transaction.nation_id)
                .where(Transaction.user_id == user_id)
                .order_by(Transaction.created_at.desc()).limit(limit)
            )).all()
            return user, rows


async def create_user_price_alert(user_id: int, currency_code: str, target_price: Decimal, direction: str | None = None):
    async with async_session() as session:
        async with session.begin():
            return await create_price_alert(session, user_id=user_id, currency_code=currency_code, target_price=target_price, direction=direction)


async def list_user_price_alerts(user_id: int):
    async with async_session() as session:
        async with session.begin():
            return await list_price_alerts(session, user_id)


async def delete_user_price_alert(user_id: int, alert_id: int) -> bool:
    async with async_session() as session:
        async with session.begin():
            return await delete_price_alert(session, user_id=user_id, alert_id=alert_id)


async def get_chart_data_for_nation(nation_id: int, window: str = "24h") -> dict:
    async with async_session() as session:
        async with session.begin():
            data = await get_chart_data(session, nation_id, window)
            data["window"] = window
            return data


async def get_listed_currencies_page_data(page: int = 0) -> ListedCurrencyPage:
    async with async_session() as session:
        async with session.begin():
            return await get_listed_currencies_page(session, page)
