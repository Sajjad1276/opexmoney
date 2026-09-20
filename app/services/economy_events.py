from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import EconomyEventOutbox
from app.database.session import async_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EconomyEvent:
    id: int
    event_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: int
    payload: dict
    attempts: int


async def enqueue_event(
    session: AsyncSession,
    *,
    event_key: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int,
    payload: dict,
) -> EconomyEventOutbox:
    existing = await session.scalar(
        select(EconomyEventOutbox)
        .where(EconomyEventOutbox.event_key == event_key)
        .limit(1)
    )
    if existing is not None:
        return existing

    row = EconomyEventOutbox(
        event_key=event_key,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        available_at=datetime.utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def claim_events(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[EconomyEvent]:
    now = datetime.utcnow()
    rows = list(
        (
            await session.execute(
                select(EconomyEventOutbox)
                .where(
                    EconomyEventOutbox.published_at.is_(None),
                    EconomyEventOutbox.available_at <= now,
                )
                .order_by(EconomyEventOutbox.id.asc())
                .limit(max(1, min(int(limit), 500)))
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
    )

    events: list[EconomyEvent] = []
    for row in rows:
        row.attempts = int(row.attempts or 0) + 1
        events.append(
            EconomyEvent(
                id=row.id,
                event_key=row.event_key,
                event_type=row.event_type,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                payload=json.loads(row.payload_json or "{}"),
                attempts=row.attempts,
            )
        )
    await session.flush()
    return events


async def mark_published(session: AsyncSession, event_id: int) -> None:
    row = await session.get(EconomyEventOutbox, event_id, with_for_update=True)
    if row is None:
        return
    row.published_at = datetime.utcnow()
    row.last_error = None
    await session.flush()


async def mark_failed(
    session: AsyncSession,
    event_id: int,
    error: str,
    *,
    retry_after_seconds: int = 60,
) -> None:
    row = await session.get(EconomyEventOutbox, event_id, with_for_update=True)
    if row is None:
        return
    row.last_error = error[:4000]
    row.available_at = datetime.utcnow() + timedelta(seconds=max(1, retry_after_seconds))
    await session.flush()


async def publish_event(event: EconomyEvent) -> None:
    """Default in-process publisher.

    The durable outbox is the boundary. External transports can replace this
    function later without changing transaction semantics.
    """
    logger.info(
        "ECONOMY_EVENT | id=%s key=%s type=%s aggregate=%s:%s attempts=%s payload=%s",
        event.id,
        event.event_key,
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
        event.attempts,
        event.payload,
    )


async def drain_outbox(
    *,
    publisher: Callable[[EconomyEvent], Awaitable[None]] = publish_event,
    limit: int = 50,
) -> tuple[int, int]:
    published = 0
    failed = 0

    while published + failed < limit:
        async with async_session() as session:
            async with session.begin():
                events = await claim_events(
                    session,
                    limit=min(50, limit - published - failed),
                )
                if not events:
                    break

        for event in events:
            try:
                await publisher(event)
            except Exception as exc:
                failed += 1
                async with async_session() as session:
                    async with session.begin():
                        await mark_failed(session, event.id, str(exc))
                logger.exception("Economy event publish failed | event_id=%s", event.id)
            else:
                published += 1
                async with async_session() as session:
                    async with session.begin():
                        await mark_published(session, event.id)

    return published, failed
