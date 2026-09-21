from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationMember,
    NationMemberRole,
    NationTelegramMember,
    RateHistory,
    User,
)
from app.database.session import async_session
from app.services.dashboard_service import build_live_dashboard, render_live_dashboard


USER_ID = 932777
GROUP_ID = -100932777
CURRENCY = "LVD"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    select(Nation.nation_id).where(Nation.group_id == GROUP_ID)
                )
            ).scalars().all()

            if nation_ids:
                await session.execute(
                    delete(RateHistory).where(
                        RateHistory.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(CurrencyHolding).where(
                        CurrencyHolding.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationMember).where(
                        NationMember.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationTelegramMember).where(
                        NationTelegramMember.nation_id.in_(nation_ids)
                    )
                )

            await session.execute(
                delete(User).where(User.user_id == USER_ID)
            )

            if nation_ids:
                await session.execute(
                    delete(Nation).where(
                        Nation.nation_id.in_(nation_ids)
                    )
                )


@pytest.mark.asyncio
async def test_live_dashboard_requires_verified_human_membership():
    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name="Live Dashboard",
                currency_code=CURRENCY,
                group_id=GROUP_ID,
                founder_user_id=None,
                exchange_rate=Decimal("1.2500"),
                rate_prev=Decimal("1.2000"),
                rate_24h_open=Decimal("1.1000"),
                trade_volume_24h=Decimal("500"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-LIVE-DASH",
                treasury=Decimal("0"),
            )
            user = User(
                user_id=USER_ID,
                username="livedashboard",
                home_nation_id=None,
                balance=Decimal("800"),
                xr_balance=Decimal("200"),
                role="player",
            )
            session.add_all([nation, user])
            await session.flush()

            session.add_all(
                [
                    NationMember(
                        nation_id=nation.nation_id,
                        user_id=USER_ID,
                        role=NationMemberRole.CITIZEN,
                        is_active=True,
                    ),
                    NationTelegramMember(
                        nation_id=nation.nation_id,
                        telegram_user_id=USER_ID,
                        telegram_status="member",
                        is_member=True,
                        is_active=True,
                        joined_at=datetime.utcnow(),
                    ),
                    CurrencyHolding(
                        user_id=USER_ID,
                        nation_id=nation.nation_id,
                        amount=Decimal("800"),
                    ),
                    RateHistory(
                        nation_id=nation.nation_id,
                        rate=Decimal("1.2500"),
                        volume=Decimal("500"),
                        active_members=1,
                        calculated_at=datetime.utcnow(),
                    ),
                ]
            )

    async with async_session() as session:
        async with session.begin():
            dashboard = await build_live_dashboard(session, USER_ID)

    assert dashboard.has_nation is True
    assert dashboard.currency_code == CURRENCY
    assert dashboard.national_rank == 1
    assert dashboard.local_balance == Decimal("800")
    assert dashboard.change_24h > Decimal("10")

    rendered = render_live_dashboard(dashboard)
    assert "Live Dashboard" in rendered
    assert CURRENCY in rendered
    assert "رتبه ملت" in rendered
