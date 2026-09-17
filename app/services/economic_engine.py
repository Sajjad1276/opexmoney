from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import log10

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, NationMemberHistory, NationRank, RateHistory, User, UserActivity

RATE_MIN = Decimal("0.10")
RATE_MAX = Decimal("50.00")


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


async def update_nation_rates(session: AsyncSession, now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    nations = (await session.execute(select(Nation).where(Nation.is_active.is_(True)))).scalars().all()
    for nation in nations:
        active = await session.scalar(select(func.count(distinct(UserActivity.user_id))).where(UserActivity.nation_id == nation.nation_id, UserActivity.created_at >= since_24h)) or 0
        total = await session.scalar(select(func.count(User.user_id)).where(User.home_nation_id == nation.nation_id)) or 0
        f_activity = Decimal(active) / Decimal(total) if total else Decimal("0")
        f_trade = Decimal(str(min(log10(float(nation.trade_volume_24h) + 1.0) / 6.0, 1.0)))
        old_members = await session.scalar(select(NationMemberHistory.member_count).where(NationMemberHistory.nation_id == nation.nation_id, NationMemberHistory.recorded_at <= since_7d).order_by(NationMemberHistory.recorded_at.desc()).limit(1))
        f_growth = Decimal("0") if old_members in (None, 0) else clamp(Decimal(nation.member_count - old_members) / Decimal(old_members), Decimal("-0.5"), Decimal("0.5"))
        score = f_activity * Decimal("0.4") + f_trade * Decimal("0.3") + f_growth * Decimal("0.3")
        delta = (score - Decimal("0.5")) * Decimal("0.04")
        new_rate = clamp(nation.exchange_rate * (Decimal("1") + delta), RATE_MIN, RATE_MAX).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        nation.rate_prev = nation.exchange_rate
        nation.exchange_rate = new_rate
        nation.active_members_24h = int(active)
        nation.last_rate_update = now
        session.add(RateHistory(nation_id=nation.nation_id, rate=new_rate, volume=nation.trade_volume_24h, active_members=int(active), calculated_at=now))
        session.add(NationMemberHistory(nation_id=nation.nation_id, member_count=nation.member_count, recorded_at=now))
    await session.commit()


async def update_nation_ranks(session: AsyncSession) -> None:
    nations = (await session.execute(select(Nation).order_by(Nation.exchange_rate.desc(), Nation.nation_id.asc()))).scalars().all()
    now = datetime.utcnow()
    for index, nation in enumerate(nations, start=1):
        nation.nation_rank = index
        existing = await session.get(NationRank, nation.nation_id)
        if existing is None:
            session.add(NationRank(nation_id=nation.nation_id, rank=index, calculated_at=now))
        else:
            existing.rank = index
            existing.calculated_at = now
    await session.commit()


async def reset_daily_metrics(session: AsyncSession) -> None:
    nations = (await session.execute(select(Nation).where(Nation.is_active.is_(True)))).scalars().all()
    for nation in nations:
        nation.rate_24h_open = nation.exchange_rate
        nation.trade_volume_24h = Decimal("0")
    await session.commit()


async def get_active_members(session: AsyncSession, nation_id: int, hours: int = 24) -> int:
    since = datetime.utcnow() - timedelta(hours=hours)
    return int(await session.scalar(select(func.count(distinct(UserActivity.user_id))).where(UserActivity.nation_id == nation_id, UserActivity.created_at >= since)) or 0)
