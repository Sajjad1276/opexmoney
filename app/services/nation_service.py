from sqlalchemy import select, desc
from app.database.models import Nation

async def get_active_nations(session, limit=3):
    result = await session.execute(
        select(Nation).order_by(desc(Nation.member_count)).limit(limit)
    )
    return result.scalars().all()
