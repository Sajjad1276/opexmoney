from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from math import isfinite

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BehaviorSnapshot,
    GovernanceLedger,
    Nation,
    NationMember,
    NationTelegramMember,
    PlayerTemporalProfile,
    Proposal,
    RuleOverride,
    Transaction,
    User,
    Vote,
)
from app.services.economy_metrics import get_player_net_worth, get_player_net_worths
from app.services.economic_event_service import record_economic_event
from app.services.nation_service import get_user_active_nation_context, is_user_active_in_nation
from app.services.rules.registry import clamp_rule_value, get_rule
from app.services.rules.resolver import invalidate_rule_cache
from app.services.temporal_service import rotate_due_profiles
from config import settings


@dataclass(frozen=True)
class GovernanceCycleResult:
    activated: int = 0
    revoked: int = 0
    circuit_suspended: int = 0
    temporal_rotated: int = 0


def utcnow() -> datetime:
    return datetime.utcnow()


def _decimal_log10(value: Decimal) -> Decimal:
    value = max(Decimal("1"), Decimal(str(value)))
    return value.ln() / Decimal("10").ln()


async def is_proposer_eligible(session: AsyncSession, player_id: int) -> bool:
    user = await session.get(User, player_id)
    if user is None:
        return False

    context = await get_user_active_nation_context(
        session,
        player_id,
        repair=False,
        lock=False,
    )
    if context is None:
        return False
    nation, role, _source = context

    players_stmt = (
        select(User.user_id)
        .join(
            NationMember,
            NationMember.user_id == User.user_id,
        )
        .join(
            Nation,
            Nation.nation_id == NationMember.nation_id,
        )
        .where(
            NationMember.nation_id == nation.nation_id,
            NationMember.is_active.is_(True),
            User.username != "",
            or_(
                Nation.is_ai.is_(True),
                select(NationTelegramMember.id)
                .where(
                    NationTelegramMember.nation_id == Nation.nation_id,
                    NationTelegramMember.telegram_user_id == User.user_id,
                    NationTelegramMember.is_active.is_(True),
                )
                .exists(),
            ),
        )
    )
    players = (
        await session.execute(players_stmt)
    ).scalars().all()
    if not players:
        return False

    wealth = await get_player_net_worths(session, players)
    ordered = sorted(
        wealth.items(),
        key=lambda item: (item[1], -item[0]),
        reverse=True,
    )
    rank = next(
        (index + 1 for index, (user_id, _) in enumerate(ordered) if user_id == player_id),
        len(ordered) + 1,
    )
    eligible_count = max(
        1,
        int(
            (
                Decimal(len(ordered))
                * settings.governance_proposal_top_percent
                / Decimal("100")
            ).to_integral_value(rounding=ROUND_CEILING)
        ),
    )
    return rank <= eligible_count


async def is_active_voter(
    session: AsyncSession,
    player_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    user = await session.get(User, player_id)
    if user is None:
        return False

    context = await get_user_active_nation_context(
        session,
        player_id,
        repair=False,
        lock=False,
    )
    if context is None:
        return False

    cutoff = now - timedelta(days=settings.governance_voter_activity_days)
    exists = await session.scalar(
        select(func.count(Transaction.id))
        .where(
            Transaction.user_id == player_id,
            Transaction.created_at >= cutoff,
        )
    )
    return bool(exists)


async def calculate_vote_weight(
    session: AsyncSession,
    player_id: int,
) -> Decimal:
    net_worth = await get_player_net_worth(session, player_id)
    weight = Decimal("1") + _decimal_log10(net_worth)
    return weight.quantize(Decimal("0.000001"))


async def create_proposal(
    session: AsyncSession,
    *,
    proposer_player_id: int,
    rule_key: str,
    proposed_value: Decimal | int | bool,
    target_scope: str,
    target_id: int | None,
    now: datetime | None = None,
) -> Proposal:
    now = now or utcnow()
    rule = get_rule(rule_key)

    if target_scope not in {"global", "nation", "player"}:
        raise ValueError("دامنه این قانون معتبر نیست.")
    if rule.target_scope != target_scope:
        raise ValueError("این قانون در این دامنه قابل پیشنهاد نیست.")
    if target_scope == "nation" and target_id is None:
        raise ValueError("ملت هدف مشخص نشده.")
    if target_scope == "player" and target_id is None:
        raise ValueError("بازیکن هدف مشخص نشده.")
    if target_scope == "global":
        target_id = None

    if not await is_proposer_eligible(session, proposer_player_id):
        raise ValueError("فعلاً شرایط ثبت طرح قانون رو نداری.")

    clamped = clamp_rule_value(proposed_value, rule)
    if isinstance(clamped, bool):
        stored_value = Decimal("1") if clamped else Decimal("0")
    else:
        stored_value = Decimal(str(clamped))

    proposal = Proposal(
        proposer_player_id=proposer_player_id,
        rule_key=rule_key,
        proposed_value=stored_value,
        target_scope=target_scope,
        target_id=target_id,
        status="draft",
        voting_opens_at=now,
        voting_closes_at=now + timedelta(hours=settings.governance_voting_hours),
        effective_from=now + timedelta(hours=settings.governance_voting_hours),
        effective_until=(
            now
            + timedelta(hours=settings.governance_voting_hours)
            + timedelta(days=settings.governance_implementation_days)
        ),
    )
    session.add(proposal)
    await session.flush()
    return proposal


async def cast_vote(
    session: AsyncSession,
    *,
    proposal_id: int,
    player_id: int,
    choice: str,
    now: datetime | None = None,
) -> Vote:
    now = now or utcnow()
    if choice not in {"for", "against", "abstain"}:
        raise ValueError("نوع رأی معتبر نیست.")

    proposal = (
        await session.execute(
            select(Proposal)
            .where(Proposal.id == proposal_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if proposal is None:
        raise ValueError("طرح پیدا نشد.")
    if proposal.status != "voting":
        raise ValueError("این طرح الان در حال رأی‌گیری نیست.")
    if now < proposal.voting_opens_at or now >= proposal.voting_closes_at:
        raise ValueError("مهلت رأی‌گیری این طرح تمام شده.")

    if proposal.target_scope == "nation" and proposal.target_id is not None:
        eligible_member = await is_user_active_in_nation(
            session,
            player_id,
            proposal.target_id,
            lock=False,
        )
        if not eligible_member:
            raise ValueError("برای رأی دادن به قانون این ملت باید عضو فعال همان ملت باشی.")

    if not await is_active_voter(session, player_id, now=now):
        raise ValueError("برای رأی دادن باید در ۷ روز اخیر حداقل یک معامله داشته باشی.")

    existing = await session.scalar(
        select(Vote.id)
        .where(
            Vote.proposal_id == proposal_id,
            Vote.player_id == player_id,
        )
    )
    if existing is not None:
        raise ValueError("قبلاً به این طرح رأی دادی.")

    weight = await calculate_vote_weight(session, player_id)
    vote = Vote(
        proposal_id=proposal_id,
        player_id=player_id,
        choice=choice,
        weight=weight,
    )
    session.add(vote)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValueError("قبلاً به این طرح رأی دادی.") from exc
    return vote


async def _activate_proposal(
    session: AsyncSession,
    proposal: Proposal,
    now: datetime,
) -> bool:
    active_count = await session.scalar(
        select(func.count(RuleOverride.id))
        .where(RuleOverride.is_active.is_(True))
    ) or 0

    existing = (
        await session.execute(
            select(RuleOverride)
            .where(
                RuleOverride.rule_key == proposal.rule_key,
                RuleOverride.scope == proposal.target_scope,
                RuleOverride.target_id == proposal.target_id,
                RuleOverride.is_active.is_(True),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if existing is None and active_count >= settings.governance_max_active_rules:
        return False

    if existing is not None:
        old_value = str(existing.value)
        existing.is_active = False
        existing.suspended_until = None
        session.add(
            GovernanceLedger(
                actor_player_id=proposal.proposer_player_id,
                action="replace",
                rule_key=proposal.rule_key,
                old_value=old_value,
                new_value=str(proposal.proposed_value),
                reason="قانون جدید جایگزین قانون فعال قبلی شد.",
                at=now,
            )
        )

    override = RuleOverride(
        rule_key=proposal.rule_key,
        value=proposal.proposed_value,
        scope=proposal.target_scope,
        target_id=proposal.target_id,
        source_proposal_id=proposal.id,
        active_from=proposal.effective_from or now,
        active_until=proposal.effective_until,
        suspended_until=None,
        is_active=True,
    )
    session.add(override)
    proposal.status = "active"
    await session.flush()
    invalidate_rule_cache(proposal.rule_key)

    if proposal.target_scope == "nation" and proposal.target_id is not None:
        record_economic_event(
            session,
            nation_id=proposal.target_id,
            event_type="GOVERNANCE_RULE_ACTIVATED",
            actor_id=proposal.proposer_player_id,
            metadata={
                "proposal_id": proposal.id,
                "rule_key": proposal.rule_key,
                "value": str(proposal.proposed_value),
            },
        )

    session.add(
        GovernanceLedger(
            actor_player_id=proposal.proposer_player_id,
            action="activate",
            rule_key=proposal.rule_key,
            old_value=str(existing.value) if existing is not None else None,
            new_value=str(proposal.proposed_value),
            reason="طرح به تصویب رسید و فعال شد.",
            at=now,
        )
    )
    return True


async def _activate_passed_proposals(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    activated = 0
    proposals = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.status == "passed",
                Proposal.effective_from.is_not(None),
                Proposal.effective_from <= now,
                Proposal.effective_until.is_not(None),
                Proposal.effective_until > now,
            )
            .with_for_update()
        )
    ).scalars().all()

    for proposal in proposals:
        if await _activate_proposal(session, proposal, now):
            activated += 1

    return activated


async def _close_voting_and_activate(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[int, int]:
    activated = 0
    rejected = 0
    proposals = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.status == "voting",
                Proposal.voting_closes_at <= now,
            )
            .with_for_update()
        )
    ).scalars().all()

    for proposal in proposals:
        votes = (
            await session.execute(
                select(Vote.choice, Vote.weight)
                .where(Vote.proposal_id == proposal.id)
            )
        ).all()
        for_weight = sum(
            (Decimal(str(weight)) for choice, weight in votes if choice == "for"),
            Decimal("0"),
        )
        against_weight = sum(
            (Decimal(str(weight)) for choice, weight in votes if choice == "against"),
            Decimal("0"),
        )
        total_weight = sum(
            (Decimal(str(weight)) for _, weight in votes),
            Decimal("0"),
        )

        if total_weight < settings.governance_min_quorum_weight:
            proposal.status = "expired"
            continue

        if for_weight <= against_weight:
            proposal.status = "rejected"
            rejected += 1
            continue

        proposal.status = "passed"
        if await _activate_proposal(session, proposal, now):
            activated += 1

    return activated, rejected


async def _revoke_expired_proposals(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    count = 0
    proposals = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.status == "active",
                Proposal.effective_until.is_not(None),
                Proposal.effective_until <= now,
            )
            .with_for_update()
        )
    ).scalars().all()

    for proposal in proposals:
        overrides = (
            await session.execute(
                select(RuleOverride)
                .where(
                    RuleOverride.source_proposal_id == proposal.id,
                    RuleOverride.is_active.is_(True),
                )
                .with_for_update()
            )
        ).scalars().all()
        for override in overrides:
            override.is_active = False
            override.suspended_until = None
        proposal.status = "revoked"
        session.add(
            GovernanceLedger(
                actor_player_id=None,
                action="revoke",
                rule_key=proposal.rule_key,
                old_value=str(proposal.proposed_value),
                new_value=None,
                reason="مدت اجرای قانون تمام شد.",
                at=now,
            )
        )
        invalidate_rule_cache(proposal.rule_key)
        count += len(overrides)
    return count


async def check_circuit_breaker(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    snapshots = (
        await session.execute(
            select(BehaviorSnapshot)
            .order_by(BehaviorSnapshot.at.desc(), BehaviorSnapshot.id.desc())
            .limit(2)
        )
    ).scalars().all()

    if len(snapshots) < 2:
        return 0

    current, previous = snapshots[0], snapshots[1]
    previous_avg = Decimal(str(previous.avg_net_worth))
    current_avg = Decimal(str(current.avg_net_worth))
    if previous_avg <= 0:
        return 0

    change_ratio = abs(current_avg - previous_avg) / previous_avg
    if change_ratio <= settings.governance_circuit_breaker_change_ratio:
        return 0

    marker = f"snapshot:{current.id}"
    already_handled = await session.scalar(
        select(GovernanceLedger.id)
        .where(
            GovernanceLedger.action == "circuit_breaker",
            GovernanceLedger.reason == marker,
        )
        .limit(1)
    )
    if already_handled is not None:
        return 0

    overrides = (
        await session.execute(
            select(RuleOverride)
            .where(RuleOverride.is_active.is_(True))
            .with_for_update()
        )
    ).scalars().all()

    if not overrides:
        session.add(
            GovernanceLedger(
                actor_player_id=None,
                action="circuit_breaker",
                rule_key="*",
                old_value=str(previous_avg),
                new_value=str(current_avg),
                reason=marker,
                at=now,
            )
        )
        return 0

    suspended_until = now + timedelta(hours=settings.governance_circuit_breaker_suspend_hours)
    for override in overrides:
        override.is_active = False
        override.suspended_until = suspended_until
        session.add(
            GovernanceLedger(
                actor_player_id=None,
                action="circuit_breaker",
                rule_key=override.rule_key,
                old_value=str(override.value),
                new_value=None,
                reason=marker,
                at=now,
            )
        )
        invalidate_rule_cache(override.rule_key)
    return len(overrides)


async def governance_cycle(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> GovernanceCycleResult:
    now = now or utcnow()

    suspended = await check_circuit_breaker(session, now=now)

    suspended_overrides = (
        await session.execute(
            select(RuleOverride)
            .where(
                RuleOverride.is_active.is_(False),
                RuleOverride.suspended_until.is_not(None),
                RuleOverride.suspended_until <= now,
                RuleOverride.active_until.is_not(None),
                RuleOverride.active_until > now,
            )
            .with_for_update()
        )
    ).scalars().all()
    for override in suspended_overrides:
        conflict = (
            await session.execute(
                select(RuleOverride)
                .where(
                    RuleOverride.id != override.id,
                    RuleOverride.rule_key == override.rule_key,
                    RuleOverride.scope == override.scope,
                    RuleOverride.target_id == override.target_id,
                    RuleOverride.is_active.is_(True),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if conflict is not None:
            override.suspended_until = None
            session.add(
                GovernanceLedger(
                    actor_player_id=None,
                    action="resume_conflict",
                    rule_key=override.rule_key,
                    old_value=str(override.value),
                    new_value=str(conflict.value),
                    reason="قانون جدید فعال بوده و قانون معلق دوباره فعال نشد.",
                    at=now,
                )
            )
            continue

        override.is_active = True
        override.suspended_until = None
        invalidate_rule_cache(override.rule_key)

    stale_drafts = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.status == "draft",
                Proposal.voting_opens_at <= now - timedelta(days=settings.governance_draft_window_days),
            )
            .with_for_update()
        )
    ).scalars().all()
    for proposal in stale_drafts:
        proposal.status = "expired"

    draft_proposals = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.status == "draft",
                Proposal.voting_opens_at <= now,
                Proposal.voting_closes_at > now,
            )
            .with_for_update()
        )
    ).scalars().all()
    for proposal in draft_proposals:
        proposal.status = "voting"

    activated, _ = await _close_voting_and_activate(session, now=now)
    activated += await _activate_passed_proposals(session, now=now)
    revoked = await _revoke_expired_proposals(session, now=now)
    rotated = await rotate_due_profiles(session, now=now)

    return GovernanceCycleResult(
        activated=activated,
        revoked=revoked,
        circuit_suspended=suspended,
        temporal_rotated=rotated,
    )


async def revoke_override(
    session: AsyncSession,
    *,
    actor_player_id: int,
    override_id: int,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    actor = await session.get(User, actor_player_id)
    if actor is None or actor.role != "founder":
        raise ValueError("فقط بنیان‌گذار می‌تونه این قانون رو فوراً لغو کنه.")

    override = (
        await session.execute(
            select(RuleOverride)
            .where(RuleOverride.id == override_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if override is None or not override.is_active:
        raise ValueError("این قانون فعال نیست.")

    old_value = str(override.value)
    override.is_active = False
    override.suspended_until = None

    if override.source_proposal_id is not None:
        proposal = await session.get(Proposal, override.source_proposal_id, with_for_update=True)
        if proposal is not None:
            proposal.status = "revoked"

    session.add(
        GovernanceLedger(
            actor_player_id=actor_player_id,
            action="revoke",
            rule_key=override.rule_key,
            old_value=old_value,
            new_value=None,
            reason="لغو فوری توسط بنیان‌گذار.",
            at=now,
        )
    )
    invalidate_rule_cache(override.rule_key)


async def get_active_overrides(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[RuleOverride]:
    now = now or utcnow()
    result = await session.execute(
        select(RuleOverride)
        .where(
            RuleOverride.is_active.is_(True),
            RuleOverride.active_from <= now,
            (RuleOverride.active_until.is_(None) | (RuleOverride.active_until > now)),
            (RuleOverride.suspended_until.is_(None) | (RuleOverride.suspended_until <= now)),
        )
        .order_by(RuleOverride.rule_key.asc(), RuleOverride.scope.asc(), RuleOverride.id.asc())
    )
    return list(result.scalars().all())


async def get_governance_history(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 8,
) -> list[tuple[GovernanceLedger, str | None]]:
    rows = (
        await session.execute(
            select(GovernanceLedger, User.username)
            .outerjoin(User, User.user_id == GovernanceLedger.actor_player_id)
            .order_by(GovernanceLedger.at.desc(), GovernanceLedger.id.desc())
            .offset(max(0, offset))
            .limit(limit)
        )
    ).all()
    return list(rows)
