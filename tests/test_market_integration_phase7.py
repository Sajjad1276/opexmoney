from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.database.models import Nation
from app.database.session import async_session
from app.services.market_service import get_tradeable_nation


@pytest.mark.asyncio
async def test_market_boundary_excludes_inactive_nations():
    active_group = -100943001
    inactive_group = -100943002

    async with async_session() as session:
        async with session.begin():
            active = Nation(
                group_id=active_group,
                name="Market Active",
                currency_code="MAC",
                exchange_rate=Decimal("1"),
                invite_code="OPX-MARKET-943001",
                is_active=True,
            )
            inactive = Nation(
                group_id=inactive_group,
                name="Market Inactive",
                currency_code="MIC",
                exchange_rate=Decimal("1"),
                invite_code="OPX-MARKET-943002",
                is_active=False,
            )
            session.add_all([active, inactive])
            await session.flush()
            active_id = active.nation_id
            inactive_id = inactive.nation_id

    async with async_session() as session:
        assert (await get_tradeable_nation(session, active_id)).nation_id == active_id
        assert await get_tradeable_nation(session, inactive_id) is None

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(Nation).where(Nation.nation_id.in_([active_id, inactive_id]))
            )
