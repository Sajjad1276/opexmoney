from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NationEconomicEvent


def record_economic_event(
    session: AsyncSession,
    *,
    nation_id: int,
    event_type: str,
    actor_id: int | None = None,
    target_nation_id: int | None = None,
    amount_xr: Decimal | int | float | str = Decimal("0"),
    amount_local: Decimal | int | float | str = Decimal("0"),
    metadata: dict[str, Any] | None = None,
) -> NationEconomicEvent:
    event = NationEconomicEvent(
        nation_id=nation_id,
        event_type=event_type,
        actor_id=actor_id,
        target_nation_id=target_nation_id,
        amount_xr=Decimal(str(amount_xr)),
        amount_local=Decimal(str(amount_local)),
        event_metadata=metadata or None,
    )
    session.add(event)
    return event


async def recent_economic_events(
    session: AsyncSession,
    nation_id: int,
    *,
    limit: int = 100,
) -> list[NationEconomicEvent]:
    from sqlalchemy import select

    result = await session.execute(
        select(NationEconomicEvent)
        .where(NationEconomicEvent.nation_id == nation_id)
        .order_by(
            NationEconomicEvent.created_at.desc(),
            NationEconomicEvent.id.desc(),
        )
        .limit(max(1, min(int(limit), 500)))
    )
    return list(result.scalars().all())
