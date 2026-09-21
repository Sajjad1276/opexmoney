from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from admin.auth import get_admin_user
from admin.cache import TTL_NATION_DETAIL, TTL_NATIONS, cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import (
    NationDetail,
    NationListItem,
    NationMemberItem,
    NationRateHistoryPoint,
    NationWarItem,
    NationsPage,
    TreasuryLogItem,
)
from app.database.models import (
    Nation,
    NationMembership,
    NationWar,
    RateHistory,
    TreasuryLog,
    User,
)
from redis.asyncio import Redis

router = APIRouter(prefix="/api/nations", tags=["nations"])

SORT_FIELDS = {
    "member_count": Nation.member_count,
    "exchange_rate": Nation.exchange_rate,
    "treasury": Nation.treasury,
    "trade_volume_24h": Nation.trade_volume_24h,
    "created_at": Nation.created_at,
    "nation_rank": Nation.nation_rank,
}


@router.get("", response_model=NationsPage)
@cached(TTL_NATIONS, "nations")
async def list_nations(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="member_count"),
    order: str = Query(default="desc"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> NationsPage:
    total = int(
        await db.scalar(
            select(func.count(Nation.nation_id)).where(
                Nation.deleted_at.is_(None), Nation.is_active.is_(True)
            )
        )
        or 0
    )

    sort_expr = SORT_FIELDS.get(sort)
    if sort_expr is None:
        raise HTTPException(status_code=400, detail="Invalid sort field")
    sort_expr = sort_expr.asc() if order == "asc" else sort_expr.desc()

    rows = (
        await db.execute(
            select(Nation).where(Nation.deleted_at.is_(None), Nation.is_active.is_(True))
            .order_by(sort_expr, Nation.nation_id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars().all()

    items = []
    for nation in rows:
        opened = float(nation.rate_24h_open or 0)
        current = float(nation.exchange_rate or 0)
        change = ((current - opened) / opened * 100) if opened else 0.0
        at_war = bool(
            await db.scalar(
                select(NationWar.id)
                .where(
                    NationWar.status == "active",
                    or_(
                        NationWar.nation_id == nation.nation_id,
                        NationWar.opponent_nation_id == nation.nation_id,
                    ),
                )
                .limit(1)
            )
        )
        items.append(
            NationListItem(
                nation_id=nation.nation_id,
                name=nation.name,
                flag_emoji=nation.flag_emoji,
                currency_code=nation.currency_code,
                exchange_rate=nation.exchange_rate,
                rate_change_24h=change,
                member_count=nation.member_count,
                treasury=nation.treasury,
                trade_volume_24h=nation.trade_volume_24h,
                nation_rank=nation.nation_rank,
                is_active=nation.is_active,
                join_policy=nation.join_policy,
                is_ai=nation.is_ai,
                created_at=nation.created_at,
                active_members_24h=nation.active_members_24h,
                war_status="at_war" if at_war else "at_peace",
            )
        )
    return NationsPage(items=items, total=total, page=page, pages=page_count(total, limit))


@router.get("/{nation_id}", response_model=NationDetail)
@cached(TTL_NATION_DETAIL, "nation-detail")
async def nation_detail(
    nation_id: int,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> NationDetail:
    nation = await db.scalar(select(Nation).where(Nation.nation_id == nation_id))
    if nation is None:
        raise HTTPException(status_code=404, detail="Nation not found")

    rate_history = (
        await db.execute(
            select(RateHistory)
            .where(RateHistory.nation_id == nation_id)
            .order_by(RateHistory.calculated_at.desc())
            .limit(20)
        )
    ).scalars().all()
    top_members = (
        await db.execute(
            select(User, NationMembership.role)
            .join(NationMembership, NationMembership.user_id == User.user_id)
            .where(
                NationMembership.nation_id == nation_id,
                NationMembership.is_active.is_(True),
            )
            .order_by(User.balance.desc(), User.user_id.asc())
            .limit(5)
        )
    ).all()
    treasury_logs = (
        await db.execute(
            select(TreasuryLog, User.username)
            .outerjoin(User, User.user_id == TreasuryLog.actor_id)
            .where(TreasuryLog.nation_id == nation_id)
            .order_by(TreasuryLog.created_at.desc())
            .limit(10)
        )
    ).all()

    all_wars = (
        await db.execute(
            select(NationWar)
            .where(
                NationWar.status == "active",
                or_(
                    NationWar.nation_id == nation_id,
                    NationWar.opponent_nation_id == nation_id,
                ),
            )
            .order_by(NationWar.ends_at.asc())
        )
    ).scalars().all()

    active_wars: list[NationWarItem] = []
    for war in all_wars:
        opponent_id = war.opponent_nation_id if war.nation_id == nation_id else war.nation_id
        opponent = await db.scalar(select(Nation).where(Nation.nation_id == opponent_id))
        if opponent is None:
            continue
        active_wars.append(
            NationWarItem(
                id=war.id,
                status=war.status,
                nation_id=nation_id,
                nation_name=nation.name,
                nation_flag=nation.flag_emoji,
                nation_member_count=nation.member_count,
                nation_treasury=nation.treasury,
                opponent_nation_id=opponent.nation_id,
                opponent_name=opponent.name,
                opponent_flag=opponent.flag_emoji,
                opponent_member_count=opponent.member_count,
                opponent_treasury=opponent.treasury,
                declared_at=war.declared_at,
                ends_at=war.ends_at,
                ended_at=war.ended_at,
            )
        )

    return NationDetail(
        nation_id=nation.nation_id,
        group_id=nation.group_id,
        name=nation.name,
        flag_emoji=nation.flag_emoji,
        currency_code=nation.currency_code,
        founder_user_id=nation.founder_user_id,
        exchange_rate=nation.exchange_rate,
        rate_prev=nation.rate_prev,
        rate_24h_open=nation.rate_24h_open,
        trade_volume_24h=nation.trade_volume_24h,
        active_members_24h=nation.active_members_24h,
        nation_rank=nation.nation_rank,
        last_rate_update=nation.last_rate_update,
        member_count=nation.member_count,
        is_active=nation.is_active,
        join_policy=nation.join_policy,
        personality=nation.personality,
        is_ai=nation.is_ai,
        invite_code=nation.invite_code,
        treasury=nation.treasury,
        deleted_at=nation.deleted_at,
        created_at=nation.created_at,
        rate_history=[
            NationRateHistoryPoint(
                id=item.id,
                rate=item.rate,
                volume=item.volume,
                active_members=item.active_members,
                calculated_at=item.calculated_at,
                dominant_cause=item.dominant_cause,
                pressure_signal=item.pressure_signal,
                foreign_signal=item.foreign_signal,
                activity_score=item.activity_score,
                trade_score=item.trade_score,
                growth_score=item.growth_score,
            )
            for item in rate_history
        ],
        top_members=[
            NationMemberItem(
                user_id=user.user_id,
                username=user.username,
                balance=user.balance,
                xr_balance=user.xr_balance,
                role=role,
                is_ai=user.is_ai,
            )
            for user, role in top_members
        ],
        treasury_logs=[
            TreasuryLogItem(
                id=log.id,
                actor_id=log.actor_id,
                actor_username=username,
                action=log.action,
                amount_xr=log.amount_xr,
                note=log.note,
                created_at=log.created_at,
            )
            for log, username in treasury_logs
        ],
        active_wars=active_wars,
    )
