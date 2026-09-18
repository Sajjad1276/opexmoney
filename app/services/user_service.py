from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, User


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
      - complete: user exists and has at least one currency holding
      - partial: user exists but has no currency holding
      - new: no user row exists
    """
    user = await session.get(User, telegram_id)

    if user is None:
        return {"status": "new", "user": None, "missing": ["user"]}

    holding = await session.execute(
        select(CurrencyHolding).where(
            CurrencyHolding.user_id == telegram_id,
        )
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
