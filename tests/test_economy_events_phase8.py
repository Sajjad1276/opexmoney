from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.database.models import EconomyEventOutbox
from app.database.session import async_session
from app.services.economy_events import (
    claim_events,
    enqueue_event,
    mark_failed,
    mark_published,
)


EVENT_KEY = "phase8-test-event-944001"


@pytest.mark.asyncio
async def test_economy_outbox_claim_publish_and_retry_are_idempotent():
    async with async_session() as session:
        async with session.begin():
            # This test owns the entire outbox fixture. Earlier integration
            # tests legitimately emit membership/trade events, so isolate the
            # lifecycle test from those rows rather than asserting global queue order.
            await session.execute(delete(EconomyEventOutbox))
            row = await enqueue_event(
                session,
                event_key=EVENT_KEY,
                event_type="test.event",
                aggregate_type="test",
                aggregate_id=944001,
                payload={"ok": True},
            )
            assert row.id is not None

    async with async_session() as session:
        async with session.begin():
            events = await claim_events(session, limit=10)
            assert [event.event_key for event in events] == [EVENT_KEY]
            event_id = events[0].id
            attempts = events[0].attempts

    async with async_session() as session:
        async with session.begin():
            await mark_failed(session, event_id, "temporary", retry_after_seconds=1)

    async with async_session() as session:
        row = await session.get(EconomyEventOutbox, event_id)
        assert row.published_at is None
        assert row.attempts == attempts

    async with async_session() as session:
        async with session.begin():
            await mark_published(session, event_id)

    async with async_session() as session:
        row = await session.get(EconomyEventOutbox, event_id)
        assert row.published_at is not None

    async with async_session() as session:
        async with session.begin():
            duplicate = await enqueue_event(
                session,
                event_key=EVENT_KEY,
                event_type="test.event",
                aggregate_type="test",
                aggregate_id=944001,
                payload={"ok": True},
            )
            assert duplicate.id == event_id
            await session.execute(delete(EconomyEventOutbox))
