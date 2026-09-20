from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.database.models import CurrencyHolding, Nation, NationMember, NationTelegramMember, User
from app.database.session import async_session
from app.services.membership_service import sync_telegram_membership
from app.services.wallet_service import reconcile_user_wallet


USER_ID = 942001
GROUP_ID = -100942001


async def _cleanup() -> None:
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    select(Nation.nation_id).where(Nation.group_id == GROUP_ID)
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
                    User.__table__.update()
                    .where(
                        User.user_id == USER_ID,
                        User.home_nation_id.in_(nation_ids),
                    )
                    .values(home_nation_id=None)
                )
                await session.execute(
                    delete(Nation).where(Nation.nation_id.in_(nation_ids))
                )
            await session.execute(delete(User).where(User.user_id == USER_ID))


@pytest.mark.asyncio
async def test_wallet_reconciliation_derives_local_balance_from_holding():
    await _cleanup()
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=USER_ID,
                username="wallet_phase6",
                balance=Decimal("999"),
                xr_balance=Decimal("25"),
            )
            nation = Nation(
                group_id=GROUP_ID,
                name="Wallet Republic",
                currency_code="WLT",
                founder_user_id=USER_ID,
                invite_code="OPX-WALLET-942001",
                member_count=1,
                is_active=True,
            )
            session.add_all([user, nation])
            await session.flush()
            user.home_nation_id = nation.nation_id
            session.add(
                CurrencyHolding(
                    user_id=USER_ID,
                    nation_id=nation.nation_id,
                    amount=Decimal("125.50"),
                )
            )

    async with async_session() as session:
        async with session.begin():
            snapshot = await reconcile_user_wallet(session, USER_ID)

    assert snapshot.local_balance == Decimal("125.50")

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        assert user.balance == Decimal("125.50")
        assert user.xr_balance == Decimal("25.00")

    await _cleanup()


@pytest.mark.asyncio
async def test_telegram_join_keeps_user_balance_aligned_with_holding():
    await _cleanup()
    async with async_session() as session:
        async with session.begin():
            user = User(
                user_id=USER_ID,
                username="wallet_join_phase6",
                balance=Decimal("0"),
                xr_balance=Decimal("10"),
            )
            nation = Nation(
                group_id=GROUP_ID,
                name="Join Wallet Republic",
                currency_code="JWL",
                founder_user_id=None,
                invite_code="OPX-JOIN-942001",
                member_count=0,
                is_active=True,
            )
            session.add_all([user, nation])
            await session.flush()

    async with async_session() as session:
        async with session.begin():
            result = await sync_telegram_membership(
                session,
                group_id=GROUP_ID,
                telegram_user_id=USER_ID,
                telegram_status="member",
                is_member=True,
                source="phase6_test",
            )

    assert result is not None
    assert result.became_active is True

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == USER_ID,
                CurrencyHolding.nation_id == user.home_nation_id,
            )
        )
        assert holding is not None
        assert user.balance == holding.amount == Decimal("500.0000")

    await _cleanup()
