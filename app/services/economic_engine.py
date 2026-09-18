from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BehaviorSnapshot,
    Nation,
    NationMemberHistory,
    NationRank,
    RateHistory,
    User,
    UserActivity,
)
from app.services.economy_metrics import (
    calculate_total_volume,
    count_transactions,
    get_active_player_ids,
    get_player_net_worths,
    gini_coefficient,
    median_decimal,
    top10_wealth_share,
)
from app.services.rules.effects import apply_rate_volatility
from app.services.rules.resolver import resolve
from config import settings


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


async def update_nation_rates(
    session: AsyncSession,
    now: datetime | None = None,
) -> None:
    now = now or datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    nations = (
        await session.execute(
            select(Nation).where(Nation.is_active.is_(True))
        )
    ).scalars().all()

    for nation in nations:
        active = (
            await session.scalar(
                select(func.count(distinct(UserActivity.user_id))).where(
                    UserActivity.nation_id == nation.nation_id,
                    UserActivity.created_at >= since_24h,
                )
            )
            or 0
        )
        total = (
            await session.scalar(
                select(func.count(User.user_id)).where(
                    User.home_nation_id == nation.nation_id
                )
            )
            or 0
        )

        f_activity = (
            Decimal(active) / Decimal(total)
            if total
            else Decimal("0")
        )

        trade_volume = Decimal(str(nation.trade_volume_24h or 0))
        f_trade = min(
            (trade_volume + Decimal("1")).log10() / Decimal("6"),
            Decimal("1"),
        )

        old_members = await session.scalar(
            select(NationMemberHistory.member_count)
            .where(
                NationMemberHistory.nation_id == nation.nation_id,
                NationMemberHistory.recorded_at <= since_7d,
            )
            .order_by(NationMemberHistory.recorded_at.desc())
            .limit(1)
        )
        if old_members in (None, 0):
            f_growth = Decimal("0")
        else:
            f_growth = clamp(
                Decimal(nation.member_count - old_members) / Decimal(old_members),
                Decimal("-0.5"),
                Decimal("0.5"),
            )

        score = (
            f_activity * Decimal("0.4")
            + f_trade * Decimal("0.3")
            + f_growth * Decimal("0.3")
        )
        delta = (score - Decimal("0.5")) * settings.rate_base_step

        volatility_multiplier = Decimal(
            str(
                await resolve(
                    session,
                    "rate.volatility_multiplier",
                    nation_id=nation.nation_id,
                )
            )
        )
        delta = apply_rate_volatility(delta, volatility_multiplier)

        new_rate = clamp(
            nation.exchange_rate * (Decimal("1") + delta),
            settings.rate_min,
            settings.rate_max,
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        nation.rate_prev = nation.exchange_rate
        nation.exchange_rate = new_rate
        nation.active_members_24h = int(active)
        nation.last_rate_update = now

        session.add(
            RateHistory(
                nation_id=nation.nation_id,
                rate=new_rate,
                volume=nation.trade_volume_24h,
                active_members=int(active),
                calculated_at=now,
            )
        )
        session.add(
            NationMemberHistory(
                nation_id=nation.nation_id,
                member_count=nation.member_count,
                recorded_at=now,
            )
        )

    await session.flush()


async def create_behavior_snapshot(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> BehaviorSnapshot:
    now = now or datetime.utcnow()
    since = now - timedelta(minutes=15)

    active_player_ids = await get_active_player_ids(session, since=since)
    wealth_map = await get_player_net_worths(session, active_player_ids)
    wealth_values = list(wealth_map.values())

    counts = await count_transactions(session, since=since)
    total_volume = await calculate_total_volume(session, since=since)

    average = (
        sum(wealth_values, Decimal("0")) / Decimal(len(wealth_values))
        if wealth_values
        else Decimal("0")
    )

    snapshot = BehaviorSnapshot(
        at=now,
        active_players_count=len(active_player_ids),
        buy_tx_count=counts["buy"],
        sell_tx_count=counts["sell"],
        export_tx_count=counts["export"],
        import_tx_count=counts["import"],
        total_volume=total_volume,
        avg_net_worth=average.quantize(Decimal("0.000001")),
        median_net_worth=median_decimal(wealth_values).quantize(Decimal("0.000001")),
        gini_coefficient=gini_coefficient(wealth_values),
        top10_wealth_share=top10_wealth_share(wealth_values),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def update_nation_ranks(session: AsyncSession) -> None:
    nations = (
        await session.execute(
            select(Nation).order_by(
                Nation.exchange_rate.desc(),
                Nation.nation_id.asc(),
            )
        )
    ).scalars().all()
    now = datetime.utcnow()

    for index, nation in enumerate(nations, start=1):
        nation.nation_rank = index
        existing = await session.get(NationRank, nation.nation_id)
        if existing is None:
            session.add(
                NationRank(
                    nation_id=nation.nation_id,
                    rank=index,
                    calculated_at=now,
                )
            )
        else:
            existing.rank = index
            existing.calculated_at = now

    await session.flush()


async def reset_daily_metrics(session: AsyncSession) -> None:
    nations = (
        await session.execute(
            select(Nation).where(Nation.is_active.is_(True))
        )
    ).scalars().all()

    for nation in nations:
        nation.rate_24h_open = nation.exchange_rate
        nation.trade_volume_24h = Decimal("0")

    cutoff = datetime.utcnow() - timedelta(days=settings.behavior_snapshot_retention_days)
    await session.execute(
        delete(BehaviorSnapshot).where(BehaviorSnapshot.at < cutoff)
    )
    await session.flush()


async def get_active_members(
    session: AsyncSession,
    nation_id: int,
    hours: int = 24,
) -> int:
    since = datetime.utcnow() - timedelta(hours=hours)
    return int(
        await session.scalar(
            select(func.count(distinct(UserActivity.user_id))).where(
                UserActivity.nation_id == nation_id,
                UserActivity.created_at >= since,
            )
        )
        or 0
    )
