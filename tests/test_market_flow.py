from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import secrets

import pytest
from sqlalchemy import delete, select

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationMember,
    NationMemberRole,
    TradePreview,
    Transaction,
    User,
    UserActivity,
)
from app.database.session import async_session
from app.services.market.market_service import get_market_page_data
from app.handlers.market import (
    buy_currency,
    market_button,
    buy_amount_message,
    confirm_buy,
    market_sell,
    sell_currency,
    sell_quick,
    confirm_sell,
)
from app.services.temporal_service import ensure_temporal_profile


TEST_USER_ID = 930001
TEST_GROUP_HOME = -100930001
TEST_GROUP_TARGET = -100930002


class FakeBot:
    async def delete_message(self, *, chat_id: int, message_id: int):
        return None


class FakeMessage:
    def __init__(self, user_id: int, text: str | None = None):
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Health Tester",
            username="healthtester",
        )
        self.text = text
        self.message_id = 100
        self.chat = SimpleNamespace(id=user_id)
        self.bot = FakeBot()
        self.edits: list[str] = []
        self.answers: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self


class FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state: str | None = None

    async def clear(self):
        self.data.clear()
        self.state = None

    async def set_state(self, state):
        self.state = getattr(state, "state", state)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)


class FakeCall:
    def __init__(self, user_id: int, data: str, message: FakeMessage):
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Health Tester",
            username="healthtester",
        )
        self.data = data
        self.message = message
        self.answers: list[tuple[object, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


@pytest.fixture(autouse=True)
async def cleanup_market_health_rows():
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(TradePreview).where(TradePreview.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(Transaction).where(Transaction.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(UserActivity).where(UserActivity.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(NationMember).where(NationMember.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(CurrencyHolding).where(CurrencyHolding.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(User).where(User.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(Nation).where(
                    Nation.group_id.in_({TEST_GROUP_HOME, TEST_GROUP_TARGET})
                )
            )


@pytest.mark.asyncio
async def test_market_opens_for_registered_user_without_nation():
    async with async_session() as session:
        async with session.begin():
            session.add(
                User(
                    user_id=TEST_USER_ID,
                    username="healthtester",
                    balance=Decimal("0"),
                    xr_balance=Decimal("500"),
                    role="player",
                    home_nation_id=None,
                )
            )

    data = await get_market_page_data(TEST_USER_ID)

    assert data.user is not None
    assert data.user.home_nation_id is None

    message = FakeMessage(TEST_USER_ID)
    await market_button(message)
    assert message.answers
    assert all("حساب پیدا نشد" not in answer for answer in message.answers)


@pytest.mark.asyncio
async def test_market_repairs_missing_home_nation_from_active_membership():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=TEST_USER_ID,
                username="healthtester",
                balance=Decimal("0"),
                xr_balance=Decimal("1000"),
                role="player",
                home_nation_id=None,
            )
            nation = Nation(
                name="Health Home",
                currency_code="HOM",
                group_id=TEST_GROUP_HOME,
                founder_user_id=None,
                invite_code=secrets.token_urlsafe(8),
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            session.add_all([user, nation])
            await session.flush()
            session.add(
                NationMember(
                    nation_id=nation.nation_id,
                    user_id=TEST_USER_ID,
                    role=NationMemberRole.CITIZEN,
                    is_active=True,
                )
            )

    data = await get_market_page_data(TEST_USER_ID)

    assert data.user is not None
    assert data.user.home_nation_id is not None
    assert data.user.home_nation_id == nation.nation_id

    message = FakeMessage(TEST_USER_ID)
    await market_button(message)
    assert message.answers
    assert all("حساب پیدا نشد" not in answer for answer in message.answers)


@pytest.mark.asyncio
async def test_complete_buy_then_sell_button_journey():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=TEST_USER_ID,
                username="healthtester",
                balance=Decimal("0"),
                xr_balance=Decimal("1000"),
                role="player",
            )
            home = Nation(
                name="Health Home",
                currency_code="HOM",
                group_id=TEST_GROUP_HOME,
                founder_user_id=None,
                invite_code=secrets.token_urlsafe(8),
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            target = Nation(
                name="Health Target",
                currency_code="HET",
                group_id=TEST_GROUP_TARGET,
                founder_user_id=None,
                invite_code=secrets.token_urlsafe(8),
                exchange_rate=Decimal("2"),
                rate_prev=Decimal("2"),
                rate_24h_open=Decimal("2"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            session.add_all([user, home, target])
            await session.flush()
            user.home_nation_id = home.nation_id
            await ensure_temporal_profile(session, TEST_USER_ID)

    state = FakeState()
    message = FakeMessage(TEST_USER_ID)

    # market_buy -> buy_{nation_id} -> amount message -> confirmation.
    buy_call = FakeCall(TEST_USER_ID, f"buy_{target.nation_id}", message)
    await buy_currency(buy_call, state)
    assert state.data["nation_id"] == target.nation_id
    assert state.state is not None

    amount_message = FakeMessage(TEST_USER_ID, text="100")
    await buy_amount_message(amount_message, state)
    assert amount_message.answers, "buy amount must render a confirmation preview"

    async with async_session() as session:
        preview = await session.scalar(
            select(TradePreview).where(
                TradePreview.user_id == TEST_USER_ID,
                TradePreview.side == "buy",
            )
        )
        assert preview is not None
        assert preview.spend == Decimal("100")

    confirm_call = FakeCall(
        TEST_USER_ID,
        f"cbuy_{target.nation_id}_100",
        message,
    )
    await confirm_buy(confirm_call)

    async with async_session() as session:
        user = await session.get(User, TEST_USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == TEST_USER_ID,
                CurrencyHolding.nation_id == target.nation_id,
            )
        )
        tx = await session.scalar(
            select(Transaction)
            .where(
                Transaction.user_id == TEST_USER_ID,
                Transaction.nation_id == target.nation_id,
                Transaction.transaction_type == "buy",
            )
            .order_by(Transaction.id.desc())
        )
        remaining_preview = await session.scalar(
            select(TradePreview).where(
                TradePreview.user_id == TEST_USER_ID,
                TradePreview.side == "buy",
            )
        )
        assert user.xr_balance < Decimal("1000")
        assert holding is not None and holding.amount > 0
        assert tx is not None
        assert remaining_preview is None

    # market_sell -> sell_{currency} -> quick 50% -> confirmation.
    sell_menu_call = FakeCall(TEST_USER_ID, "market_sell", message)
    await market_sell(sell_menu_call, state)

    sell_call = FakeCall(TEST_USER_ID, "sell_HET", message)
    await sell_currency(sell_call, state)
    assert state.data["nation_id"] == target.nation_id

    quick_sell = FakeCall(TEST_USER_ID, "sellq_50_HET", message)
    await sell_quick(quick_sell, state)
    assert message.answers, "sell quick action must render a confirmation preview"

    async with async_session() as session:
        preview = await session.scalar(
            select(TradePreview).where(
                TradePreview.user_id == TEST_USER_ID,
                TradePreview.side == "sell",
            )
        )
        assert preview is not None
        assert preview.spend > 0

    amount = preview.spend
    confirm_sell_call = FakeCall(
        TEST_USER_ID,
        f"csell_{target.nation_id}_{amount}",
        message,
    )
    await confirm_sell(confirm_sell_call)

    async with async_session() as session:
        user = await session.get(User, TEST_USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == TEST_USER_ID,
                CurrencyHolding.nation_id == target.nation_id,
            )
        )
        sell_tx = await session.scalar(
            select(Transaction)
            .where(
                Transaction.user_id == TEST_USER_ID,
                Transaction.nation_id == target.nation_id,
                Transaction.transaction_type == "sell",
            )
            .order_by(Transaction.id.desc())
        )
        remaining_preview = await session.scalar(
            select(TradePreview).where(
                TradePreview.user_id == TEST_USER_ID,
                TradePreview.side == "sell",
            )
        )
        assert user.xr_balance > Decimal("900")
        assert holding is not None and holding.amount >= 0
        assert sell_tx is not None
        assert remaining_preview is None
        assert confirm_sell_call.answers
