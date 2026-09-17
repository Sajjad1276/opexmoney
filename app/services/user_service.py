from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()


async def username_exists(session: AsyncSession, username: str, *, exclude_user_id: int | None = None) -> bool:
    stmt = select(User.user_id).where(func.lower(User.username) == username.lower())
    if exclude_user_id is not None:
        stmt = stmt.where(User.user_id != exclude_user_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None
