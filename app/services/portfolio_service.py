from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, RateHistory, User
from app.utils.formatting import imperial_datetime, today_jalali


async def get_portfolio_data(session: AsyncSession, user_id: int) -> dict:
    user = await session.scalar(
        select(User).where(User.user_id == user_id)
    )
    if user is None:
        raise ValueError(f"User {user_id} not found")

    yesterday = datetime.utcnow().date() - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday, time.min)
    today_start = yesterday_start + timedelta(days=1)

    previous_rate = (
        select(RateHistory.rate)
        .where(
            RateHistory.nation_id == Nation.nation_id,
            RateHistory.calculated_at >= yesterday_start,
            RateHistory.calculated_at < today_start,
        )
        .order_by(RateHistory.calculated_at.desc())
        .limit(1)
        .scalar_subquery()
    )

    result = await session.execute(
        select(
            CurrencyHolding.nation_id,
            Nation.name,
            Nation.currency_code,
            CurrencyHolding.amount,
            Nation.exchange_rate,
            previous_rate,
        )
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(
            CurrencyHolding.user_id == user_id,
            CurrencyHolding.amount > 0,
            # ECONOMIC RULE: holdings liquidate to XR on kick/dissolve wherever you touch this logic.
            # Keep active-nation filtering as a safety net against legacy/unrepaired rows.
            Nation.is_active.is_(True),
        )
    )

    xr_balance = Decimal(str(user.xr_balance or Decimal("0")))
    total_xr = xr_balance
    previous_total_xr = xr_balance
    holdings = []

    for (
        nation_id,
        nation_name,
        currency_code,
        amount,
        exchange_rate,
        yesterday_rate,
    ) in result.all():
        amount = Decimal(str(amount))
        exchange_rate = Decimal(str(exchange_rate))
        value_in_xr = amount * exchange_rate

        if yesterday_rate is None or Decimal(str(yesterday_rate)) == 0:
            previous_rate_for_total = exchange_rate
            rate_change_pct = Decimal("0.0")
        else:
            previous_rate_for_total = Decimal(str(yesterday_rate))
            rate_change_pct = (
                (exchange_rate - previous_rate_for_total)
                / previous_rate_for_total
                * Decimal("100")
            )

        if rate_change_pct > 0:
            rate_emoji = "📈"
        elif rate_change_pct < 0:
            rate_emoji = "📉"
        else:
            rate_emoji = "➡️"

        total_xr += value_in_xr
        previous_total_xr += amount * previous_rate_for_total
        holdings.append(
            {
                "nation_id": int(nation_id),
                "nation_name": str(nation_name),
                "currency_code": str(currency_code),
                "amount": amount,
                "exchange_rate": exchange_rate,
                "value_in_xr": value_in_xr,
                "is_home_nation": nation_id == user.home_nation_id,
                "rate_change_pct": rate_change_pct,
                "rate_emoji": rate_emoji,
            }
        )

    holdings.sort(
        key=lambda item: (
            not item["is_home_nation"],
            -item["value_in_xr"],
        )
    )

    portfolio_change_pct = (
        Decimal("0")
        if previous_total_xr == 0
        else (total_xr - previous_total_xr) / previous_total_xr * Decimal("100")
    )
    imperial_date, imperial_time = imperial_datetime()

    return {
        "username": str(user.username),
        "xr_balance": xr_balance,
        "home_nation_id": user.home_nation_id,
        "holdings": holdings,
        "total_xr": total_xr,
        "today_imperial": imperial_date,
        "current_time": imperial_time,
        "portfolio_change_pct": portfolio_change_pct,
        "live_update_seconds": 10,
        # Backward-compatible key for existing consumers/tests.
        "today_jalali": today_jalali(),
    }
