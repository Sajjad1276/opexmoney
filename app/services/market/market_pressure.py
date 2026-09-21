from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation
from app.services.market.market_state import CurrencyMarketState, get_currency_state


@dataclass(frozen=True)
class PriceImpact:
    base_rate: Decimal
    adjusted_rate: Decimal
    impact_pct: Decimal
    cause: str
    market_state: CurrencyMarketState


def pressure_signal_from_state(state: CurrencyMarketState) -> Decimal:
    total = state.buy_pressure + state.sell_pressure
    if total <= 0:
        return Decimal("0")
    imbalance = (state.buy_pressure - state.sell_pressure) / total
    demand_scale = min(Decimal("1"), state.foreign_demand / Decimal("1000"))
    return max(Decimal("-1"), min(Decimal("1"), imbalance + demand_scale * Decimal("0.10")))


def _impact_for_ratio(ratio: Decimal) -> Decimal:
    if ratio <= 0:
        return Decimal("0")
    if ratio < Decimal("0.01"):
        return (ratio * Decimal("9")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    span = (ratio - Decimal("0.01")) / Decimal("0.09")
    impact = Decimal("0.09") + span * Decimal("1.91")
    return min(Decimal("2"), impact).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def calculate_price_impact(
    session: AsyncSession,
    nation_id: int,
    action: str,
    amount: Decimal,
) -> PriceImpact:
    if action not in {"buy", "sell"}:
        raise ValueError("عملیات معامله باید buy یا sell باشد.")
    if amount <= 0:
        raise ValueError("مقدار معامله باید بیشتر از صفر باشد.")

    nation = await session.get(Nation, nation_id)
    if nation is None or not nation.is_active:
        raise ValueError("ملت فعال پیدا نشد.")

    state = await get_currency_state(session, nation_id)
    base_rate = Decimal(str(nation.exchange_rate))
    liquidity = state.liquidity
    trade_units = amount / base_rate if action == "buy" and base_rate > 0 else amount
    ratio = trade_units / liquidity if liquidity > 0 else Decimal("1")
    impact_pct = _impact_for_ratio(ratio)
    direction = Decimal("1") if action == "buy" else Decimal("-1")
    adjusted_rate = (base_rate * (Decimal("1") + direction * impact_pct / Decimal("100"))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )

    if action == "buy":
        cause = f"فشار خرید؛ اثر تخمینی این معامله {impact_pct}٪ است."
    else:
        cause = f"فشار فروش؛ اثر تخمینی این معامله {impact_pct}٪ است."

    return PriceImpact(
        base_rate=base_rate,
        adjusted_rate=adjusted_rate,
        impact_pct=impact_pct,
        cause=cause,
        market_state=state,
    )
