from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import secrets

import pytest
from sqlalchemy import delete, select

from app.database.models import Nation, WorldEvent, WorldEventType
from app.database.session import async_session
from app.services.world_events import generate_market_world_event, get_active_world_event


@pytest.mark.asyncio
async def test_world_event_is_created_from_real_rate_move() -> None:
    currency = "WE1"
    invite_code = f"test-world-{secrets.token_urlsafe(8)}"

    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name="World Event Nation",
                currency_code=currency,
                group_id=None,
                invite_code=invite_code,
                exchange_rate=Decimal("1.1000"),
                rate_prev=Decimal("1.0500"),
                rate_24h_open=Decimal("1.0000"),
                is_active=True,
                treasury=Decimal("0"),
            )
            session.add(nation)
            await session.flush()

            event = await generate_market_world_event(
                session,
                now=datetime.now(UTC),
            )

            assert event is not None
            assert event.event_type == WorldEventType.MARKET_BOOM
            assert event.affected_nation_id == nation.nation_id
            assert event.effect_magnitude == 10.0
            assert event.duration_minutes == 90

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(WorldEvent).where(
                    WorldEvent.affected_nation_id == nation.nation_id
                )
            )
            await session.execute(
                delete(Nation).where(Nation.nation_id == nation.nation_id)
            )


@pytest.mark.asyncio
async def test_recent_world_event_is_not_duplicated() -> None:
    currency = "WE2"
    invite_code = f"test-world-{secrets.token_urlsafe(8)}"
    now = datetime.now(UTC)

    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name="World Event Nation Two",
                currency_code=currency,
                group_id=None,
                invite_code=invite_code,
                exchange_rate=Decimal("0.9000"),
                rate_prev=Decimal("0.9500"),
                rate_24h_open=Decimal("1.0000"),
                is_active=True,
                treasury=Decimal("0"),
            )
            session.add(nation)
            await session.flush()

            first = await generate_market_world_event(session, now=now)
            second = await generate_market_world_event(
                session,
                now=now + timedelta(minutes=10),
            )
            active = await get_active_world_event(
                session,
                nation_id=nation.nation_id,
                now=now + timedelta(minutes=10),
            )

            assert first is not None
            assert first.event_type == WorldEventType.MARKET_CRASH
            assert second is None
            assert active is not None
            assert active.event_id == first.event_id

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(WorldEvent).where(
                    WorldEvent.affected_nation_id == nation.nation_id
                )
            )
            await session.execute(
                delete(Nation).where(Nation.nation_id == nation.nation_id)
            )


@pytest.mark.asyncio
async def test_active_world_event_is_visible_without_nation() -> None:
    currency = "WE3"
    invite_code = f"test-world-{secrets.token_urlsafe(8)}"
    now = datetime.now(UTC)

    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name="World Feed Nation",
                currency_code=currency,
                group_id=None,
                invite_code=invite_code,
                exchange_rate=Decimal("1.1000"),
                rate_prev=Decimal("1.0500"),
                rate_24h_open=Decimal("1.0000"),
                is_active=True,
                treasury=Decimal("0"),
            )
            session.add(nation)
            await session.flush()

            event = await generate_market_world_event(session, now=now)
            assert event is not None

            visible = await get_active_world_event(
                session,
                nation_id=None,
                now=now,
            )
            assert visible is not None
            assert visible.event_id == event.event_id

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(WorldEvent).where(
                    WorldEvent.affected_nation_id == nation.nation_id
                )
            )
            await session.execute(
                delete(Nation).where(Nation.nation_id == nation.nation_id)
            )
