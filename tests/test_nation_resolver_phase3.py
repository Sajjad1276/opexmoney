from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationMember,
    NationMemberRole,
    NationTelegramMember,
    User,
)
from app.database.session import async_session
from app.services.nation_service import get_user_active_nation_context


HUMAN_USER_ID = 940001
HUMAN_GROUP_ID = -100940001
AI_USER_ID = 940002


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    select(Nation.nation_id).where(
                        (Nation.group_id == HUMAN_GROUP_ID)
                        | Nation.is_ai.is_(True)
                    )
                )
            ).scalars().all()
            if nation_ids:
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
                    delete(CurrencyHolding).where(
                        CurrencyHolding.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(Nation).where(
                        Nation.nation_id.in_(nation_ids)
                    )
                )
            await session.execute(
                delete(User).where(
                    User.user_id.in_([HUMAN_USER_ID, AI_USER_ID])
                )
            )


async def _seed_human(*, telegram_active: bool, game_member: bool = True):
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=HUMAN_USER_ID,
                username="resolver_human",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            nation = Nation(
                name="Resolver Republic",
                currency_code="R941",
                group_id=HUMAN_GROUP_ID,
                founder_user_id=HUMAN_USER_ID,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-RESOLVER",
                treasury=Decimal("0"),
            )
            session.add_all([user, nation])
            await session.flush()
            user.home_nation_id = nation.nation_id

            if game_member:
                session.add(
                    NationMember(
                        nation_id=nation.nation_id,
                        user_id=HUMAN_USER_ID,
                        role=NationMemberRole.FOUNDER,
                        is_active=True,
                    )
                )

            session.add(
                NationTelegramMember(
                    nation_id=nation.nation_id,
                    telegram_user_id=HUMAN_USER_ID,
                    telegram_status="member" if telegram_active else "left",
                    is_member=telegram_active,
                    is_active=telegram_active,
                )
            )
            await session.flush()
            return nation.nation_id


@pytest.mark.asyncio
async def test_human_legacy_membership_without_telegram_is_not_authoritative():
    nation_id = await _seed_human(telegram_active=False, game_member=True)

    async with async_session() as session:
        context = await get_user_active_nation_context(session, HUMAN_USER_ID)

    assert context is None

    async with async_session() as session:
        context = await get_user_active_nation_context(
            session,
            HUMAN_USER_ID,
            repair=True,
            lock=True,
        )
        assert context is None
        member = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == HUMAN_USER_ID,
            )
        )
        assert member is not None
        assert member.is_active is True


@pytest.mark.asyncio
async def test_human_active_telegram_membership_is_required_and_resolved():
    nation_id = await _seed_human(telegram_active=True, game_member=True)

    async with async_session() as session:
        context = await get_user_active_nation_context(session, HUMAN_USER_ID)

    assert context is not None
    nation, role, source = context
    assert nation.nation_id == nation_id
    assert role == NationMemberRole.FOUNDER.value
    assert source in {"home_membership", "telegram_membership"}


@pytest.mark.asyncio
async def test_human_repair_can_create_game_membership_only_with_telegram_proof():
    nation_id = await _seed_human(telegram_active=True, game_member=False)

    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                HUMAN_USER_ID,
                repair=True,
                lock=True,
            )

            assert context is not None
            nation, role, source = context
            assert nation.nation_id == nation_id
            assert role == NationMemberRole.FOUNDER.value
            assert source == "repaired_telegram_membership"

            member = await session.scalar(
                select(NationMember).where(
                    NationMember.nation_id == nation_id,
                    NationMember.user_id == HUMAN_USER_ID,
                )
            )
            assert member is not None
            assert member.is_active is True


@pytest.mark.asyncio
async def test_ai_membership_remains_virtual_without_telegram_projection():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=AI_USER_ID,
                username="resolver_ai",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            nation = Nation(
                name="Virtual Republic",
                currency_code="VIR",
                group_id=None,
                founder_user_id=AI_USER_ID,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=10,
                member_count=10,
                is_active=True,
                is_ai=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-VIRTUAL",
                treasury=Decimal("0"),
            )
            session.add_all([user, nation])
            await session.flush()
            user.home_nation_id = nation.nation_id
            session.add(
                NationMember(
                    nation_id=nation.nation_id,
                    user_id=AI_USER_ID,
                    role=NationMemberRole.CITIZEN,
                    is_active=True,
                )
            )

    async with async_session() as session:
        context = await get_user_active_nation_context(session, AI_USER_ID)

    assert context is not None
    nation, role, source = context
    assert nation.is_ai is True
    assert role == NationMemberRole.CITIZEN.value
    assert source == "home_membership"


@pytest.mark.asyncio
async def test_human_holding_and_founder_fallbacks_are_blocked_without_telegram():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=HUMAN_USER_ID,
                username="resolver_human",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="founder",
            )
            nation = Nation(
                name="Fallback Blocked",
                currency_code="BLK",
                group_id=HUMAN_GROUP_ID,
                founder_user_id=HUMAN_USER_ID,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-BLOCKED",
                treasury=Decimal("0"),
            )
            session.add_all([user, nation])
            await session.flush()
            session.add(
                CurrencyHolding(
                    user_id=HUMAN_USER_ID,
                    nation_id=nation.nation_id,
                    amount=Decimal("500"),
                )
            )
            await session.flush()

    async with async_session() as session:
        context = await get_user_active_nation_context(session, HUMAN_USER_ID)

    assert context is None
