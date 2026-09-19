from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, User


async def sync_user_balance(session: AsyncSession, user_id: int) -> Decimal:
    """
    Synchronize User.balance with the user's home-nation CurrencyHolding.

    XR is a separate liquid cash ledger with no CurrencyHolding source, so it
    is preserved and normalized rather than derived from currency holdings.
    """
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    new_balance = Decimal("0")
    if user.home_nation_id is not None:
        holding_amount = await session.scalar(
            select(CurrencyHolding.amount).where(
                CurrencyHolding.user_id == user_id,
                CurrencyHolding.nation_id == user.home_nation_id,
            )
        )
        if holding_amount is not None:
            new_balance = Decimal(str(holding_amount))

    user.balance = new_balance
    user.xr_balance = Decimal(str(user.xr_balance or Decimal("0")))
    return new_balance


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()


async def get_registration_status(
    session: AsyncSession,
    telegram_id: int,
) -> dict:
    """
    Return the persistent registration status.

    status:
      - complete: user exists, has a non-empty username, and has at least one currency holding
      - partial: user exists but is missing a required registration field
      - new: no user row exists
    """
    user = await session.get(User, telegram_id)

    if user is None:
        return {"status": "new", "user": None, "missing": ["user"]}

    if not (user.username or "").strip():
        return {"status": "partial", "user": user, "missing": ["username", "holding"]}

    holding = await session.execute(
        select(CurrencyHolding.id)
        .where(CurrencyHolding.user_id == telegram_id)
        .limit(1)
    )
    if holding.scalar_one_or_none() is None:
        return {"status": "partial", "user": user, "missing": ["holding"]}

    return {"status": "complete", "user": user, "missing": []}


async def username_exists(
    session: AsyncSession,
    username: str,
    *,
    exclude_user_id: int | None = None,
) -> bool:
    stmt = select(User.user_id).where(func.lower(User.username) == username.lower())
    if exclude_user_id is not None:
        stmt = stmt.where(User.user_id != exclude_user_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def is_fully_registered(
    session: AsyncSession,
    telegram_id: int,
) -> bool:
    """
    Return True only when the persistent registration is complete.

    Required conditions:
    1. User exists.
    2. User has a non-empty username.
    3. User has at least one currency holding.
    """
    user = await session.get(User, telegram_id)
    if not user or not (user.username or "").strip():
        return False

    result = await session.execute(
        select(CurrencyHolding)
        .where(CurrencyHolding.user_id == telegram_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def is_user_registered(
    session: AsyncSession,
    telegram_id: int,
) -> bool:
    """Backward-compatible alias for the persistent full-registration check."""
    return await is_fully_registered(session, telegram_id)
