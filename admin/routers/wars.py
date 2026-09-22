from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import WarListItem
from app.database.models import Nation, NationWar, WarStatus
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wars", tags=["wars"])


async def _run_all(statement: Any) -> list[Any]:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.all()


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


def _enum_value(value: Any) -> str:
    return getattr(value, "value", str(value)) if value is not None else ""


@router.get("", response_model=list[WarListItem])
@cached(20, "wars")
async def list_wars(
    status: str = Query(default="active"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[WarListItem]:
    n1 = aliased(Nation)
    n2 = aliased(Nation)

    stmt = (
        select(
            NationWar.id,
            NationWar.status,
            NationWar.declared_at,
            NationWar.ends_at,
            NationWar.ended_at,
            n1.name,
            n1.flag_emoji,
            n1.member_count,
            n2.name,
            n2.flag_emoji,
            n2.member_count,
        )
        .join(n1, NationWar.nation_id == n1.nation_id)
        .join(n2, NationWar.opponent_nation_id == n2.nation_id)
        .order_by(NationWar.declared_at.desc(), NationWar.id.desc())
        .limit(100)
    )

    normalized_status = status.lower()
    if normalized_status in {"active", "ended", "draw"}:
        stmt = stmt.where(NationWar.status == normalized_status)

    rows = await _run_all(stmt)
    return [
        WarListItem(
            war_id=int(row[0]),
            nation_name=row[5] or "",
            nation_flag=row[6],
            opponent_name=row[8] or "",
            opponent_flag=row[9],
            status=_enum_value(row[1]),
            declared_at=_iso(row[2]),
            ends_at=_iso(row[3]),
            ended_at=_iso(row[4]) if row[4] is not None else None,
            nation_member_count=int(row[7] or 0),
            opponent_member_count=int(row[10] or 0),
        )
        for row in rows
    ]


# ── END OF wars.py ──
