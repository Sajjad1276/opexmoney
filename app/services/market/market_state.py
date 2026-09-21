from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, RateHistory, UserActivity


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


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


async def _read_pressure(redis, nation_id: int) -> tuple[Decimal, Decimal, Decimal]:
    if redis is None:
        return Decimal("0"), Decimal("0"), Decimal("0")

    try:
        pressure = await redis.hgetall(f"pressure:{int(nation_id)}")
    except Exception:
        return Decimal("0"), Decimal("0"), Decimal("0")

    if not isinstance(pressure, dict):
        return Decimal("0"), Decimal("0"), Decimal("0")

    return (
        _to_decimal(pressure.get("buy_volume")),
        _to_decimal(pressure.get("sell_volume")),
        _to_decimal(pressure.get("foreign_buy")),
    )


async def _get_redis():
    from app.core.redis import get_redis
    return get_redis()


async def _calculate_volatility(
    session: AsyncSession,
    nation_id: int,
) -> Decimal:
    rows = list(reversed((
        await session.execute(
            select(RateHistory.rate)
            .where(RateHistory.nation_id == nation_id)
            .order_by(RateHistory.calculated_at.desc())
            .limit(4)
        )
    ).scalars().all()))

    rates = [_to_decimal(rate) for rate in rows if _to_decimal(rate) > 0]
    if len(rates) < 2:
        return Decimal("0")

    changes = [
        (rates[index] - rates[index - 1]) / rates[index - 1]
        for index in range(1, len(rates))
        if rates[index - 1] > 0
    ]
    if not changes:
        return Decimal("0")

    mean = sum(changes, Decimal("0")) / Decimal(len(changes))
    variance = sum(
        (change - mean) ** 2
        for change in changes
    ) / Decimal(len(changes))
    try:
        volatility = variance.sqrt()
    except Exception:
        volatility = Decimal("0")
    return _clamp(abs(volatility), Decimal("0"), Decimal("1"))


async def get_currency_state(
    session: AsyncSession,
    redis=None,
    nation_id: int | None = None,
    window_minutes: int = 15,
) -> CurrencyMarketState:
    # Backward-compatible support for the old get_currency_state(session, nation_id) call.
    if nation_id is None and isinstance(redis, int):
        nation_id = redis
        redis = None

    if nation_id is None:
        raise ValueError("شناسه ملت مشخص نشده است.")

    nation = await session.get(Nation, nation_id)
    if nation is None:
        raise ValueError("ملت پیدا نشد.")

    if redis is None:
        redis = await _get_redis()

    buy_pressure, sell_pressure, foreign_demand = await _read_pressure(
        redis,
        nation_id,
    )

    liquidity = _to_decimal(await session.scalar(
        select(
            func.coalesce(
                func.sum(CurrencyHolding.amount),
                0,
            )
        ).where(
            CurrencyHolding.nation_id == nation_id,
        )
    ))

    from app.services.economic_engine import get_active_members

    national_activity = await get_active_members(
        session,
        nation_id,
        hours=max(1, int(window_minutes)),
    )

    liquidity_score = min(
        liquidity / Decimal("100000"),
        Decimal("1"),
    )
    activity_score = Decimal(national_activity) / Decimal("100")
    confidence = _clamp(
        (liquidity_score * Decimal("0.5"))
        + (activity_score * Decimal("0.5")),
        Decimal("0"),
        Decimal("1"),
    )

    volatility = await _calculate_volatility(session, nation_id)

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
