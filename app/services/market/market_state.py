from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, NationTelegramMember, Transaction, User, UserActivity


@dataclass(frozen=True)
class CurrencyMarketState:
    currency_code: str
    buy_pressure: Decimal
    sell_pressure: Decimal
    liquidity: Decimal
    confidence: Decimal
    volatility: Decimal
    foreign_demand: Decimal
    national_activity: int


def _safe_ratio(value: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return value / denominator


async def get_currency_state(
    session: AsyncSession,
    nation_id: int,
    window_minutes: int = 15,
) -> CurrencyMarketState:
    nation = await session.get(Nation, nation_id)
    if nation is None:
        raise ValueError("ملت پیدا نشد.")

    since = datetime.utcnow() - timedelta(minutes=max(1, window_minutes))

    buy_pressure = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(Transaction.spend_xr), 0)).where(
            Transaction.nation_id == nation_id,
            Transaction.transaction_type == "buy",
            Transaction.created_at >= since,
        )
    ) or 0))
    sell_pressure = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(Transaction.spend_xr), 0)).where(
            Transaction.nation_id == nation_id,
            Transaction.transaction_type == "sell",
            Transaction.created_at >= since,
        )
    ) or 0))
    foreign_demand = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(Transaction.spend_xr), 0))
        .join(User, User.user_id == Transaction.user_id)
        .where(
            Transaction.nation_id == nation_id,
            Transaction.transaction_type == "buy",
            Transaction.created_at >= since,
            User.home_nation_id != nation_id,
        )
    ) or 0))

    liquidity = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(CurrencyHolding.amount), 0)).where(
            CurrencyHolding.nation_id == nation_id,
            CurrencyHolding.amount > 0,
        )
    ) or 0))

    activity_stmt = select(func.count(distinct(UserActivity.user_id))).where(
        UserActivity.nation_id == nation_id,
        UserActivity.created_at >= since,
    )
    if not nation.is_ai:
        activity_stmt = activity_stmt.join(
            NationTelegramMember,
            NationTelegramMember.telegram_user_id == UserActivity.user_id,
        ).where(
            NationTelegramMember.nation_id == nation_id,
            NationTelegramMember.is_active.is_(True),
        )
    national_activity = int(await session.scalar(activity_stmt) or 0)

    total_pressure = buy_pressure + sell_pressure
    confidence = min(
        Decimal("1"),
        _safe_ratio(total_pressure, Decimal("1000"))
        + _safe_ratio(Decimal(national_activity), Decimal("100")),
    )
    rates = list((
        await session.execute(
            select(Transaction.rate)
            .where(
                Transaction.nation_id == nation_id,
                Transaction.created_at >= since,
            )
            .order_by(Transaction.created_at.asc())
        )
    ).scalars().all())
    changes = [
        (
            Decimal(str(rates[index])) - Decimal(str(rates[index - 1]))
        ) / Decimal(str(rates[index - 1]))
        for index in range(1, len(rates))
        if Decimal(str(rates[index - 1])) > 0
    ]
    if changes:
        mean = sum(changes, Decimal("0")) / Decimal(len(changes))
        variance = sum(
            (change - mean) ** 2
            for change in changes
        ) / Decimal(len(changes))
        volatility = variance.sqrt()
    else:
        volatility = Decimal("0")
    volatility = min(Decimal("1"), abs(volatility))

    return CurrencyMarketState(
        currency_code=nation.currency_code,
        buy_pressure=buy_pressure,
        sell_pressure=sell_pressure,
        liquidity=liquidity,
        confidence=confidence,
        volatility=volatility,
        foreign_demand=foreign_demand,
        national_activity=national_activity,
    )
