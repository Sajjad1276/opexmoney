from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity


async def get_player_net_worth(
    session: AsyncSession,
    player_id: int,
) -> Decimal:
    values = await get_player_net_worths(session, [player_id])
    return values.get(player_id, Decimal("0"))


async def get_player_net_worths(
    session: AsyncSession,
    player_ids: Iterable[int],
) -> dict[int, Decimal]:
    ids = list(dict.fromkeys(int(item) for item in player_ids))
    if not ids:
        return {}

    users = (
        await session.execute(
            select(User).where(User.user_id.in_(ids))
        )
    ).scalars().all()

    result = {user.user_id: Decimal(str(user.xr_balance or 0)) for user in users}
    home_nation_ids = {user.home_nation_id for user in users if user.home_nation_id is not None}
    home_rates: dict[int, Decimal] = {}

    if home_nation_ids:
        nations = (
            await session.execute(
                select(Nation.nation_id, Nation.exchange_rate)
                .where(Nation.nation_id.in_(home_nation_ids))
            )
        ).all()
        home_rates = {nation_id: Decimal(str(rate)) for nation_id, rate in nations}

    for user in users:
        if user.home_nation_id is not None:
            rate = home_rates.get(user.home_nation_id, Decimal("0"))
            result[user.user_id] += Decimal(str(user.balance or 0)) * rate

    holdings = (
        await session.execute(
            select(
                CurrencyHolding.user_id,
                CurrencyHolding.nation_id,
                CurrencyHolding.amount,
                Nation.exchange_rate,
            )
            .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
            .where(CurrencyHolding.user_id.in_(ids))
        )
    ).all()

    home_lookup = {user.user_id: user.home_nation_id for user in users}
    for user_id, nation_id, amount, rate in holdings:
        if nation_id == home_lookup.get(user_id):
            continue
        result[user_id] = result.get(user_id, Decimal("0")) + (
            Decimal(str(amount)) * Decimal(str(rate))
        )

    for player_id in ids:
        result.setdefault(player_id, Decimal("0"))
    return result


async def get_active_player_ids(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[int]:
    result = await session.execute(
        select(UserActivity.user_id)
        .where(UserActivity.created_at >= since)
        .distinct()
    )
    return [int(value) for value in result.scalars().all()]


def median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def gini_coefficient(values: list[Decimal]) -> Decimal:
    positive = sorted(max(Decimal("0"), value) for value in values)
    if not positive:
        return Decimal("0")
    total = sum(positive, Decimal("0"))
    if total == 0:
        return Decimal("0")

    count = Decimal(len(positive))
    weighted = sum(
        Decimal(index) * value
        for index, value in enumerate(positive, start=1)
    )
    return (
        (Decimal("2") * weighted) / (count * total)
        - (count + Decimal("1")) / count
    ).quantize(Decimal("0.00000001"))


def top10_wealth_share(values: list[Decimal]) -> Decimal:
    positive = sorted((max(Decimal("0"), value) for value in values), reverse=True)
    if not positive:
        return Decimal("0")
    total = sum(positive, Decimal("0"))
    if total == 0:
        return Decimal("0")
    count = max(1, (len(positive) + 9) // 10)
    return (
        sum(positive[:count], Decimal("0")) / total
    ).quantize(Decimal("0.00000001"))


async def count_transactions(
    session: AsyncSession,
    *,
    since: datetime,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Transaction.transaction_type, Transaction.id)
            .where(Transaction.created_at >= since)
        )
    ).all()

    counts = {"buy": 0, "sell": 0, "export": 0, "import": 0}
    for transaction_type, _ in rows:
        counts[transaction_type] = counts.get(transaction_type, 0) + 1
    return {
        "buy": counts.get("buy", 0),
        "sell": counts.get("sell", 0),
        "export": counts.get("export", 0),
        "import": counts.get("import", 0),
    }


async def calculate_total_volume(
    session: AsyncSession,
    *,
    since: datetime,
) -> Decimal:
    rows = (
        await session.execute(
            select(
                Transaction.transaction_type,
                Transaction.spend_xr,
                Transaction.amount,
                Transaction.rate,
            )
            .where(Transaction.created_at >= since)
        )
    ).all()

    total = Decimal("0")
    for transaction_type, spend_xr, amount, rate in rows:
        if transaction_type == "buy":
            total += Decimal(str(spend_xr))
        elif transaction_type == "sell":
            total += Decimal(str(amount)) * Decimal(str(rate))
        else:
            total += Decimal(str(spend_xr or 0))
    return total
