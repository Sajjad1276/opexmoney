from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import secrets

import pytest
from sqlalchemy import delete, select

from app.database.models import Nation, PriceAlert, User
from app.database.session import async_session
from app.services.alert_service import (
    check_price_alerts,
    create_price_alert,
    delete_price_alert,
)


TEST_USER_ID = 930901
TEST_GROUP_ID = -100930901


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, user_id: int, text: str, **kwargs):
        self.messages.append((user_id, text))
        return SimpleNamespace()


@pytest.fixture(autouse=True)
async def cleanup_alert_rows():
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(PriceAlert).where(PriceAlert.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(User).where(User.user_id == TEST_USER_ID)
            )
            await session.execute(
                delete(Nation).where(Nation.group_id == TEST_GROUP_ID)
            )


@pytest.mark.asyncio
async def test_price_alert_triggers_and_can_be_deleted():
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=TEST_USER_ID,
                username="alerttester",
                balance=Decimal("500"),
                xr_balance=Decimal("100"),
                role="player",
            )
            nation = Nation(
                name="Alert Test",
                currency_code="ALT",
                group_id=TEST_GROUP_ID,
                founder_user_id=None,
                invite_code=secrets.token_urlsafe(8),
                exchange_rate=Decimal("1.5000"),
                rate_prev=Decimal("1.5000"),
                rate_24h_open=Decimal("1.0000"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            session.add_all([user, nation])
            await session.flush()

            alert = await create_price_alert(
                session,
                user_id=TEST_USER_ID,
                currency_code="ALT",
                target_price=Decimal("1.5000"),
                direction="above",
            )
            assert alert.triggered is False

    bot = FakeBot()
    async with async_session() as session:
        async with session.begin():
            triggered = await check_price_alerts(session, bot)
            assert triggered == 1

    assert len(bot.messages) == 1
    assert "ALT" in bot.messages[0][1]
    assert "1.5000" in bot.messages[0][1]

    async with async_session() as session:
        alert = await session.scalar(
            select(PriceAlert).where(PriceAlert.user_id == TEST_USER_ID)
        )
        assert alert is not None
        assert alert.triggered is True
        alert_id = alert.id

    async with async_session() as session:
        async with session.begin():
            deleted = await delete_price_alert(
                session,
                user_id=TEST_USER_ID,
                alert_id=alert_id,
            )
            assert deleted is True
