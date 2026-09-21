from decimal import Decimal
import secrets

import pytest
from sqlalchemy import delete, select

from app.database.models import CurrencyHolding, Nation, User, UserActivity
from app.database.session import async_session
from app.services.nation_service import create_nation
from app.services.user_service import is_user_registered


@pytest.mark.asyncio
async def test_registration_does_not_require_currency_holding():
    async with async_session() as session:
        async with session.begin():
            session.add(
                User(
                    user_id=910001,
                    username="testuser1",
                    home_nation_id=None,
                    balance=Decimal("0"),
                    xr_balance=Decimal("500"),
                )
            )

    async with async_session() as session:
        assert await is_user_registered(session, 910001) is True

    async with async_session() as session:
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == 910001,
            )
        )
        assert holding is None


@pytest.mark.asyncio
async def test_nation_name_is_english_and_currency_is_generated_uniquely():
    from app.utils.validators import generate_unique_currency_code, validate_nation_name

    assert validate_nation_name("New Empire")[0] is True
    assert validate_nation_name("New-Empire")[0] is False
    assert validate_nation_name("Persian Empire!")[0] is False
    assert validate_nation_name("OPEX")[0] is False

    async with async_session() as session:
        async with session.begin():
            session.add(Nation(name="Origin Currency", currency_code="NEW", group_id=-100910010, invite_code=secrets.token_urlsafe(8)))
            await session.flush()
            code = await generate_unique_currency_code("New Empire", session)
            assert code != "NEW"
            assert len(code) == 3
            assert code.isalpha() and code.isupper()


@pytest.mark.asyncio
async def test_create_nation_is_atomic_and_initializes_founder():
    async with async_session() as session:
        async with session.begin():
            user = User(user_id=910002, username="testuser2")
            origin = Nation(name="Origin2", currency_code="OR2", group_id=-100910002, invite_code=secrets.token_urlsafe(8))
            session.add_all([user, origin])
            await session.flush()
            session.add(CurrencyHolding(user_id=user.user_id, nation_id=origin.nation_id, amount=Decimal("500")))

    async with async_session() as session:
        nation = await create_nation(
            session=session,
            founder_user_id=910002,
            group_id=-100910003,
            nation_name="Test Nation",
            currency_code="TS2",
        )

    async with async_session() as session:
        user = await session.get(User, 910002)
        holding = await session.scalar(
            select(CurrencyHolding)
            .where(CurrencyHolding.user_id == 910002, CurrencyHolding.nation_id == nation.nation_id)
        )
        assert user.role == "founder"
        assert user.home_nation_id == nation.nation_id
        assert user.xr_balance == Decimal("1000.00")
        assert holding.amount == Decimal("1000.0000")


@pytest.mark.asyncio
async def test_create_nation_works_before_first_currency_holding():
    async with async_session() as session:
        async with session.begin():
            session.add(User(user_id=910006, username="testuser6"))

    async with async_session() as session:
        nation = await create_nation(
            session=session,
            founder_user_id=910006,
            group_id=-100910009,
            nation_name="First Kingdom",
            currency_code="KIN",
        )

    async with async_session() as session:
        user = await session.get(User, 910006)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == 910006,
                CurrencyHolding.nation_id == nation.nation_id,
            )
        )
        assert user.role == "founder"
        assert user.home_nation_id == nation.nation_id
        assert holding.amount == Decimal("1000.0000")


@pytest.mark.asyncio
async def test_founder_constraints_reject_duplicate_group_and_currency():
    async with async_session() as session:
        async with session.begin():
            user = User(user_id=910003, username="testuser3")
            origin = Nation(name="Origin3", currency_code="OR3", group_id=-100910004, invite_code=secrets.token_urlsafe(8))
            session.add_all([user, origin])
            await session.flush()
            session.add(CurrencyHolding(user_id=user.user_id, nation_id=origin.nation_id, amount=Decimal("500")))

    async with async_session() as session:
        await create_nation(
            session=session,
            founder_user_id=910003,
            group_id=-100910005,
            nation_name="First Nation",
            currency_code="DUP",
        )

    async with async_session() as session:
        async with session.begin():
            user = User(user_id=910004, username="testuser4")
            origin = Nation(name="Origin4", currency_code="OR4", group_id=-100910006, invite_code=secrets.token_urlsafe(8))
            session.add_all([user, origin])
            await session.flush()
            session.add(CurrencyHolding(user_id=user.user_id, nation_id=origin.nation_id, amount=Decimal("500")))

    async with async_session() as session:
        with pytest.raises(ValueError):
            await create_nation(
                session=session,
                founder_user_id=910004,
                group_id=-100910005,
                nation_name="Second Nation",
                currency_code="DUP2",
            )

    async with async_session() as session:
        async with session.begin():
            user = User(user_id=910005, username="testuser5")
            origin = Nation(name="Origin5", currency_code="OR5", group_id=-100910007, invite_code=secrets.token_urlsafe(8))
            session.add_all([user, origin])
            await session.flush()
            session.add(CurrencyHolding(user_id=user.user_id, nation_id=origin.nation_id, amount=Decimal("500")))

    async with async_session() as session:
        with pytest.raises(ValueError):
            await create_nation(
                session=session,
                founder_user_id=910005,
                group_id=-100910008,
                nation_name="Third Nation",
                currency_code="DUP",
            )


@pytest.fixture(autouse=True)
async def cleanup_test_rows():
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(CurrencyHolding).where(CurrencyHolding.user_id >= 910001, CurrencyHolding.user_id <= 910006))
            await session.execute(delete(UserActivity).where(UserActivity.user_id >= 910001, UserActivity.user_id <= 910006))
            await session.execute(delete(User).where(User.user_id >= 910001, User.user_id <= 910006))
            await session.execute(delete(Nation).where(Nation.group_id >= -100910010, Nation.group_id <= -100910001))
