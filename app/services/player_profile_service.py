from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationLog,
    NationWar,
    Proposal,
    Transaction,
    User,
    UserLessonProgress,
    UserXP,
    Vote,
)


@dataclass(frozen=True)
class PlayerProfile:
    username: str
    nation_name: str | None
    nation_currency: str | None
    wealth_xr: Decimal
    trades: int
    trade_volume: Decimal
    governance_actions: int
    lessons_completed: int
    wars_seen: int
    archetype: str
    reputation_trade: int
    reputation_governance: int
    reputation_knowledge: int
    reputation_military: int
    reputation_builder: int


async def get_player_profile(
    session: AsyncSession,
    user_id: int,
) -> PlayerProfile:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("پروفایل کاربر پیدا نشد.")

    since = datetime.now(UTC) - timedelta(days=30)

    trade_result = await session.execute(
        select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.spend_xr), 0),
        ).where(
            Transaction.user_id == user_id,
            Transaction.created_at >= since,
        )
    )
    trade_row = trade_result.one()
    trades = int(trade_row[0] or 0)
    volume = Decimal(str(trade_row[1] or 0))

    governance_actions = int(
        await session.scalar(
            select(func.count(Proposal.id)).where(
                Proposal.proposer_player_id == user_id,
                Proposal.created_at >= since,
            )
        )
        or 0
    )
    governance_actions += int(
        await session.scalar(
            select(func.count(Vote.id)).where(
                Vote.player_id == user_id,
                Vote.created_at >= since,
            )
        )
        or 0
    )

    lessons_completed = int(
        await session.scalar(
            select(func.count(UserLessonProgress.id)).where(
                UserLessonProgress.user_id == user_id,
                UserLessonProgress.status == "done",
            )
        )
        or 0
    )

    nation = None
    nation_wars = 0
    nation_logs = 0
    if user.home_nation_id is not None:
        nation = await session.get(Nation, user.home_nation_id)
        nation_wars = int(
            await session.scalar(
                select(func.count(NationWar.id)).where(
                    (NationWar.nation_id == user.home_nation_id)
                    | (NationWar.opponent_nation_id == user.home_nation_id),
                    NationWar.declared_at >= since,
                )
            )
            or 0
        )
        nation_logs = int(
            await session.scalar(
                select(func.count(NationLog.id)).where(
                    NationLog.nation_id == user.home_nation_id,
                    NationLog.created_at >= since,
                    NationLog.actor_id == user_id,
                )
            )
            or 0
        )

    xp = await session.scalar(
        select(UserXP.total_xp).where(UserXP.user_id == user_id)
    )
    holdings_value = await session.scalar(
        select(
            func.coalesce(
                func.sum(CurrencyHolding.amount * Nation.exchange_rate),
                0,
            )
        )
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(
            CurrencyHolding.user_id == user_id,
            Nation.is_active.is_(True),
        )
    )
    wealth = Decimal(str(user.xr_balance or 0)) + Decimal(str(holdings_value or 0))

    reputation_trade = min(
        100,
        int(
            (Decimal(trades) * Decimal("4")).sqrt() * Decimal("10")
            if trades
            else 0
        ),
    )
    reputation_trade = min(
        100,
        reputation_trade + int((volume / Decimal("1000")).sqrt() * Decimal("5")),
    )
    reputation_governance = min(
        100,
        int(Decimal(governance_actions + nation_logs).sqrt() * Decimal("20")),
    )
    reputation_knowledge = min(
        100,
        int((Decimal(lessons_completed) ** Decimal("0.5")) * Decimal("25"))
        + min(25, int(Decimal(str(xp or 0)) / Decimal("100"))),
    )
    reputation_military = min(
        100,
        nation_wars * 15 + (10 if nation is not None and nation.founder_user_id == user_id else 0),
    )
    reputation_builder = min(
        100,
        nation_logs * 8 + (20 if nation is not None and nation.founder_user_id == user_id else 0),
    )

    scores = {
        "معامله‌گر": reputation_trade,
        "حاکم": reputation_governance,
        "نوآور": reputation_knowledge,
        "جنگ‌سالار": reputation_military,
        "سازنده": reputation_builder,
    }
    archetype = max(
        scores.items(),
        key=lambda item: (item[1], {"معامله‌گر": 5, "حاکم": 4, "نوآور": 3, "جنگ‌سالار": 2, "سازنده": 1}[item[0]]),
    )[0]

    return PlayerProfile(
        username=user.username,
        nation_name=nation.name if nation is not None else None,
        nation_currency=nation.currency_code if nation is not None else None,
        wealth_xr=wealth,
        trades=trades,
        trade_volume=volume,
        governance_actions=governance_actions,
        lessons_completed=lessons_completed,
        wars_seen=nation_wars,
        archetype=archetype,
        reputation_trade=reputation_trade,
        reputation_governance=reputation_governance,
        reputation_knowledge=reputation_knowledge,
        reputation_military=reputation_military,
        reputation_builder=reputation_builder,
    )
