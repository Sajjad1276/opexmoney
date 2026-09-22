from __future__ import annotations

from decimal import Decimal
import inspect
import secrets

import pytest
from sqlalchemy import delete, select

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.services import world_action_service
from app.services.ai_world import _execute_buy as ai_execute_buy
from app.services.market.trade_service import execute_buy, execute_sell


TEST_USER_ID = 941001
TEST_NATION_GROUP = -100941001


@pytest.fixture(autouse=True)
async def cleanup_world_action_rows():
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(UserActivity).where(UserActivity.user_id == TEST_USER_ID))
            await session.execute(delete(Transaction).where(Transaction.user_id == TEST_USER_ID))
            await session.execute(
                delete(CurrencyHolding).where(CurrencyHolding.user_id == TEST_USER_ID)
            )
            await session.execute(delete(User).where(User.user_id == TEST_USER_ID))
            await session.execute(
                delete(Nation).where(Nation.group_id == TEST_NATION_GROUP)
            )


@pytest.mark.asyncio
async def test_canonical_trade_action_updates_all_world_ledgers(monkeypatch):
    async def fake_resolve(*args, **kwargs):
        return Decimal("0")

    async def fake_peak(*args, **kwargs):
        return Decimal("1")

    async def fake_pressure(**kwargs):
        return None

    monkeypatch.setattr(world_action_service, "resolve", fake_resolve)
    monkeypatch.setattr(world_action_service, "get_peak_multiplier", fake_peak)
    monkeypatch.setattr(world_action_service, "record_trade_pressure", fake_pressure)

    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=TEST_USER_ID,
                username="worldactiontester",
                balance=Decimal("100"),
                xr_balance=Decimal("1000"),
                role="player",
            )
            nation = Nation(
                name="World Action Nation",
                currency_code="WAX",
                group_id=TEST_NATION_GROUP,
                invite_code=f"world-action-{secrets.token_urlsafe(6)}",
                exchange_rate=Decimal("2"),
                rate_prev=Decimal("2"),
                rate_24h_open=Decimal("2"),
                trade_volume_24h=Decimal("0"),
                member_count=1,
                is_active=True,
            )
            session.add_all([user, nation])
            await session.flush()
            user.home_nation_id = nation.nation_id
            session.add(
                CurrencyHolding(
                    user_id=TEST_USER_ID,
                    nation_id=nation.nation_id,
                    amount=Decimal("100"),
                )
            )
            await session.flush()

            bought = await world_action_service.execute_trade_action(
                session,
                user_id=TEST_USER_ID,
                nation_id=nation.nation_id,
                side="buy",
                amount=Decimal("100"),
            )
            sold = await world_action_service.execute_trade_action(
                session,
                user_id=TEST_USER_ID,
                nation_id=nation.nation_id,
                side="sell",
                amount=Decimal("10"),
            )

            assert bought.side == "buy"
            assert sold.side == "sell"
            assert bought.receive == Decimal("50")
            assert sold.receive == Decimal("20")

            holding = await session.scalar(
                select(CurrencyHolding).where(
                    CurrencyHolding.user_id == TEST_USER_ID,
                    CurrencyHolding.nation_id == nation.nation_id,
                )
            )
            assert holding is not None
            assert holding.amount == Decimal("140")
            assert user.xr_balance == Decimal("920")
            assert user.balance == Decimal("140")
            assert nation.trade_volume_24h == Decimal("120")

            transactions = (
                await session.execute(
                    select(Transaction)
                    .where(Transaction.user_id == TEST_USER_ID)
                    .order_by(Transaction.id.asc())
                )
            ).scalars().all()
            assert [row.transaction_type for row in transactions] == ["buy", "sell"]
            assert [row.spend_xr for row in transactions] == [
                Decimal("100"),
                Decimal("20"),
            ]

            activities = (
                await session.execute(
                    select(UserActivity)
                    .where(UserActivity.user_id == TEST_USER_ID)
                )
            ).scalars().all()
            assert len(activities) == 2


def test_human_and_ai_trade_paths_use_the_same_canonical_mutation():
    human_source = inspect.getsource(execute_buy)
    ai_source = inspect.getsource(ai_execute_buy)

    assert "execute_trade_action" in human_source
    assert "execute_trade_action" in ai_source
