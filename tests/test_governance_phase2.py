from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select, update

from app.database.models import (
    BehaviorSnapshot,
    CurrencyHolding,
    GovernanceLedger,
    Nation,
    PlayerTemporalProfile,
    Proposal,
    RuleOverride,
    Transaction,
    User,
    UserActivity,
    Vote,
    NationMemberHistory,
    RateHistory,
)
from app.database.session import async_session
from app.services.economic_engine import clamp, update_nation_rates
from app.services.governance_service import (
    cast_vote,
    check_circuit_breaker,
    create_proposal,
    governance_cycle,
)
from app.services.rules.registry import get_rule
from app.services.rules.resolver import invalidate_rule_cache, resolve
from app.services.temporal_service import get_peak_multiplier
from app.utils.formatting import calc_trade


TEST_USER_MIN = 920001
TEST_USER_MAX = 920020
TEST_GROUP_MIN = -100920020
TEST_GROUP_MAX = -100920001


@pytest.fixture(autouse=True)
async def cleanup_phase2_rows():
    yield
    async with async_session() as session:
        async with session.begin():
            nation_ids = select(Nation.nation_id).where(
                Nation.group_id.between(TEST_GROUP_MIN, TEST_GROUP_MAX)
            )
            proposal_ids = select(Proposal.id).where(
                Proposal.proposer_player_id.between(TEST_USER_MIN, TEST_USER_MAX)
            )
            behavior_ids = select(BehaviorSnapshot.id).where(
                BehaviorSnapshot.active_players_count == 920006
            )

            await session.execute(delete(Vote).where(
                Vote.player_id.between(TEST_USER_MIN, TEST_USER_MAX)
            ))
            await session.execute(delete(RuleOverride).where(
                RuleOverride.source_proposal_id.in_(proposal_ids)
                | (
                    (RuleOverride.rule_key == "market.tx_fee")
                    & (RuleOverride.scope == "global")
                    & (RuleOverride.active_until.is_not(None))
                )
            ))
            await session.execute(delete(GovernanceLedger).where(
                GovernanceLedger.actor_player_id.between(TEST_USER_MIN, TEST_USER_MAX)
                | (
                    (GovernanceLedger.action == "circuit_breaker")
                    & (GovernanceLedger.old_value == "920100")
                    & (GovernanceLedger.new_value == "920200")
                )
            ))
            await session.execute(delete(Proposal).where(
                Proposal.id.in_(proposal_ids)
            ))
            await session.execute(delete(BehaviorSnapshot).where(
                BehaviorSnapshot.id.in_(behavior_ids)
            ))
            await session.execute(delete(RateHistory).where(
                RateHistory.nation_id.in_(nation_ids)
            ))
            await session.execute(delete(NationMemberHistory).where(
                NationMemberHistory.nation_id.in_(nation_ids)
            ))
            await session.execute(
                delete(PlayerTemporalProfile).where(
                    PlayerTemporalProfile.player_id.between(TEST_USER_MIN, TEST_USER_MAX)
                )
            )
            await session.execute(
                delete(Transaction).where(
                    Transaction.user_id.between(TEST_USER_MIN, TEST_USER_MAX)
                )
            )
            await session.execute(
                delete(UserActivity).where(
                    UserActivity.user_id.between(TEST_USER_MIN, TEST_USER_MAX)
                )
            )
            await session.execute(
                delete(CurrencyHolding).where(
                    CurrencyHolding.user_id.between(TEST_USER_MIN, TEST_USER_MAX)
                )
            )
            await session.execute(
                update(User)
                .where(User.user_id.between(TEST_USER_MIN, TEST_USER_MAX))
                .values(home_nation_id=None)
            )
            await session.execute(
                delete(Nation).where(
                    Nation.group_id.between(TEST_GROUP_MIN, TEST_GROUP_MAX)
                )
            )
            await session.execute(
                delete(User).where(
                    User.user_id.between(TEST_USER_MIN, TEST_USER_MAX)
                )
            )


async def seed_nation_user(
    user_id: int,
    *,
    nation_name: str = "Phase Two",
    currency_code: str = "P2A",
    group_id: int = TEST_GROUP_MIN,
    role: str = "player",
    xr_balance: Decimal = Decimal("1000"),
) -> tuple[int, int]:
    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                name=nation_name,
                currency_code=currency_code if currency_code != "P2A" else f"T{user_id % 100:02d}",
                group_id=group_id,
                founder_user_id=user_id if role == "founder" else None,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            user = User(
                user_id=user_id,
                username=f"phase{user_id}",
                home_nation_id=None,
                balance=Decimal("0"),
                xr_balance=xr_balance,
                role=role,
            )
            session.add_all([nation, user])
            await session.flush()
            user.home_nation_id = nation.nation_id
            return nation.nation_id, user.user_id


@pytest.mark.asyncio
async def test_resolver_priority_and_clamping():
    nation_id, player_id = await seed_nation_user(
        TEST_USER_MIN,
        role="founder",
        group_id=TEST_GROUP_MIN,
    )
    now = datetime.utcnow()

    async with async_session() as session:
        async with session.begin():
            session.add_all([
                RuleOverride(
                    rule_key="market.tx_fee",
                    value=Decimal("0.25"),
                    scope="global",
                    target_id=None,
                    active_from=now - timedelta(minutes=1),
                    active_until=now + timedelta(hours=1),
                    is_active=True,
                ),
                RuleOverride(
                    rule_key="market.tx_fee",
                    value=Decimal("1.5"),
                    scope="nation",
                    target_id=nation_id,
                    active_from=now - timedelta(minutes=1),
                    active_until=now + timedelta(hours=1),
                    is_active=True,
                ),
                RuleOverride(
                    rule_key="market.tx_fee",
                    value=Decimal("3"),
                    scope="player",
                    target_id=player_id,
                    active_from=now - timedelta(minutes=1),
                    active_until=now + timedelta(hours=1),
                    is_active=True,
                ),
            ])
            await session.flush()

            invalidate_rule_cache("market.tx_fee")
            assert await resolve(
                session,
                "market.tx_fee",
                nation_id=nation_id,
                player_id=player_id,
            ) == Decimal("3.000000")

            player_override = await session.scalar(
                select(RuleOverride).where(RuleOverride.scope == "player")
            )
            player_override.is_active = False
            invalidate_rule_cache("market.tx_fee")
            assert await resolve(
                session,
                "market.tx_fee",
                nation_id=nation_id,
                player_id=player_id,
            ) == Decimal("1.500000")

            nation_override = await session.scalar(
                select(RuleOverride).where(RuleOverride.scope == "nation")
            )
            nation_override.is_active = False
            invalidate_rule_cache("market.tx_fee")
            assert await resolve(
                session,
                "market.tx_fee",
                nation_id=nation_id,
                player_id=player_id,
            ) == Decimal("0.250000")

            global_override = await session.scalar(
                select(RuleOverride).where(RuleOverride.scope == "global")
            )
            global_override.value = Decimal("99")
            invalidate_rule_cache("market.tx_fee")
            assert await resolve(
                session,
                "market.tx_fee",
                nation_id=nation_id,
                player_id=player_id,
            ) == Decimal("10.000000")


@pytest.mark.asyncio
async def test_rate_engine_default_regression():
    nation_id, _ = await seed_nation_user(
        TEST_USER_MIN + 8,
        role="founder",
        group_id=TEST_GROUP_MIN + 8,
    )

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id, with_for_update=True)
            await update_nation_rates(
                session,
                now=datetime(2026, 1, 15, 12, 0, 0),
            )
            await session.flush()

            assert nation.exchange_rate == Decimal("0.9800")
            assert nation.rate_prev == Decimal("1.0000")


@pytest.mark.asyncio
async def test_rule_defaults_preserve_previous_trade_math():
    buy = calc_trade(
        Decimal("100"),
        Decimal("2"),
        True,
    )
    sell = calc_trade(
        Decimal("100"),
        Decimal("2"),
        False,
    )

    assert buy["fee"] == Decimal("0.5000")
    assert buy["receive"] == Decimal("49.7500")
    assert sell["fee"] == Decimal("0.5000")
    assert sell["receive"] == Decimal("199.5000")


@pytest.mark.asyncio
async def test_governance_full_state_machine():
    nation_id, player_id = await seed_nation_user(
        TEST_USER_MIN + 1,
        role="founder",
        group_id=TEST_GROUP_MIN + 1,
    )
    voter_nation_id, voter_id = await seed_nation_user(
        TEST_USER_MIN + 2,
        role="player",
        group_id=TEST_GROUP_MIN + 2,
        xr_balance=Decimal("1000"),
    )

    async with async_session() as session:
        async with session.begin():
            session.add(
                Transaction(
                    user_id=voter_id,
                    nation_id=voter_nation_id,
                    transaction_type="buy",
                    spend_xr=Decimal("10"),
                    amount=Decimal("9.95"),
                    fee_xr=Decimal("0.05"),
                    rate=Decimal("1"),
                )
            )

    created_at = datetime.utcnow()
    async with async_session() as session:
        async with session.begin():
            proposal = await create_proposal(
                session,
                proposer_player_id=player_id,
                rule_key="market.tx_fee",
                proposed_value=Decimal("1"),
                target_scope="global",
                target_id=None,
                now=created_at,
            )
            assert proposal.status == "draft"
            proposal.voting_opens_at = created_at + timedelta(hours=1)
            proposal.voting_closes_at = created_at + timedelta(hours=2)
            proposal.effective_from = created_at + timedelta(hours=2)
            proposal.effective_until = created_at + timedelta(days=9)
            proposal_id = proposal.id

    async with async_session() as session:
        async with session.begin():
            await governance_cycle(
                session,
                now=created_at + timedelta(hours=1, minutes=1),
            )
            proposal = await session.get(Proposal, proposal_id)
            assert proposal.status == "voting"

            await cast_vote(
                session,
                proposal_id=proposal_id,
                player_id=voter_id,
                choice="for",
                now=created_at + timedelta(hours=1, minutes=2),
            )

    async with async_session() as session:
        async with session.begin():
            await governance_cycle(
                session,
                now=created_at + timedelta(hours=2, minutes=1),
            )
            proposal = await session.get(Proposal, proposal_id)
            assert proposal.status == "active"
            override = await session.scalar(
                select(RuleOverride).where(RuleOverride.source_proposal_id == proposal_id)
            )
            assert override is not None
            assert override.is_active is True

    async with async_session() as session:
        async with session.begin():
            await governance_cycle(
                session,
                now=created_at + timedelta(days=10),
            )
            proposal = await session.get(Proposal, proposal_id)
            assert proposal.status == "revoked"
            override = await session.scalar(
                select(RuleOverride).where(RuleOverride.source_proposal_id == proposal_id)
            )
            assert override.is_active is False


@pytest.mark.asyncio
async def test_governance_cycle_is_idempotent():
    nation_id, player_id = await seed_nation_user(
        TEST_USER_MIN + 3,
        role="founder",
        group_id=TEST_GROUP_MIN + 3,
    )
    now = datetime.utcnow()

    async with async_session() as session:
        async with session.begin():
            proposal = Proposal(
                proposer_player_id=player_id,
                rule_key="market.tx_fee",
                proposed_value=Decimal("1"),
                target_scope="global",
                target_id=None,
                status="passed",
                voting_opens_at=now - timedelta(hours=3),
                voting_closes_at=now - timedelta(hours=2),
                effective_from=now - timedelta(minutes=1),
                effective_until=now + timedelta(days=7),
            )
            session.add(proposal)
            await session.flush()
            proposal_id = proposal.id

    async with async_session() as session:
        async with session.begin():
            first = await governance_cycle(session, now=now)
            assert first.activated == 1

    async with async_session() as session:
        async with session.begin():
            second = await governance_cycle(session, now=now + timedelta(minutes=1))
            assert second.activated == 0
            count = await session.scalar(
                select(RuleOverride.id).where(RuleOverride.source_proposal_id == proposal_id)
            )
            assert count is not None
            total = await session.scalar(
                select(func.count(RuleOverride.id)).where(
                    RuleOverride.source_proposal_id == proposal_id,
                    RuleOverride.is_active.is_(True),
                )
            )
            assert total == 1


@pytest.mark.asyncio
async def test_duplicate_vote_is_rejected():
    nation_id, founder_id = await seed_nation_user(
        TEST_USER_MIN + 4,
        role="founder",
        group_id=TEST_GROUP_MIN + 4,
    )
    _, voter_id = await seed_nation_user(
        TEST_USER_MIN + 5,
        role="player",
        group_id=TEST_GROUP_MIN + 5,
        xr_balance=Decimal("1000"),
    )
    now = datetime.utcnow()

    async with async_session() as session:
        async with session.begin():
            session.add(
                Transaction(
                    user_id=voter_id,
                    nation_id=nation_id,
                    transaction_type="buy",
                    spend_xr=Decimal("10"),
                    amount=Decimal("9.95"),
                    fee_xr=Decimal("0.05"),
                    rate=Decimal("1"),
                )
            )
            proposal = Proposal(
                proposer_player_id=founder_id,
                rule_key="market.tx_fee",
                proposed_value=Decimal("1"),
                target_scope="global",
                target_id=None,
                status="voting",
                voting_opens_at=now - timedelta(hours=1),
                voting_closes_at=now + timedelta(hours=1),
            )
            session.add(proposal)
            await session.flush()
            proposal_id = proposal.id

            await cast_vote(
                session,
                proposal_id=proposal_id,
                player_id=voter_id,
                choice="for",
                now=now,
            )

            with pytest.raises(ValueError, match="قبلاً"):
                await cast_vote(
                    session,
                    proposal_id=proposal_id,
                    player_id=voter_id,
                    choice="against",
                    now=now,
                )


@pytest.mark.asyncio
async def test_circuit_breaker_suspends_active_overrides():
    _, founder_id = await seed_nation_user(
        TEST_USER_MIN + 6,
        role="founder",
        group_id=TEST_GROUP_MIN + 6,
    )
    now = datetime.utcnow()

    async with async_session() as session:
        async with session.begin():
            override = RuleOverride(
                rule_key="market.tx_fee",
                value=Decimal("1"),
                scope="global",
                active_from=now - timedelta(hours=1),
                active_until=now + timedelta(days=1),
                is_active=True,
            )
            session.add(override)
            session.add_all([
                BehaviorSnapshot(
                    at=now - timedelta(minutes=1),
                    active_players_count=10,
                    buy_tx_count=1,
                    sell_tx_count=1,
                    export_tx_count=0,
                    import_tx_count=0,
                    total_volume=Decimal("920006"),
                    avg_net_worth=Decimal("920100"),
                    median_net_worth=Decimal("100"),
                    gini_coefficient=Decimal("0.1"),
                    top10_wealth_share=Decimal("0.2"),
                ),
                BehaviorSnapshot(
                    at=now,
                    active_players_count=10,
                    buy_tx_count=1,
                    sell_tx_count=1,
                    export_tx_count=0,
                    import_tx_count=0,
                    total_volume=Decimal("920006"),
                    avg_net_worth=Decimal("920200"),
                    median_net_worth=Decimal("100"),
                    gini_coefficient=Decimal("0.1"),
                    top10_wealth_share=Decimal("0.2"),
                ),
            ])
            await session.flush()

            suspended = await check_circuit_breaker(session, now=now)
            assert suspended == 1

            override = await session.get(RuleOverride, override.id)
            assert override.is_active is False
            assert override.suspended_until is not None

            assert await check_circuit_breaker(session, now=now + timedelta(minutes=1)) == 0


@pytest.mark.asyncio
async def test_temporal_multiplier_boundaries():
    _, player_id = await seed_nation_user(
        TEST_USER_MIN + 7,
        role="player",
        group_id=TEST_GROUP_MIN + 7,
    )

    local_zone = ZoneInfo("Asia/Tehran")
    local_peak = datetime(2026, 1, 15, 10, 0, tzinfo=local_zone)
    utc_peak = local_peak.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_inside_end = local_peak.replace(hour=11).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_outside = local_peak.replace(hour=12).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    async with async_session() as session:
        async with session.begin():
            session.add(
                PlayerTemporalProfile(
                    player_id=player_id,
                    peak_hour_start=10,
                    peak_window_hours=2,
                    peak_multiplier=Decimal("1.5"),
                    assigned_at=utc_peak - timedelta(days=1),
                    next_rotation_at=utc_peak + timedelta(days=6),
                )
            )
            await session.flush()

            assert await get_peak_multiplier(session, player_id, now=utc_peak) == Decimal("1.5")
            assert await get_peak_multiplier(session, player_id, now=utc_inside_end) == Decimal("1.5")
            assert await get_peak_multiplier(session, player_id, now=utc_outside) == Decimal("1")


def test_rate_clamp_contract():
    assert clamp(Decimal("100"), Decimal("0.10"), Decimal("50")) == Decimal("50")
    assert clamp(Decimal("-1"), Decimal("0.10"), Decimal("50")) == Decimal("0.10")
