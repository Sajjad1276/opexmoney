from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.database.models import Nation, RateHistory, User, UserActivity
from app.database.session import async_session


USER_ID = 933001
GROUP_ID = -100933001


@pytest.mark.asyncio
async def test_bigint_history_ids_are_database_generated():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=USER_ID,
                username="identitytest",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            nation = Nation(
                name="Identity Republic",
                currency_code="IDS",
                group_id=GROUP_ID,
                founder_user_id=USER_ID,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-IDENTITY",
                treasury=Decimal("0"),
            )
            session.add_all([user, nation])
            await session.flush()

            activity = UserActivity(
                user_id=USER_ID,
                nation_id=nation.nation_id,
                activity_type="login",
            )
            rate = RateHistory(
                nation_id=nation.nation_id,
                rate=Decimal("1.25"),
                volume=Decimal("10"),
                active_members=1,
            )
            session.add_all([activity, rate])
            await session.flush()

            assert activity.id is not None
            assert rate.id is not None

            activity_id = activity.id
            rate_id = rate.id

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(UserActivity).where(UserActivity.user_id == USER_ID)
            )
            await session.execute(
                delete(RateHistory).where(RateHistory.nation_id == nation.nation_id)
            )
            await session.execute(delete(User).where(User.user_id == USER_ID))
            await session.execute(
                delete(Nation).where(Nation.nation_id == nation.nation_id)
            )

    assert activity_id > 0
    assert rate_id > 0
