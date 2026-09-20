from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationEconomicEvent,
    NationMember,
    NationTelegramMember,
    User,
)
from app.database.session import async_session
from app.services.economic_event_service import record_economic_event
from app.services.membership_service import sync_telegram_membership
from app.services.nation_service import get_user_active_nation_context


USER_ID = 941001
GROUP_A = -100941001
GROUP_B = -100941002
CURRENCY_A = "P31"
CURRENCY_B = "P32"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    select(Nation.nation_id).where(
                        Nation.group_id.in_([GROUP_A, GROUP_B])
                    )
                )
            ).scalars().all()
            if nation_ids:
                await session.execute(
                    delete(NationEconomicEvent).where(
                        NationEconomicEvent.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(CurrencyHolding).where(
                        CurrencyHolding.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationTelegramMember).where(
                        NationTelegramMember.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationMember).where(
                        NationMember.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(Nation).where(Nation.nation_id.in_(nation_ids))
                )
            await session.execute(delete(User).where(User.user_id == USER_ID))


async def _seed_human_nations() -> tuple[int, int]:
    async with async_session() as session:
        async with session.begin():
            session.add(
                User(
                    user_id=USER_ID,
                    username="phase310user",
                    balance=Decimal("500"),
                    xr_balance=Decimal("200"),
                    role="player",
                )
            )
            nation_a = Nation(
                name="Phase Three One",
                currency_code=CURRENCY_A,
                group_id=GROUP_A,
                founder_user_id=None,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=0,
                member_count=0,
                is_active=True,
                is_ai=False,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-PHASE310-A",
                treasury=Decimal("0"),
            )
            nation_b = Nation(
                name="Phase Three Two",
                currency_code=CURRENCY_B,
                group_id=GROUP_B,
                founder_user_id=None,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=0,
                member_count=0,
                is_active=True,
                is_ai=False,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-PHASE310-B",
                treasury=Decimal("0"),
            )
            session.add_all([nation_a, nation_b])
            await session.flush()
            return nation_a.nation_id, nation_b.nation_id


@pytest.mark.asyncio
async def test_telegram_membership_is_authoritative_and_home_wallet_survives_second_nation():
    nation_a_id, nation_b_id = await _seed_human_nations()

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, USER_ID, with_for_update=True)
            session.add(
                CurrencyHolding(
                    user_id=USER_ID,
                    nation_id=nation_a_id,
                    amount=Decimal("777"),
                )
            )
            session.add(
                CurrencyHolding(
                    user_id=USER_ID,
                    nation_id=nation_b_id,
                    amount=Decimal("333"),
                )
            )
            await session.flush()

            first = await sync_telegram_membership(
                session,
                group_id=GROUP_A,
                telegram_user_id=USER_ID,
                telegram_status="member",
                is_member=True,
                source="test:phase3",
            )
            assert first is not None
            assert first.became_active is True

            context = await get_user_active_nation_context(
                session,
                USER_ID,
                repair=False,
                lock=False,
            )
            assert context is not None
            assert context[0].nation_id == nation_a_id
            assert user.home_nation_id == nation_a_id
            assert user.balance == Decimal("777")

            second = await sync_telegram_membership(
                session,
                group_id=GROUP_B,
                telegram_user_id=USER_ID,
                telegram_status="member",
                is_member=True,
                source="test:phase3",
            )
            assert second is not None
            assert second.became_active is True

            user = await session.get(User, USER_ID)
            assert user is not None
            assert user.home_nation_id == nation_a_id
            assert user.balance == Decimal("777")

            nation_b_member = await session.scalar(
                select(NationMember).where(
                    NationMember.nation_id == nation_b_id,
                    NationMember.user_id == USER_ID,
                    NationMember.is_active.is_(True),
                )
            )
            nation_b_projection = await session.scalar(
                select(NationTelegramMember).where(
                    NationTelegramMember.nation_id == nation_b_id,
                    NationTelegramMember.telegram_user_id == USER_ID,
                    NationTelegramMember.is_active.is_(True),
                )
            )
            assert nation_b_member is not None
            assert nation_b_projection is not None


@pytest.mark.asyncio
async def test_economic_event_is_durable():
    nation_a_id, _ = await _seed_human_nations()

    async with async_session() as session:
        async with session.begin():
            event = record_economic_event(
                session,
                nation_id=nation_a_id,
                event_type="TEST_EVENT",
                actor_id=USER_ID,
                amount_xr=Decimal("12.50"),
                amount_local=Decimal("5"),
                metadata={"phase": "8"},
            )
            await session.flush()
            assert event.id is not None
            assert event.nation_id == nation_a_id
            assert event.amount_xr == Decimal("12.500000")
