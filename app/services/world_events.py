from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Nation,
    WorldEvent,
    WorldEventEffectType,
    WorldEventScope,
    WorldEventSource,
    WorldEventType,
)

EVENT_DURATION = timedelta(minutes=90)
EVENT_REARM = timedelta(hours=2)
RATE_EVENT_THRESHOLD = Decimal("5")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _rate_change_pct(nation: Nation) -> Decimal:
    base = Decimal(str(nation.rate_24h_open or nation.exchange_rate or 0))
    current = Decimal(str(nation.exchange_rate or 0))
    if base <= 0:
        return Decimal("0")
    return ((current - base) / base * Decimal("100")).quantize(Decimal("0.01"))


async def expire_world_events(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    current = now or _utcnow()
    result = await session.execute(
        update(WorldEvent)
        .where(
            WorldEvent.is_active.is_(True),
            WorldEvent.ends_at <= current,
        )
        .values(is_active=False)
    )
    return int(result.rowcount or 0)


async def get_active_world_event(
    session: AsyncSession,
    *,
    nation_id: int | None = None,
    now: datetime | None = None,
) -> WorldEvent | None:
    current = now or _utcnow()
    statement = select(WorldEvent).where(
        WorldEvent.is_active.is_(True),
        WorldEvent.ends_at > current,
    )
    if nation_id is None:
        statement = statement.order_by(WorldEvent.started_at.desc())
    else:
        statement = statement.where(
            (WorldEvent.affected_nation_id == nation_id)
            | (WorldEvent.scope == WorldEventScope.GLOBAL)
        ).order_by(
            (WorldEvent.affected_nation_id == nation_id).desc(),
            WorldEvent.started_at.desc(),
        )
    return await session.scalar(statement.limit(1))


async def get_recent_world_events(
    session: AsyncSession,
    *,
    nation_id: int | None = None,
    limit: int = 5,
) -> list[WorldEvent]:
    """Return recent events relevant to the player and the public world feed."""
    limit = max(1, min(limit, 20))
    statement = select(WorldEvent).order_by(WorldEvent.started_at.desc()).limit(limit)
    if nation_id is not None:
        statement = (
            select(WorldEvent)
            .where(
                (WorldEvent.affected_nation_id == nation_id)
                | (WorldEvent.scope == WorldEventScope.GLOBAL)
            )
            .order_by(WorldEvent.started_at.desc())
            .limit(limit)
        )
    return list((await session.execute(statement)).scalars().all())


async def generate_market_world_event(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> WorldEvent | None:
    current = now or _utcnow()
    await expire_world_events(session, now=current)

    nations = (
        await session.execute(
            select(Nation).where(Nation.is_active.is_(True))
        )
    ).scalars().all()

    candidates = [
        (nation, _rate_change_pct(nation))
        for nation in nations
        if abs(_rate_change_pct(nation)) >= RATE_EVENT_THRESHOLD
    ]
    if not candidates:
        return None

    nation, change = max(candidates, key=lambda item: abs(item[1]))
    event_type = (
        WorldEventType.MARKET_BOOM
        if change > 0
        else WorldEventType.MARKET_CRASH
    )

    recent = await session.scalar(
        select(WorldEvent)
        .where(
            WorldEvent.affected_nation_id == nation.nation_id,
            WorldEvent.event_type == event_type,
            WorldEvent.started_at >= current - EVENT_REARM,
        )
        .order_by(WorldEvent.started_at.desc())
        .limit(1)
    )
    if recent is not None:
        return None

    direction = "جهش" if change > 0 else "سقوط"
    title = f"{direction} {nation.currency_code}"
    description = (
        f"ارز {nation.name} ({nation.currency_code}) "
        f"در ۲۴ ساعت گذشته {abs(change):.2f}٪ "
        f'{"رشد" if change > 0 else "افت"} داشته است.'
    )

    event = WorldEvent(
        event_type=event_type,
        scope=WorldEventScope.NATIONAL,
        affected_nation_id=nation.nation_id,
        affected_currency=nation.currency_code,
        title=title,
        description=description,
        effect_type=(
            WorldEventEffectType.RATE_BOOST
            if change > 0
            else WorldEventEffectType.RATE_DROP
        ),
        effect_magnitude=float(change),
        duration_minutes=int(EVENT_DURATION.total_seconds() // 60),
        started_at=current,
        ends_at=current + EVENT_DURATION,
        is_active=True,
        source=WorldEventSource.SCHEDULER,
        announced_in_group=False,
    )
    session.add(event)
    await session.flush()
    return event
