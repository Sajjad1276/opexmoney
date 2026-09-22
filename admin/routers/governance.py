from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.cache import cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import (
    GovernanceLedgerItem,
    PaginatedResponse,
    ProposalListItem,
    RuleOverrideItem,
)
from app.database.models import GovernanceLedger, Proposal, RuleOverride, User, Vote
from app.database.session import async_session
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/governance", tags=["governance"])


async def _run_all(statement: Any) -> list[Any]:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.all()


async def _run_scalar(statement: Any) -> Any:
    async with async_session() as session:
        result = await session.execute(statement)
        return result.scalar_one_or_none()


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


@router.get("/proposals", response_model=PaginatedResponse[ProposalListItem])
@cached(30, "governance:proposals")
async def proposals(
    status: str = Query(default="voting"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PaginatedResponse[ProposalListItem]:
    conditions: list[Any] = []
    if status.lower() != "all":
        conditions.append(Proposal.status == status)

    count_stmt = select(func.count(Proposal.id)).where(*conditions)

    yes_count = func.coalesce(
        func.sum(case((Vote.choice == "yes", 1), else_=0)),
        0,
    )
    no_count = func.coalesce(
        func.sum(case((Vote.choice == "no", 1), else_=0)),
        0,
    )
    abstain_count = func.coalesce(
        func.sum(case((Vote.choice == "abstain", 1), else_=0)),
        0,
    )
    total_weight = func.coalesce(func.sum(Vote.weight), 0)

    data_stmt = (
        select(
            Proposal.id,
            User.username,
            Proposal.rule_key,
            Proposal.proposed_value,
            Proposal.status,
            Proposal.voting_opens_at,
            Proposal.voting_closes_at,
            yes_count.label("yes_count"),
            no_count.label("no_count"),
            abstain_count.label("abstain_count"),
            total_weight.label("total_weight"),
        )
        .join(User, User.user_id == Proposal.proposer_player_id)
        .outerjoin(Vote, Vote.proposal_id == Proposal.id)
        .where(*conditions)
        .group_by(
            Proposal.id,
            User.user_id,
            User.username,
            Proposal.rule_key,
            Proposal.proposed_value,
            Proposal.status,
            Proposal.voting_opens_at,
            Proposal.voting_closes_at,
        )
        .order_by(Proposal.voting_closes_at.asc(), Proposal.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    total, rows = await asyncio.gather(
        _run_scalar(count_stmt),
        _run_all(data_stmt),
    )

    total_int = int(total or 0)
    items = [
        ProposalListItem(
            id=int(row.id),
            proposer_username=row.username or "",
            rule_key=row.rule_key,
            proposed_value=float(row.proposed_value or 0),
            status=row.status,
            voting_opens_at=_iso(row.voting_opens_at),
            voting_closes_at=_iso(row.voting_closes_at),
            yes_count=int(row.yes_count or 0),
            no_count=int(row.no_count or 0),
            abstain_count=int(row.abstain_count or 0),
            total_weight=float(row.total_weight or 0),
        )
        for row in rows
    ]

    return PaginatedResponse(
        items=items,
        total=total_int,
        page=page,
        pages=page_count(total_int, limit),
    )


@router.get("/ledger", response_model=PaginatedResponse[GovernanceLedgerItem])
@cached(60, "governance:ledger")
async def ledger(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> PaginatedResponse[GovernanceLedgerItem]:
    count_stmt = select(func.count(GovernanceLedger.id))
    data_stmt = (
        select(
            GovernanceLedger.id,
            GovernanceLedger.at,
            User.username,
            GovernanceLedger.action,
            GovernanceLedger.rule_key,
            GovernanceLedger.old_value,
            GovernanceLedger.new_value,
            GovernanceLedger.reason,
        )
        .outerjoin(User, User.user_id == GovernanceLedger.actor_player_id)
        .order_by(GovernanceLedger.at.desc(), GovernanceLedger.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    total, rows = await asyncio.gather(
        _run_scalar(count_stmt),
        _run_all(data_stmt),
    )

    total_int = int(total or 0)
    items = [
        GovernanceLedgerItem(
            id=int(row.id),
            at=_iso(row.at),
            actor_username=row.username,
            action=row.action,
            rule_key=row.rule_key,
            old_value=row.old_value,
            new_value=row.new_value,
            reason=row.reason,
        )
        for row in rows
    ]
    return PaginatedResponse(
        items=items,
        total=total_int,
        page=page,
        pages=page_count(total_int, limit),
    )


@router.get("/rule-overrides", response_model=list[RuleOverrideItem])
@cached(60, "governance:rule-overrides")
async def rule_overrides(
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> list[RuleOverrideItem]:
    stmt = select(
        RuleOverride.id,
        RuleOverride.rule_key,
        RuleOverride.value,
        RuleOverride.scope,
        RuleOverride.target_id,
        RuleOverride.active_from,
        RuleOverride.active_until,
        RuleOverride.is_active,
    )
    if active_only:
        stmt = stmt.where(RuleOverride.is_active.is_(True))
    stmt = stmt.order_by(RuleOverride.active_from.desc(), RuleOverride.id.desc()).limit(500)

    rows = await _run_all(stmt)
    return [
        RuleOverrideItem(
            id=int(row.id),
            rule_key=row.rule_key,
            value=float(row.value or 0),
            scope=row.scope,
            target_id=int(row.target_id) if row.target_id is not None else None,
            active_from=_iso(row.active_from),
            active_until=_iso(row.active_until) if row.active_until is not None else None,
            is_active=bool(row.is_active),
        )
        for row in rows
    ]


# ── END OF governance.py ──
