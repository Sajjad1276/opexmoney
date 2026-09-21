from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BehaviorSnapshot,
    Nation,
    NationMemberHistory,
    NationTelegramMember,
    NationRank,
    RateHistory,
    User,
    UserActivity,
)
from app.services.market.market_pressure import _read_pressure_signals
from app.services.market.market_state import get_currency_state
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


@dataclass(frozen=True)
class RateFactors:
    activity_score: Decimal
    trade_score: Decimal
    growth_score: Decimal
    pressure_signal: Decimal
    foreign_signal: Decimal
    dominant_cause: str


@dataclass(frozen=True)
class RateDelta:
    nation_id: int
    old_rate: Decimal
    new_rate: Decimal
    delta_pct: Decimal
    factors: RateFactors


@dataclass(frozen=True)
class ReceiptLine:
    label: str
    impact_pct: Decimal
    direction: str


@dataclass(frozen=True)
class PriceReceipt:
    currency_code: str
    total_change_pct: Decimal
    factors: list[ReceiptLine]


_RATE_FACTOR_HISTORY: dict[int, list[RateDelta]] = defaultdict(list)
_MAX_FACTOR_HISTORY = 20


async def _member_counts(
    session: AsyncSession,
    nation: Nation,
    since_24h: datetime,
) -> tuple[int, int]:
    if nation.is_ai:
        total = int(await session.scalar(
            select(func.count(User.user_id)).where(
                User.home_nation_id == nation.nation_id,
            )
        ) or 0)
        active = int(await session.scalar(
            select(func.count(distinct(UserActivity.user_id))).where(
                UserActivity.nation_id == nation.nation_id,
                UserActivity.created_at >= since_24h,
            )
        ) or 0)
        return total, active

    total = int(await session.scalar(
        select(func.count(NationTelegramMember.id)).where(
            NationTelegramMember.nation_id == nation.nation_id,
            NationTelegramMember.is_active.is_(True),
        )
    ) or 0)
    active = int(await session.scalar(
        select(func.count(distinct(UserActivity.user_id)))
        .join(
            NationTelegramMember,
            NationTelegramMember.telegram_user_id == UserActivity.user_id,
        )
        .where(
            UserActivity.nation_id == nation.nation_id,
            UserActivity.created_at >= since_24h,
            NationTelegramMember.nation_id == nation.nation_id,
            NationTelegramMember.is_active.is_(True),
        )
    ) or 0)
    return total, active


def _dominant_cause(
    activity_score: Decimal,
    trade_score: Decimal,
    growth_score: Decimal,
    pressure_signal: Decimal,
    foreign_signal: Decimal,
) -> str:
    weighted = {
        "activity_score": abs(activity_score) * Decimal("0.30"),
        "trade_score": abs(trade_score) * Decimal("0.25"),
        "growth_score": abs(growth_score) * Decimal("0.20"),
        "pressure_signal": abs(pressure_signal) * Decimal("0.15"),
        "foreign_signal": abs(foreign_signal) * Decimal("0.10"),
    }
    return max(weighted, key=weighted.get)


async def calculate_rate_delta(
    session: AsyncSession,
    nation: Nation,
    now: datetime,
) -> RateDelta:
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    total, active = await _member_counts(session, nation, since_24h)

    activity_score = (
        Decimal(active) / Decimal(total)
        if total
        else Decimal("0")
    )

    trade_volume = Decimal(str(nation.trade_volume_24h or 0))
    trade_score = min(
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
        growth_score = Decimal("0")
    else:
        current_members = (
            nation.member_count if nation.is_ai else total
        )
        growth_score = clamp(
            Decimal(current_members - old_members) / Decimal(old_members),
            Decimal("-0.5"),
            Decimal("0.5"),
        )

    market_state = await get_currency_state(
        session,
        nation_id=nation.nation_id,
        window_minutes=15,
    )

    from app.core.redis import get_redis

    pressure_signal, foreign_signal = await _read_pressure_signals(
        get_redis(),
        nation.nation_id,
    )

    market_score = (
        activity_score * Decimal("0.30")
        + trade_score * Decimal("0.25")
        + growth_score * Decimal("0.20")
        + pressure_signal * Decimal("0.15")
        + foreign_signal * Decimal("0.10")
    )
    # Preserve the existing neutral-market baseline: a zero score remains
    # a -0.50 offset, so a nation with no activity/trading does not jump
    # from 1.0000 to 1.0000 merely because the factor weighting changed.
    raw_delta = (market_score - Decimal("0.50")) * settings.rate_base_step
    volatility_multiplier = Decimal(str(
        await resolve(
            session,
            "rate.volatility_multiplier",
            nation_id=nation.nation_id,
        )
    ))
    raw_delta = apply_rate_volatility(raw_delta, volatility_multiplier)

    new_rate = clamp(
        Decimal(str(nation.exchange_rate)) * (Decimal("1") + raw_delta),
        settings.rate_min,
        settings.rate_max,
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    old_rate = Decimal(str(nation.exchange_rate))
    delta_pct = (
        ((new_rate - old_rate) / old_rate) * Decimal("100")
        if old_rate
        else Decimal("0")
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    factors = RateFactors(
        activity_score=activity_score,
        trade_score=trade_score,
        growth_score=growth_score,
        pressure_signal=pressure_signal,
        foreign_signal=foreign_signal,
        dominant_cause=_dominant_cause(
            activity_score,
            trade_score,
            growth_score,
            pressure_signal,
            foreign_signal,
        ),
    )
    return RateDelta(
        nation_id=nation.nation_id,
        old_rate=old_rate,
        new_rate=new_rate,
        delta_pct=delta_pct,
        factors=factors,
    )


async def persist_rate_update(
    session: AsyncSession,
    delta: RateDelta,
    now: datetime,
) -> None:
    nation = await session.get(Nation, delta.nation_id)
    if nation is None:
        return

    total, active = await _member_counts(
        session,
        nation,
        now - timedelta(hours=24),
    )
    nation.rate_prev = nation.exchange_rate
    nation.exchange_rate = delta.new_rate
    nation.active_members_24h = active
    if not nation.is_ai:
        nation.member_count = total
    nation.last_rate_update = now

    session.add(
        RateHistory(
            nation_id=nation.nation_id,
            rate=delta.new_rate,
            volume=nation.trade_volume_24h,
            active_members=active,
            calculated_at=now,
            dominant_cause=delta.factors.dominant_cause,
            pressure_signal=delta.factors.pressure_signal,
            foreign_signal=delta.factors.foreign_signal,
            activity_score=delta.factors.activity_score,
            trade_score=delta.factors.trade_score,
            growth_score=delta.factors.growth_score,
        )
    )
    session.add(
        NationMemberHistory(
            nation_id=nation.nation_id,
            member_count=total,
            recorded_at=now,
        )
    )
    await session.flush()


async def update_nation_rates(
    session: AsyncSession,
    now: datetime | None = None,
) -> list[RateDelta]:
    now = now or datetime.utcnow()
    nations = (
        await session.execute(
            select(Nation).where(Nation.is_active.is_(True))
        )
    ).scalars().all()

    deltas: list[RateDelta] = []
    for nation in nations:
        delta = await calculate_rate_delta(session, nation, now)
        await persist_rate_update(session, delta, now)
        history = _RATE_FACTOR_HISTORY[nation.nation_id]
        history.append(delta)
        if len(history) > _MAX_FACTOR_HISTORY:
            del history[:-_MAX_FACTOR_HISTORY]
        deltas.append(delta)
    return deltas


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
    nation = await session.get(Nation, nation_id)
    if nation is None:
        return 0

    stmt = (
        select(func.count(distinct(UserActivity.user_id)))
        .where(
            UserActivity.nation_id == nation_id,
            UserActivity.created_at >= since,
        )
    )
    if not nation.is_ai:
        stmt = stmt.join(
            NationTelegramMember,
            NationTelegramMember.telegram_user_id == UserActivity.user_id,
        ).where(
            NationTelegramMember.nation_id == nation_id,
            NationTelegramMember.is_active.is_(True),
        )

    return int(await session.scalar(stmt) or 0)


async def build_price_receipt(
    session: AsyncSession,
    nation_id: int,
    last_n_updates: int = 3,
) -> PriceReceipt:
    nation = await session.get(Nation, nation_id)
    if nation is None:
        raise ValueError("ملت پیدا نشد.")

    limit = max(1, int(last_n_updates))
    history_rows = list(reversed((
        await session.execute(
            select(RateHistory)
            .where(RateHistory.nation_id == nation_id)
            .order_by(RateHistory.calculated_at.desc())
            .limit(limit)
        )
    ).scalars().all()))
    if len(history_rows) >= 2 and history_rows[0].rate:
        total_change = (
            (history_rows[-1].rate - history_rows[0].rate)
            / history_rows[0].rate
            * Decimal("100")
        )
    else:
        total_change = Decimal("0")

    deltas = _RATE_FACTOR_HISTORY.get(nation_id, [])[-limit:]
    component_specs = (
        ("فعالیت اعضا", Decimal("0.30"), "activity_score"),
        ("حجم معاملات", Decimal("0.25"), "trade_score"),
        ("رشد ملت", Decimal("0.20"), "growth_score"),
        ("فشار بازار", Decimal("0.15"), "pressure_signal"),
        ("تقاضای خارجی", Decimal("0.10"), "foreign_signal"),
    )
    totals: dict[str, Decimal] = {
        label: Decimal("0") for label, _, _ in component_specs
    }
    for delta in deltas:
        for label, weight, attribute in component_specs:
            value = Decimal(str(getattr(delta.factors, attribute)))
            totals[label] += value * weight * settings.rate_base_step * Decimal("100")

    factors = [
        ReceiptLine(
            label=label,
            impact_pct=value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            direction="+" if value >= 0 else "-",
        )
        for label, value in totals.items()
        if value != 0
    ]
    return PriceReceipt(
        currency_code=nation.currency_code,
        total_change_pct=Decimal(str(total_change)).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        ),
        factors=factors,
    )
