from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

import app.handlers.start as start_module
from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.states.onboarding import OnboardingStates


USER_ID = 932001
GROUP_ID = -100932001


class FakeState:
    def __init__(self, state_value, data=None):
        self.state = state_value
        self.data = dict(data or {})

    async def get_state(self):
        return self.state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def clear(self):
        self.state = None
        self.data.clear()

    async def set_state(self, state):
        self.state = getattr(state, "state", state)


class FakeMessage:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self


class FakeBot:
    async def send_message(self, *args, **kwargs):
        return SimpleNamespace()


class FakeCall:
    def __init__(self, user_id: int, data: str):
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Onboarding Tester",
            username="onboarding_tester",
        )
        self.data = data
        self.message = FakeMessage()
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
            await session.execute(delete(Transaction).where(Transaction.user_id == USER_ID))
            await session.execute(delete(UserActivity).where(UserActivity.user_id == USER_ID))
            await session.execute(delete(CurrencyHolding).where(CurrencyHolding.user_id == USER_ID))
            await session.execute(delete(User).where(User.user_id == USER_ID))
            if nation_ids:
                await session.execute(delete(Nation).where(Nation.nation_id.in_(nation_ids)))


@pytest.mark.asyncio
async def test_registration_to_first_trade_journey():
    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name="Onboarding Republic",
                currency_code="ONB",
                group_id=GROUP_ID,
                founder_user_id=None,
                exchange_rate=Decimal("2"),
                rate_prev=Decimal("2"),
                rate_24h_open=Decimal("2"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-ONBOARDING",
                treasury=Decimal("0"),
            )
            session.add(nation)
            await session.flush()
            nation_id = nation.nation_id

    state = FakeState(
        OnboardingStates.SELECT_NATION.state,
        {
            "username": "OnboardingTester",
            "onboarding_started_at": datetime.now().timestamp(),
        },
    )
    call = FakeCall(USER_ID, f"confirm_nation:{nation_id}")
    bot = FakeBot()

    await start_module.confirm_nation(call, state, bot)

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == USER_ID,
                CurrencyHolding.nation_id == nation_id,
            )
        )
        nation = await session.get(Nation, nation_id)

        assert user is not None, f"confirm_nation returned early: {call.answers!r}"
        assert user.username == "OnboardingTester"
        assert user.home_nation_id == nation_id
        assert user.balance == Decimal("500")
        assert user.xr_balance == Decimal("0")
        assert holding.amount == Decimal("500")
        assert nation.member_count == 2

    assert state.data["first_trade_available"] is True
    assert call.answers
    assert call.message.edits

    tutorial = FakeCall(USER_ID, "first_trade_tutorial")
    await start_module.first_trade_tutorial(tutorial, state)
    assert tutorial.message.edits

    confirm = FakeCall(USER_ID, "confirm_first_trade")
    await start_module.confirm_first_trade(confirm, state)

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == USER_ID,
                CurrencyHolding.nation_id == nation_id,
            )
        )
        trade = await session.scalar(
            select(Transaction)
            .where(
                Transaction.user_id == USER_ID,
                Transaction.nation_id == nation_id,
                Transaction.transaction_type == "sell",
            )
            .order_by(Transaction.id.desc())
        )
        assert user.xr_balance == Decimal("100")
        assert holding.amount == Decimal("450")
        assert trade is not None
        assert trade.amount == Decimal("50")

    assert state.state is None
    assert confirm.message.edits
