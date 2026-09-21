from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from admin.auth import get_admin_user
from admin.cache import TTL_WARS, cached
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import WarListItem
from app.database.models import Nation, NationWar
from redis.asyncio import Redis

router = APIRouter(prefix="/api/wars", tags=["wars"])


@router.get("", response_model=list[WarListItem])
@cached(TTL_WARS, "wars")
async def list_wars(
    status: str = Query(default="active"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[WarListItem]:
    if status not in {"active", "ended", "draw", "all"}:
        raise HTTPException(status_code=400, detail="Invalid war status")

    left_nation = aliased(Nation)
    right_nation = aliased(Nation)
    stmt = (
        select(NationWar, left_nation, right_nation)
        .join(left_nation, left_nation.nation_id == NationWar.nation_id)
        .join(right_nation, right_nation.nation_id == NationWar.opponent_nation_id)
    )
    if status != "all":
        stmt = stmt.where(NationWar.status == status)
    stmt = stmt.order_by(NationWar.declared_at.desc())

    rows = (await db.execute(stmt)).all()
    return [
        WarListItem(
            id=war.id,
            status=war.status,
            nation_id=left.nation_id,
            nation_name=left.name,
            nation_flag=left.flag_emoji,
            nation_member_count=left.member_count,
            nation_treasury=left.treasury,
            opponent_nation_id=right.nation_id,
            opponent_name=right.name,
            opponent_flag=right.flag_emoji,
            opponent_member_count=right.member_count,
            opponent_treasury=right.treasury,
            declared_at=war.declared_at,
            ends_at=war.ends_at,
            ended_at=war.ended_at,
        )
        for war, left, right in rows
    ]
