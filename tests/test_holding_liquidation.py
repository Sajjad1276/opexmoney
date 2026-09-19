from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationLog,
    NationMember,
    NationMemberRole,
    Transaction,
    User,
)
from app.database.session import async_session
from app.handlers import nation_management as nm
from app.services.nation_service import convert_holding_to_xr


USER_ID = 932001
FOUNDER_ID = 932002
GROUP_ID = -100932001


class FakeBot:
    async def send_message(self, *args, **kwargs):
        return SimpleNamespace()


class FakeCall:
    def __init__(self, user_id: int, data: str):
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Liquidation",
            username=f"u{user_id}",
        )
        self.data = data
        self.message = None
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


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
                    delete(NationLog).where(NationLog.nation_id.in_(nation_ids))
                )
                await session.execute(
                    delete(Transaction).where(Transaction.nation_id.in_(nation_ids))
                )
                await session.execute(
                    delete(CurrencyHolding).where(CurrencyHolding.nation_id.in_(nation_ids))
                )
                await session.execute(
                    delete(NationMember).where(NationMember.nation_id.in_(nation_ids))
                )
            await session.execute(
                delete(User).where(User.user_id.in_([USER_ID, FOUNDER_ID]))
            )
            if nation_ids:
                await session.execute(
                    delete(Nation).where(Nation.nation_id.in_(nation_ids))
                )


async def seed_nation(*, rate: Decimal, prev: Decimal = Decimal("1")) -> int:
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=USER_ID,
                username="liquidation_user",
                balance=Decimal("500"),
                xr_balance=Decimal("10"),
                role="citizen",
            )
            founder = User(
                user_id=FOUNDER_ID,
                username="liquidation_founder",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="founder",
            )
            nation = Nation(
                name="Liquidation Nation",
                currency_code="LIQ",
                group_id=GROUP_ID,
                founder_user_id=FOUNDER_ID,
                exchange_rate=rate,
                rate_prev=prev,
                rate_24h_open=rate,
                trade_volume_24h=Decimal("0"),
                active_members_24h=2,
                member_count=2,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-LIQ",
                treasury=Decimal("0"),
            )
            session.add_all([user, founder, nation])
            await session.flush()
            session.add_all(
                [
                    NationMember(
                        nation_id=nation.nation_id,
                        user_id=USER_ID,
                        role=NationMemberRole.CITIZEN,
                        is_active=True,
                    ),
                    NationMember(
                        nation_id=nation.nation_id,
                        user_id=FOUNDER_ID,
                        role=NationMemberRole.FOUNDER,
                        is_active=True,
                    ),
                    CurrencyHolding(
                        user_id=USER_ID,
                        nation_id=nation.nation_id,
                        amount=Decimal("100"),
                    ),
                    CurrencyHolding(
                        user_id=FOUNDER_ID,
                        nation_id=nation.nation_id,
                        amount=Decimal("50"),
                    ),
                ]
            )
            user.home_nation_id = nation.nation_id
            founder.home_nation_id = nation.nation_id
            await session.flush()
            return nation.nation_id


@pytest.mark.asyncio
async def test_convert_holding_uses_current_rate_and_preserves_audit():
    nation_id = await seed_nation(rate=Decimal("2.50"))

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, USER_ID, with_for_update=True)
            nation = await session.get(Nation, nation_id, with_for_update=True)
            result = await convert_holding_to_xr(user, nation, session)
            assert result["xr_received"] == Decimal("250.0")

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == USER_ID,
                CurrencyHolding.nation_id == nation_id,
            )
        )
        tx = await session.scalar(
            select(Transaction)
            .where(
                Transaction.user_id == USER_ID,
                Transaction.nation_id == nation_id,
                Transaction.transaction_type == "liquidate",
            )
            .order_by(Transaction.id.desc())
        )
        log = await session.scalar(
            select(NationLog)
            .where(
                NationLog.target_id == USER_ID,
                NationLog.nation_id == nation_id,
                NationLog.action_type == "HOLDING_LIQUIDATED",
            )
            .order_by(NationLog.id.desc())
        )
        assert user.xr_balance == Decimal("260.00")
        assert holding.amount == Decimal("0.0000")
        assert tx is not None and tx.amount == Decimal("100.0000")
        assert tx.rate == Decimal("2.5000")
        assert log is not None
        assert log.event_metadata["xr_received"] == "250.0"


@pytest.mark.asyncio
async def test_kick_liquidates_holding_before_membership_is_deactivated(monkeypatch):
    nation_id = await seed_nation(rate=Decimal("1.50"))
    bot = FakeBot()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(nm, "publish_nation_event_analysis", noop)
    monkeypatch.setattr(nm, "_notify_founder", noop)

    call = FakeCall(FOUNDER_ID, f"nm:kick:{nation_id}:{USER_ID}")
    await nm.kick_member(call, bot)

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        member = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == USER_ID,
            )
        )
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == USER_ID,
                CurrencyHolding.nation_id == nation_id,
            )
        )
        assert user.xr_balance == Decimal("160.00")
        assert user.home_nation_id is None
        assert member.is_active is False
        assert holding.amount == Decimal("0.0000")


@pytest.mark.asyncio
async def test_dissolve_liquidates_all_active_member_holdings(monkeypatch):
    nation_id = await seed_nation(rate=Decimal("3.00"))
    bot = FakeBot()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(nm, "publish_nation_event_analysis", noop)
    monkeypatch.setattr(nm, "_notify_founder", noop)

    call = FakeCall(FOUNDER_ID, f"nm:confirm_dissolve:{nation_id}")
    await nm.confirm_dissolve(call, bot)

    async with async_session() as session:
        users = {
            row.user_id: row
            for row in (
                await session.execute(
                    select(User).where(User.user_id.in_([USER_ID, FOUNDER_ID]))
                )
            ).scalars().all()
        }
        holdings = (
            await session.execute(
                select(CurrencyHolding).where(CurrencyHolding.nation_id == nation_id)
            )
        ).scalars().all()
        members = (
            await session.execute(
                select(NationMember).where(NationMember.nation_id == nation_id)
            )
        ).scalars().all()
        nation = await session.get(Nation, nation_id)

        assert users[USER_ID].xr_balance == Decimal("310.00")
        assert users[FOUNDER_ID].xr_balance == Decimal("150.00")
        assert users[USER_ID].home_nation_id is None
        assert users[FOUNDER_ID].home_nation_id is None
        assert all(row.amount == Decimal("0.0000") for row in holdings)
        assert all(not row.is_active for row in members)
        assert nation.is_active is False
