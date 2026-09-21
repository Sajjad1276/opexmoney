from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_admin_user
from admin.cache import TTL_GOVERNANCE_LEDGER, TTL_PROPOSALS, TTL_RULE_OVERRIDES, cached
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import (
    GovernanceLedgerItem,
    GovernanceLedgerPage,
    ProposalItem,
    ProposalsPage,
    RuleOverrideItem,
)
from app.database.models import GovernanceLedger, Proposal, RuleOverride, User, Vote
from redis.asyncio import Redis

router = APIRouter(prefix="/api/governance", tags=["governance"])


@router.get("/proposals", response_model=ProposalsPage)
@cached(TTL_PROPOSALS, "governance-proposals")
async def proposals(
    status: str = Query(default="voting"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> ProposalsPage:
    conditions = [Proposal.status == status] if status != "all" else []
    total = int(await db.scalar(select(func.count(Proposal.id)).where(*conditions)) or 0)

    yes_votes = func.coalesce(func.sum(case((Vote.choice == "yes", 1), else_=0)), 0)
    no_votes = func.coalesce(func.sum(case((Vote.choice == "no", 1), else_=0)), 0)
    abstain_votes = func.coalesce(func.sum(case((Vote.choice == "abstain", 1), else_=0)), 0)
    total_weight = func.coalesce(func.sum(Vote.weight), 0)

    stmt = (
        select(
            Proposal,
            User.username,
            yes_votes.label("yes_votes"),
            no_votes.label("no_votes"),
            abstain_votes.label("abstain_votes"),
            total_weight.label("total_weight"),
        )
        .join(User, User.user_id == Proposal.proposer_player_id)
        .outerjoin(Vote, Vote.proposal_id == Proposal.id)
        .where(*conditions)
        .group_by(Proposal.id, User.user_id)
        .order_by(Proposal.voting_closes_at.asc(), Proposal.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        ProposalItem(
            id=proposal.id,
            created_at=proposal.created_at,
            proposer_player_id=proposal.proposer_player_id,
            proposer_username=username,
            rule_key=proposal.rule_key,
            proposed_value=proposal.proposed_value,
            target_scope=proposal.target_scope,
            target_id=proposal.target_id,
            status=proposal.status,
            voting_opens_at=proposal.voting_opens_at,
            voting_closes_at=proposal.voting_closes_at,
            effective_from=proposal.effective_from,
            effective_until=proposal.effective_until,
            yes_votes=int(yes or 0),
            no_votes=int(no or 0),
            abstain_votes=int(abstain or 0),
            total_weight=weight or 0,
        )
        for proposal, username, yes, no, abstain, weight in rows
    ]
    return ProposalsPage(items=items, total=total, page=page, pages=page_count(total, limit))


@router.get("/ledger", response_model=GovernanceLedgerPage)
@cached(TTL_GOVERNANCE_LEDGER, "governance-ledger")
async def ledger(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> GovernanceLedgerPage:
    total = int(await db.scalar(select(func.count(GovernanceLedger.id))) or 0)
    rows = (
        await db.execute(
            select(GovernanceLedger, User.username)
            .outerjoin(User, User.user_id == GovernanceLedger.actor_player_id)
            .order_by(GovernanceLedger.at.desc(), GovernanceLedger.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    items = [
        GovernanceLedgerItem(
            id=entry.id,
            at=entry.at,
            actor_player_id=entry.actor_player_id,
            actor_username=username,
            action=entry.action,
            rule_key=entry.rule_key,
            old_value=entry.old_value,
            new_value=entry.new_value,
            reason=entry.reason,
        )
        for entry, username in rows
    ]
    return GovernanceLedgerPage(items=items, total=total, page=page, pages=page_count(total, limit))


@router.get("/rule-overrides", response_model=list[RuleOverrideItem])
@cached(TTL_RULE_OVERRIDES, "governance-rule-overrides")
async def rule_overrides(
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> list[RuleOverrideItem]:
    stmt = select(RuleOverride)
    if active_only:
        stmt = stmt.where(RuleOverride.is_active.is_(True))
    rows = (
        await db.execute(
            stmt.order_by(RuleOverride.active_from.desc(), RuleOverride.id.desc())
        )
    ).scalars().all()
    return [
        RuleOverrideItem(
            id=item.id,
            rule_key=item.rule_key,
            value=item.value,
            scope=item.scope,
            target_id=item.target_id,
            source_proposal_id=item.source_proposal_id,
            active_from=item.active_from,
            active_until=item.active_until,
            suspended_until=item.suspended_until,
            is_active=item.is_active,
        )
        for item in rows
    ]
