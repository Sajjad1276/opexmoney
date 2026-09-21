from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from admin.auth import get_admin_user
from admin.cache import TTL_PLAYER_DETAIL, TTL_PLAYERS, cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import (
    HomeNationDetails,
    PlayerAIUsage,
    PlayerMissionSummary,
    PlayerProfile,
    PlayerTransaction,
    PlayerWarSummary,
    PlayerXP,
    PlayersPage,
    PlayerListItem,
)
from app.database.models import (
    AIUsageLog,
    BehaviorSnapshot,
    Mission,
    Nation,
    NationWar,
    Transaction,
    User,
    UserActivity,
    UserMissionProgress,
    UserXP,
)
from redis.asyncio import Redis

router = APIRouter(prefix="/api/players", tags=["players"])


SORT_FIELDS = {
    "balance": User.balance,
    "xr_balance": User.xr_balance,
    "created_at": User.created_at,
}


def _safe_order(value: str) -> str:
    return value if value in {"asc", "desc"} else "desc"


@router.get("", response_model=PlayersPage)
@cached(TTL_PLAYERS, "players")
async def list_players(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="balance"),
    order: str = Query(default="desc"),
    search: str = Query(default=""),
    filter: str = Query(default="active"),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> PlayersPage:
    tx_count = (
        select(func.count(Transaction.id))
        .where(Transaction.user_id == User.user_id)
        .correlate(User)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(UserActivity.created_at))
        .where(UserActivity.user_id == User.user_id)
        .correlate(User)
        .scalar_subquery()
    )

    conditions = []
    if filter == "active":
        conditions.append(User.deleted_at.is_(None))
    elif filter == "banned":
        conditions.append(User.deleted_at.is_not(None))
    elif filter == "ai":
        conditions.append(User.is_ai.is_(True))
    else:
        raise HTTPException(status_code=400, detail="Invalid filter")

    if search:
        conditions.append(User.username.ilike(f"%{search}%"))

    total = int(await db.scalar(select(func.count(User.user_id)).where(*conditions)) or 0)

    sort_expr = SORT_FIELDS.get(sort)
    if sort == "total_transactions":
        sort_expr = tx_count
    if sort_expr is None:
        raise HTTPException(status_code=400, detail="Invalid sort field")

    sort_expr = sort_expr.asc() if _safe_order(order) == "asc" else sort_expr.desc()

    stmt = (
        select(User, Nation.name, Nation.flag_emoji, tx_count.label("tx_count"), last_activity.label("last_activity"))
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .where(*conditions)
        .order_by(sort_expr, User.user_id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    items = [
        PlayerListItem(
            user_id=user.user_id,
            username=user.username,
            balance=user.balance,
            xr_balance=user.xr_balance,
            role=user.role,
            ai_tier=user.ai_tier,
            home_nation_name=nation_name,
            home_nation_flag=flag,
            created_at=user.created_at,
            is_ai=user.is_ai,
            total_transactions=int(tx_count_value or 0),
            last_activity_at=last_activity_value,
        )
        for user, nation_name, flag, tx_count_value, last_activity_value in rows
    ]
    return PlayersPage(items=items, total=total, page=page, pages=page_count(total, limit))


@router.get("/{user_id}", response_model=PlayerProfile)
@cached(TTL_PLAYER_DETAIL, "player-detail")
async def player_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> PlayerProfile:
    user = await db.scalar(select(User).where(User.user_id == user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Player not found")

    nation = None
    if user.home_nation_id is not None:
        nation = await db.scalar(select(Nation).where(Nation.nation_id == user.home_nation_id))

    tx_rows = (
        await db.execute(
            select(Transaction, Nation.name)
            .outerjoin(Nation, Nation.nation_id == Transaction.nation_id)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
            .limit(10)
        )
    ).all()
    last_transactions = [
        PlayerTransaction(
            id=tx.id,
            nation_id=tx.nation_id,
            nation_name=nation_name,
            transaction_type=tx.transaction_type,
            spend_xr=tx.spend_xr,
            amount=tx.amount,
            fee_xr=tx.fee_xr,
            rate=tx.rate,
            created_at=tx.created_at,
        )
        for tx, nation_name in tx_rows
    ]

    mission_completion_count = int(
        await db.scalar(
            select(func.count(UserMissionProgress.id)).where(
                UserMissionProgress.user_id == user_id,
                UserMissionProgress.completed.is_(True),
            )
        )
        or 0
    )

    current_war = None
    if user.home_nation_id is not None:
        war_row = (
            await db.execute(
                select(NationWar, Nation.name, Nation.flag_emoji)
                .join(
                    Nation,
                    Nation.nation_id
                    == case(
                        (NationWar.nation_id == user.home_nation_id, NationWar.opponent_nation_id),
                        else_=NationWar.nation_id,
                    ),
                )
                .where(
                    NationWar.status == "active",
                    or_(
                        NationWar.nation_id == user.home_nation_id,
                        NationWar.opponent_nation_id == user.home_nation_id,
                    ),
                )
                .order_by(NationWar.ends_at.asc())
                .limit(1)
            )
        ).first()
        if war_row:
            war, opponent_name, opponent_flag = war_row
            current_war = PlayerWarSummary(
                war_id=war.id,
                status=war.status,
                nation_id=user.home_nation_id,
                opponent_nation_id=war.opponent_nation_id if war.nation_id == user.home_nation_id else war.nation_id,
                opponent_name=opponent_name,
                opponent_flag=opponent_flag,
                ends_at=war.ends_at,
            )

    today = datetime.now(timezone.utc).date()
    usage = await db.scalar(
        select(AIUsageLog).where(AIUsageLog.user_id == user_id, AIUsageLog.date == today)
    )
    xp = await db.scalar(select(UserXP).where(UserXP.user_id == user_id))

    home_nation = None
    if nation is not None:
        home_nation = HomeNationDetails(
            nation_id=nation.nation_id,
            name=nation.name,
            flag_emoji=nation.flag_emoji,
            currency_code=nation.currency_code,
            exchange_rate=nation.exchange_rate,
            member_count=nation.member_count,
            is_active=nation.is_active,
            is_ai=nation.is_ai,
        )

    return PlayerProfile(
        user_id=user.user_id,
        username=user.username,
        home_nation_id=user.home_nation_id,
        balance=user.balance,
        xr_balance=user.xr_balance,
        role=user.role,
        is_ai=user.is_ai,
        ai_strategy=user.ai_strategy,
        ai_tier=user.ai_tier,
        deleted_at=user.deleted_at,
        created_at=user.created_at,
        home_nation=home_nation,
        last_transactions=last_transactions,
        mission_completion_count=mission_completion_count,
        current_war=current_war,
        ai_usage_today=(
            PlayerAIUsage(
                date=usage.date,
                advisor_questions_used=usage.advisor_questions_used,
                portfolio_scans_used=usage.portfolio_scans_used,
                war_analysis_used=usage.war_analysis_used,
            )
            if usage
            else None
        ),
        total_xp=xp.total_xp if xp else 0,
        level=xp.level if xp else "beginner",
    )
