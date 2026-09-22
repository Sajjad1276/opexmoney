from __future__ import annotations

import logging
import random
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationMember,
    NationMemberHistory,
    NationMemberRole,
    RateHistory,
    Transaction,
    User,
    UserActivity,
)
from app.services.ai_world_data import AI_WORLD_SEEDS
from app.services.rules.resolver import resolve
from app.services.world_action_service import execute_trade_action

logger = logging.getLogger(__name__)

AI_BATCH_MODULO = 4
AI_MIN_TRADE_XR = Decimal("10")
AI_MAX_TRADE_XR = Decimal("160")
AI_MIN_HOLDING_TO_SELL = Decimal("10")
AI_HOME_CASH_TARGET = Decimal("150")


def _utc_naive(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return current.astimezone(UTC).replace(tzinfo=None)


def _rate_change(nation: Nation) -> Decimal:
    previous = Decimal(str(nation.rate_prev or nation.exchange_rate or 0))
    current = Decimal(str(nation.exchange_rate or 0))
    if previous <= 0:
        return Decimal("0")
    return (current - previous) / previous


async def ensure_ai_world(session: AsyncSession) -> dict[str, int]:
    """Idempotently create the persistent AI civilization.

    The function never resets existing AI wealth or holdings. It repairs only
    missing seed rows so deploys/restarts cannot duplicate the NPC world.
    """
    now = _utc_naive()
    created_nations = 0
    created_users = 0
    created_holdings = 0
    created_memberships = 0

    for index, seed in enumerate(AI_WORLD_SEEDS, start=1):
        invite_code = f"OPX-AI-{index:03d}"
        nation = await session.scalar(
            select(Nation).where(Nation.invite_code == invite_code).limit(1)
        )
        if nation is None:
            nation = Nation(
                group_id=None,
                name=seed.name,
                flag_emoji="🏴",
                currency_code=seed.currency_code,
                founder_user_id=None,
                exchange_rate=(
                    Decimal("0.75")
                    + Decimal(str((index - 1) % 20)) * Decimal("0.055")
                    + Decimal(str((index - 1) // 20)) * Decimal("0.015")
                ).quantize(Decimal("0.0001")),
                rate_prev=Decimal("1.0000"),
                rate_24h_open=Decimal("1.0000"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                nation_rank=None,
                last_rate_update=now,
                member_count=0,
                is_active=True,
                is_ai=True,
                join_policy="OPEN",
                personality=seed.personality,
                invite_code=invite_code,
                treasury=Decimal("0"),
            )
            session.add(nation)
            await session.flush()
            created_nations += 1

            session.add(
                RateHistory(
                    nation_id=nation.nation_id,
                    rate=nation.exchange_rate,
                    volume=Decimal("0"),
                    active_members=1,
                    calculated_at=now,
                )
            )
            session.add(
                NationMemberHistory(
                    nation_id=nation.nation_id,
                    member_count=1,
                    recorded_at=now,
                )
            )

        else:
            nation.is_active = True
            nation.is_ai = True
            nation.join_policy = "OPEN"
            nation.personality = seed.personality

        user = await session.get(User, seed.player_id)
        if user is None:
            user = User(
                user_id=seed.player_id,
                username=seed.player_name,
                home_nation_id=nation.nation_id,
                balance=Decimal("500.00"),
                xr_balance=Decimal("1000.00"),
                role="player",
                is_ai=True,
                ai_strategy=seed.personality,
            )
            session.add(user)
            await session.flush()
            created_users += 1
            session.add(
                UserActivity(
                    user_id=user.user_id,
                    nation_id=nation.nation_id,
                    activity_type=ActivityType.LOGIN,
                    created_at=now,
                )
            )
        else:
            user.is_ai = True
            user.ai_strategy = seed.personality
            if user.home_nation_id is None:
                user.home_nation_id = nation.nation_id

        holding = await session.scalar(
            select(CurrencyHolding)
            .where(
                CurrencyHolding.user_id == seed.player_id,
                CurrencyHolding.nation_id == nation.nation_id,
            )
            .limit(1)
        )
        if holding is None:
            session.add(
                CurrencyHolding(
                    user_id=seed.player_id,
                    nation_id=nation.nation_id,
                    amount=Decimal("500.0000"),
                )
            )
            created_holdings += 1

        member = await session.scalar(
            select(NationMember)
            .where(
                NationMember.user_id == seed.player_id,
                NationMember.nation_id == nation.nation_id,
            )
            .limit(1)
        )
        if member is None:
            session.add(
                NationMember(
                    nation_id=nation.nation_id,
                    user_id=seed.player_id,
                    role=NationMemberRole.CITIZEN,
                    is_active=True,
                )
            )
            created_memberships += 1
        else:
            member.is_active = True
            if member.role == NationMemberRole.FOUNDER:
                member.role = NationMemberRole.CITIZEN

        if nation.member_count < 1:
            nation.member_count = 1
            nation.active_members_24h = max(int(nation.active_members_24h or 0), 1)

    await session.flush()
    return {
        "nations": created_nations,
        "users": created_users,
        "holdings": created_holdings,
        "memberships": created_memberships,
    }


def _pick_target(
    user: User,
    nations: list[Nation],
    rng: random.Random,
) -> Nation | None:
    candidates = [
        nation for nation in nations
        if nation.nation_id != user.home_nation_id
    ]
    if not candidates:
        return None

    strategy = user.ai_strategy or "balanced"
    ranked = sorted(
        candidates,
        key=lambda nation: _rate_change(nation),
        reverse=True,
    )

    if strategy == "momentum":
        pool = ranked[: min(12, len(ranked))]
    elif strategy == "value":
        pool = sorted(
            candidates,
            key=lambda nation: nation.exchange_rate,
        )[: min(12, len(candidates))]
    elif strategy == "aggressive":
        pool = sorted(
            candidates,
            key=lambda nation: abs(_rate_change(nation)),
            reverse=True,
        )[: min(12, len(candidates))]
    elif strategy == "conservative":
        median_rate = sorted(
            nation.exchange_rate for nation in candidates
        )[len(candidates) // 2]
        pool = sorted(
            candidates,
            key=lambda nation: abs(nation.exchange_rate - median_rate),
        )[: min(12, len(candidates))]
    else:
        pool = ranked[: min(18, len(ranked))]

    return rng.choice(pool)


async def _execute_buy(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    spend: Decimal,
) -> bool:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or Decimal(str(user.xr_balance or 0)) < AI_MIN_TRADE_XR:
        return False

    spend = min(spend, Decimal(str(user.xr_balance or 0)))
    if spend < AI_MIN_TRADE_XR:
        return False

    try:
        await execute_trade_action(
            session,
            user_id=user_id,
            nation_id=nation_id,
            side="buy",
            amount=spend,
        )
    except ValueError:
        return False
    return True


async def _execute_sell(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    amount: Decimal,
) -> bool:
    if amount < AI_MIN_HOLDING_TO_SELL:
        return False

    try:
        await execute_trade_action(
            session,
            user_id=user_id,
            nation_id=nation_id,
            side="sell",
            amount=amount,
        )
    except ValueError:
        return False
    return True


async def _choose_and_trade(
    session: AsyncSession,
    user: User,
    nations: list[Nation],
    now: datetime,
) -> str | None:
    bucket = int(now.timestamp() // 300)
    rng = random.Random((user.user_id << 3) ^ bucket)
    strategy = user.ai_strategy or "balanced"

    holdings_rows = (
        await session.execute(
            select(CurrencyHolding, Nation)
            .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
            .where(
                CurrencyHolding.user_id == user.user_id,
                CurrencyHolding.amount >= AI_MIN_HOLDING_TO_SELL,
                Nation.is_active.is_(True),
            )
        )
    ).all()

    foreign_holdings = [
        (holding, nation)
        for holding, nation in holdings_rows
        if nation.nation_id != user.home_nation_id
    ]

    home_holding = next(
        (
            holding
            for holding, nation in holdings_rows
            if nation.nation_id == user.home_nation_id
        ),
        None,
    )

    if (
        foreign_holdings
        and strategy != "value"
        and rng.random() < (0.50 if strategy == "aggressive" else 0.32)
    ):
        holding, nation = max(
            foreign_holdings,
            key=lambda item: _rate_change(item[1]),
        )
        fraction = Decimal(str(0.18 + rng.random() * 0.32))
        amount = (holding.amount * fraction).quantize(Decimal("0.0001"))
        if await _execute_sell(session, user.user_id, nation.nation_id, amount):
            return f"sell:{nation.currency_code}"

    locked_user = await session.get(User, user.user_id, with_for_update=True)
    if locked_user is None:
        return None

    if (
        home_holding is not None
        and Decimal(str(locked_user.xr_balance or 0)) < AI_HOME_CASH_TARGET
    ):
        fraction = Decimal(str(0.08 + rng.random() * 0.12))
        amount = (home_holding.amount * fraction).quantize(Decimal("0.0001"))
        if amount >= AI_MIN_HOLDING_TO_SELL and await _execute_sell(
            session,
            locked_user.user_id,
            locked_user.home_nation_id,
            amount,
        ):
            return "sell:home"

    target = _pick_target(locked_user, nations, rng)
    if target is None:
        return None

    cash = Decimal(str(locked_user.xr_balance or 0))
    if cash < AI_MIN_TRADE_XR:
        return None

    if strategy == "conservative":
        fraction = Decimal(str(0.04 + rng.random() * 0.05))
    elif strategy == "aggressive":
        fraction = Decimal(str(0.12 + rng.random() * 0.16))
    elif strategy == "value":
        fraction = Decimal(str(0.08 + rng.random() * 0.12))
    else:
        fraction = Decimal(str(0.06 + rng.random() * 0.10))

    spend = min(
        AI_MAX_TRADE_XR,
        max(
            AI_MIN_TRADE_XR,
            (cash * fraction).quantize(Decimal("0.01")),
        ),
    )
    if await _execute_buy(
        session,
        locked_user.user_id,
        target.nation_id,
        spend,
    ):
        return f"buy:{target.currency_code}"
    return None


async def run_ai_world_cycle(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    warmup: bool = False,
) -> int:
    """Run autonomous actions for the AI population.

    Normal cycles rotate through four batches, so every bot acts at least once
    every ~20 minutes. Warmup mode touches the full seeded population once.
    """
    current = _utc_naive(now)

    nations = (
        await session.execute(
            select(Nation).where(Nation.is_active.is_(True))
        )
    ).scalars().all()
    ai_users = (
        await session.execute(
            select(User)
            .where(
                User.is_ai.is_(True),
                User.home_nation_id.is_not(None),
            )
            .order_by(User.user_id.asc())
        )
    ).scalars().all()

    if not ai_users or not nations:
        return 0

    if warmup:
        selected = ai_users
    else:
        bucket = int(current.timestamp() // 300) % AI_BATCH_MODULO
        selected = [
            user
            for index, user in enumerate(ai_users)
            if index % AI_BATCH_MODULO == bucket
        ]

    acted = 0
    for user in selected:
        try:
            action = await _choose_and_trade(
                session,
                user,
                nations,
                current,
            )
            if action:
                acted += 1
                logger.info(
                    "AI_WORLD|%s|%s|%s",
                    user.username,
                    user.user_id,
                    action,
                )
        except Exception:
            logger.exception(
                "AI world action failed for user=%s",
                user.user_id,
            )

    await session.flush()
    return acted
