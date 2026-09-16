from sqlalchemy import select
from app.database.models import User

async def get_user(session, user_id: int):
    result = await session.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()
