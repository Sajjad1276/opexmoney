from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, RateHistory


WINDOWS: dict[str, dict[str, Any]] = {
    "1h": {"hours": 1, "label": "۱ ساعت"},
    "6h": {"hours": 6, "label": "۶ ساعت"},
    "24h": {"hours": 24, "label": "۲۴ ساعت"},
    "7d": {"hours": 168, "label": "۷ روز"},
    # Legacy callback support for messages already sent before this redesign.
    "72h": {"hours": 72, "label": "۷۲ ساعت"},
}

VISIBLE_WINDOWS = ("1h", "6h", "24h", "7d")
MIN_HISTORY_POINTS = 3


def _empty_chart_data(
    nation: Nation,
    *,
    window_hours: int,
    change_7d: Decimal | None = None,
    market_status: str | None = None,
) -> dict:
    current_rate = Decimal(str(nation.exchange_rate))
    return {
        "rates": [],
        "timestamps": [],
        "max_rate": current_rate,
        "min_rate": current_rate,
        "current_rate": current_rate,
        "change_pct": Decimal("0"),
        "change_7d": change_7d,
        "rate_emoji": "➡️",
        "currency_code": str(nation.currency_code),
        "nation_name": str(nation.name),
        "nation_flag": "🌍",
        "window_hours": window_hours,
        "enough_data": False,
        "market_status": market_status,
        "three_day_downtrend": False,
        "volatility": Decimal("0"),
    }


async def _get_market_status(
    session: AsyncSession,
    nation_id: int,
) -> str | None:
    nations = (
        await session.execute(
            select(
                Nation.nation_id,
                Nation.exchange_rate,
                Nation.rate_24h_open,
            )
            .where(Nation.is_active.is_(True))
        )
    ).all()

    changes: list[tuple[int, Decimal]] = []
    for row in nations:
        current = Decimal(str(row.exchange_rate or "0"))
        opening = Decimal(str(row.rate_24h_open or "0"))
        if opening <= 0:
            continue
        changes.append(
            (
                int(row.nation_id),
                (current - opening) / opening * Decimal("100"),
            )
        )

    if len(changes) < 2:
        return None

    best_id = max(changes, key=lambda item: item[1])[0]
    worst_id = min(changes, key=lambda item: item[1])[0]

    if nation_id == best_id:
        return "best"
    if nation_id == worst_id:
        return "worst"
    return None


def _calculate_change(
    rates: list[Decimal],
    current_rate: Decimal,
) -> Decimal:
    if not rates:
        return Decimal("0")

    first = rates[0]
    if first == 0:
        return Decimal("0")

    return (current_rate - first) / first * Decimal("100")


def _calculate_7d_change(
    rows: list[Any],
    current_rate: Decimal,
) -> Decimal | None:
    if not rows:
        return None

    first_rate = Decimal(str(rows[0].rate))
    if first_rate == 0:
        return None

    return (
        (current_rate - first_rate)
        / first_rate
        * Decimal("100")
    )


def _has_three_day_downtrend(rows: list[Any]) -> bool:
    daily_last: dict[object, Decimal] = {}

    for row in rows:
        daily_last[row.calculated_at.date()] = Decimal(str(row.rate))

    if len(daily_last) < 3:
        return False

    ordered_days = sorted(daily_last)
    values = [
        daily_last[ordered_days[-3]],
        daily_last[ordered_days[-2]],
        daily_last[ordered_days[-1]],
    ]
    return values[0] > values[1] > values[2]


def _calculate_volatility(rates: list[Decimal]) -> Decimal:
    if not rates:
        return Decimal("0")

    values = [float(rate) for rate in rates]
    average = sum(values) / len(values)

    if average == 0:
        return Decimal("0")

    variance = sum(
        (value - average) ** 2
        for value in values
    ) / len(values)

    return Decimal(str((variance ** 0.5) / abs(average)))


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

    now = datetime.utcnow()
    requested_hours = int(WINDOWS[window]["hours"])
    requested_cutoff = now - timedelta(hours=requested_hours)
    history_cutoff = now - timedelta(days=7)

    rows = (
        await session.execute(
            select(RateHistory.rate, RateHistory.calculated_at)
            .where(
                RateHistory.nation_id == nation_id,
                RateHistory.calculated_at >= history_cutoff,
            )
            .order_by(RateHistory.calculated_at.asc())
        )
    ).all()

    history_rows = [
        row
        for row in rows
        if row.calculated_at >= history_cutoff
    ]

    selected_rows = [
        row
        for row in history_rows
        if row.calculated_at >= requested_cutoff
    ]

    history_rates = [
        Decimal(str(row.rate))
        for row in selected_rows
    ]
    history_timestamps = [
        row.calculated_at
        for row in selected_rows
    ]

    current_rate = Decimal(str(nation.exchange_rate))
    change_7d = _calculate_7d_change(
        history_rows,
        current_rate,
    )
    market_status = await _get_market_status(
        session,
        nation_id,
    )

    if len(history_rates) < MIN_HISTORY_POINTS:
        return _empty_chart_data(
            nation,
            window_hours=requested_hours,
            change_7d=change_7d,
            market_status=market_status,
        )

    change_pct = _calculate_change(
        history_rates,
        current_rate,
    )

    if change_pct > 0:
        rate_emoji = "📈"
    elif change_pct < 0:
        rate_emoji = "📉"
    else:
        rate_emoji = "➡️"

    return {
        "rates": history_rates,
        "timestamps": history_timestamps,
        "max_rate": max([*history_rates, current_rate]),
        "min_rate": min([*history_rates, current_rate]),
        "current_rate": current_rate,
        "change_pct": change_pct,
        "change_7d": change_7d,
        "rate_emoji": rate_emoji,
        "currency_code": str(nation.currency_code),
        "nation_name": str(nation.name),
        "nation_flag": "🌍",
        "window_hours": requested_hours,
        "window": window,
        "enough_data": True,
        "market_status": market_status,
        "three_day_downtrend": _has_three_day_downtrend(history_rows),
        "volatility": _calculate_volatility(
            [*history_rates, current_rate]
        ),
    }
