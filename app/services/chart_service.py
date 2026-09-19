from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, RateHistory


WINDOWS: dict[str, dict[str, Any]] = {
    "24h": {"hours": 24, "label": "۲۴ ساعت"},
    "72h": {"hours": 72, "label": "۷۲ ساعت"},
    "7d": {"hours": 168, "label": "۷ روز"},
}


def _empty_chart_data(
    nation: Nation,
    *,
    window_hours: int,
) -> dict:
    current_rate = Decimal(str(nation.exchange_rate))
    return {
        "rates": [],
        "timestamps": [],
        "max_rate": current_rate,
        "min_rate": current_rate,
        "current_rate": current_rate,
        "change_pct": Decimal("0"),
        "rate_emoji": "➡️",
        "currency_code": str(nation.currency_code),
        "nation_name": str(nation.name),
        "window_hours": window_hours,
        "enough_data": False,
    }


async def get_chart_data(
    session: AsyncSession,
    nation_id: int,
    window: str,
) -> dict:
    if window not in WINDOWS:
        raise ValueError("invalid_window")

    nation = await session.get(Nation, nation_id)
    if nation is None:
        raise ValueError("nation_not_found")

    window_hours = int(WINDOWS[window]["hours"])
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    rows = (
        await session.execute(
            select(
                RateHistory.rate,
                RateHistory.calculated_at,
                Nation.currency_code,
                Nation.name,
            )
            .join(Nation, Nation.nation_id == RateHistory.nation_id)
            .where(
                RateHistory.nation_id == nation_id,
                RateHistory.calculated_at >= cutoff,
            )
            .order_by(RateHistory.calculated_at.asc())
        )
    ).all()

    if len(rows) < 3:
        return _empty_chart_data(
            nation,
            window_hours=window_hours,
        )

    rates = [Decimal(str(row.rate)) for row in rows]
    timestamps = [row.calculated_at for row in rows]
    first_rate = rates[0]
    last_rate = rates[-1]

    if first_rate == 0:
        change_pct = Decimal("0")
    else:
        change_pct = (last_rate - first_rate) / first_rate * Decimal("100")

    if change_pct > 0:
        rate_emoji = "📈"
    elif change_pct < 0:
        rate_emoji = "📉"
    else:
        rate_emoji = "➡️"

    return {
        "rates": rates,
        "timestamps": timestamps,
        "max_rate": max(rates),
        "min_rate": min(rates),
        "current_rate": Decimal(str(nation.exchange_rate)),
        "change_pct": change_pct,
        "rate_emoji": rate_emoji,
        "currency_code": str(nation.currency_code),
        "nation_name": str(nation.name),
        "window_hours": window_hours,
        "enough_data": True,
    }
