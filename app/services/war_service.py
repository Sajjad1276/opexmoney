from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_session
from app.database.models import (
    Nation,
    NationLog,
    NationMember,
    NationMemberRole,
    NationTreasury,
    NationWar,
    TreasuryLog,
)

logger = logging.getLogger(__name__)

WAR_DURATION = timedelta(hours=48)
REPARATION_RATE = Decimal("0.10")
ACTIVE_STATUS = "active"
ENDED_STATUS = "ended"
DRAW_STATUS = "draw"

WAR_DECLARED_ACTION = "WAR_DECLARED"
WAR_ENDED_ACTION = "WAR_ENDED"
DRAW_ACTION = "DRAW"


def _now() -> datetime:
    return datetime.utcnow()


def _role_value(role: NationMemberRole | str) -> str:
    return role.value if isinstance(role, NationMemberRole) else str(role)


async def _ensure_treasury(
    session: AsyncSession,
    nation: Nation,
) -> NationTreasury:
    treasury = await session.scalar(
        select(NationTreasury)
        .where(NationTreasury.nation_id == nation.nation_id)
        .with_for_update()
    )
    if treasury is None:
        treasury = NationTreasury(
            nation_id=nation.nation_id,
            balance_xr=Decimal(str(nation.treasury or Decimal("0"))),
            balance_local=Decimal("0"),
            total_deposited=Decimal("0"),
        )
        session.add(treasury)
        await session.flush()
    return treasury


async def _send_to_groups(
    bot: Bot | None,
    group_ids: list[int | None],
    text: str,
) -> None:
    if bot is None:
        return

    for group_id in dict.fromkeys(group_id for group_id in group_ids if group_id is not None):
        try:
            await bot.send_message(group_id, text, parse_mode="HTML")
        except Exception:
            logger.exception("Could not send war notification to group %s", group_id)


async def declare_war(
    declaring_nation_id: int,
    target_nation_id: int,
    session: AsyncSession,
    *,
    actor_user_id: int | None = None,
    bot: Bot | None = None,
) -> dict:
    """Declare a 48-hour war between two active nations.

    The caller may omit actor_user_id for compatibility. In that case the
    declaring nation's founder is used. The real UI flow always passes the
    authenticated founder/minister id.
    """
    if declaring_nation_id == target_nation_id:
        raise ValueError("⛔ یک ملت نمی‌تواند با خودش وارد جنگ شود.")

    async with session.begin():
        nation_ids = sorted({declaring_nation_id, target_nation_id})
        nations = (
            await session.execute(
                select(Nation)
                .where(Nation.nation_id.in_(nation_ids))
                .order_by(Nation.nation_id.asc())
                .with_for_update()
            )
        ).scalars().all()
        nations_by_id = {nation.nation_id: nation for nation in nations}

        declaring_nation = nations_by_id.get(declaring_nation_id)
        target_nation = nations_by_id.get(target_nation_id)

        if declaring_nation is None or not declaring_nation.is_active:
            raise ValueError("⚠️ ملت مهاجم پیدا نشد یا فعال نیست.")
        if target_nation is None or not target_nation.is_active:
            raise ValueError("⚠️ ملت هدف پیدا نشد یا فعال نیست.")

        actor_user_id = actor_user_id or declaring_nation.founder_user_id
        if actor_user_id is None:
            raise ValueError("⛔ بنیان‌گذار ملت مشخص نیست.")

        actor = await session.scalar(
            select(NationMember)
            .where(
                NationMember.nation_id == declaring_nation_id,
                NationMember.user_id == actor_user_id,
                NationMember.is_active.is_(True),
            )
            .with_for_update()
        )
        if actor is None or _role_value(actor.role) not in {
            NationMemberRole.FOUNDER.value,
            NationMemberRole.MINISTER.value,
        }:
            raise ValueError("⛔ فقط بنیان‌گذار و وزیر می‌توانند جنگ اعلام کنند.")

        existing_war = await session.scalar(
            select(NationWar)
            .where(
                NationWar.status == ACTIVE_STATUS,
                or_(
                    and_(
                        NationWar.nation_id == declaring_nation_id,
                        NationWar.opponent_nation_id == target_nation_id,
                    ),
                    and_(
                        NationWar.nation_id == target_nation_id,
                        NationWar.opponent_nation_id == declaring_nation_id,
                    ),
                ),
            )
            .with_for_update()
            .limit(1)
        )
        if existing_war is not None:
            raise ValueError("⚔️ این دو ملت همین حالا در حال جنگ هستند.")

        declared_at = _now()
        war = NationWar(
            nation_id=declaring_nation_id,
            opponent_nation_id=target_nation_id,
            status=ACTIVE_STATUS,
            declared_at=declared_at,
            ends_at=declared_at + WAR_DURATION,
        )
        session.add(war)
        await session.flush()

        await _append_war_log(
            session,
            nation_id=declaring_nation_id,
            actor_id=actor_user_id,
            action_type=WAR_DECLARED_ACTION,
            metadata={
                "opponent_nation_id": target_nation_id,
                "opponent_name": target_nation.name,
                "war_id": war.id,
                "ends_at": war.ends_at.isoformat(),
            },
        )
        await _append_war_log(
            session,
            nation_id=target_nation_id,
            actor_id=actor_user_id,
            action_type=WAR_DECLARED_ACTION,
            metadata={
                "opponent_nation_id": declaring_nation_id,
                "opponent_name": declaring_nation.name,
                "war_id": war.id,
                "ends_at": war.ends_at.isoformat(),
            },
        )

        result = {
            "war_id": war.id,
            "declaring_nation_id": declaring_nation_id,
            "target_nation_id": target_nation_id,
            "declaring_name": declaring_nation.name,
            "target_name": target_nation.name,
            "declared_at": declared_at,
            "ends_at": war.ends_at,
            "declaring_group_id": declaring_nation.group_id,
            "target_group_id": target_nation.group_id,
        }

    await _send_to_groups(
        bot,
        [result["declaring_group_id"], result["target_group_id"]],
        (
            "⚔️ <b>جنگ اعلام شد</b>\n\n"
            f"🏴 <b>{html.escape(result['declaring_name'])}</b> "
            f"علیه <b>{html.escape(result['target_name'])}</b> وارد جنگ شد.\n"
            f"⏳ مدت جنگ: <b>48 ساعت</b>\n"
            f"🏁 پایان: <b>{result['ends_at'].strftime('%Y-%m-%d %H:%M')} UTC</b>\n\n"
            "📈 در زمان پایان، ملتی که نرخ ارز بالاتری داشته باشد برنده است.\n"
            "💰 خسارت جنگی: 10٪ از خزانه ملت بازنده."
        ),
    )
    return result


async def resolve_war(
    war_id: int,
    session: AsyncSession,
    *,
    bot: Bot | None = None,
) -> dict | None:
    """Resolve one war atomically and pay the 10% treasury reparation."""
    async with session.begin():
        initial_war = await session.get(NationWar, war_id)
        if initial_war is None:
            return None

        nation_ids = sorted({initial_war.nation_id, initial_war.opponent_nation_id})
        nations = (
            await session.execute(
                select(Nation)
                .where(Nation.nation_id.in_(nation_ids))
                .order_by(Nation.nation_id.asc())
                .with_for_update()
            )
        ).scalars().all()
        nations_by_id = {nation.nation_id: nation for nation in nations}

        war = await session.scalar(
            select(NationWar)
            .where(NationWar.id == war_id)
            .with_for_update()
        )
        if war is None:
            return None
        if war.status != ACTIVE_STATUS:
            return None

        declaring_nation = nations_by_id.get(war.nation_id)
        opponent_nation = nations_by_id.get(war.opponent_nation_id)
        if declaring_nation is None or opponent_nation is None:
            raise ValueError("⚠️ یکی از ملت‌های جنگ پیدا نشد.")

        declaring_treasury = await _ensure_treasury(session, declaring_nation)
        opponent_treasury = await _ensure_treasury(session, opponent_nation)

        declaring_rate = Decimal(str(declaring_nation.exchange_rate or Decimal("0")))
        opponent_rate = Decimal(str(opponent_nation.exchange_rate or Decimal("0")))

        winner: Nation | None
        loser: Nation | None
        reparation = Decimal("0")

        if declaring_rate == opponent_rate:
            winner = None
            loser = None
            war.status = DRAW_STATUS
            action_type = DRAW_ACTION
        else:
            winner = declaring_nation if declaring_rate > opponent_rate else opponent_nation
            loser = opponent_nation if winner.nation_id == declaring_nation.nation_id else declaring_nation
            winner_treasury = (
                declaring_treasury
                if winner.nation_id == declaring_nation.nation_id
                else opponent_treasury
            )
            loser_treasury = (
                declaring_treasury
                if loser.nation_id == declaring_nation.nation_id
                else opponent_treasury
            )

            available = Decimal(str(loser_treasury.balance_xr or Decimal("0")))
            requested = (available * REPARATION_RATE).quantize(Decimal("0.0001"))
            reparation = min(available, requested)

            if reparation > 0:
                loser_treasury.balance_xr = available - reparation
                winner_treasury.balance_xr = (
                    Decimal(str(winner_treasury.balance_xr or Decimal("0"))) + reparation
                )
                winner_treasury.total_deposited = (
                    Decimal(str(winner_treasury.total_deposited or Decimal("0"))) + reparation
                )
                winner_treasury.last_deposit_at = _now()

                session.add(
                    TreasuryLog(
                        nation_id=loser.nation_id,
                        actor_id=None,
                        action="withdraw",
                        amount_xr=reparation,
                        note=f"war_reparation:war={war.id}",
                    )
                )
                session.add(
                    TreasuryLog(
                        nation_id=winner.nation_id,
                        actor_id=None,
                        action="deposit",
                        amount_xr=reparation,
                        note=f"war_reparation:war={war.id}",
                    )
                )

            declaring_nation.treasury = declaring_treasury.balance_xr
            opponent_nation.treasury = opponent_treasury.balance_xr
            war.status = ENDED_STATUS
            action_type = WAR_ENDED_ACTION

        if winner is None:
            declaring_nation.treasury = declaring_treasury.balance_xr
            opponent_nation.treasury = opponent_treasury.balance_xr

        ended_at = _now()
        war.ended_at = ended_at

        winner_id = winner.nation_id if winner is not None else None
        loser_id = loser.nation_id if loser is not None else None
        winner_name = winner.name if winner is not None else None
        loser_name = loser.name if loser is not None else None

        metadata = {
            "war_id": war.id,
            "winner": winner_id,
            "winner_name": winner_name,
            "loser": loser_id,
            "loser_name": loser_name,
            "reparation_amount": str(reparation),
            "declaring_rate": str(declaring_rate),
            "opponent_rate": str(opponent_rate),
            "status": war.status,
            "ended_at": ended_at.isoformat(),
        }

        await _append_war_log(
            session,
            nation_id=declaring_nation.nation_id,
            actor_id=None,
            action_type=action_type,
            metadata={
                **metadata,
                "opponent_name": opponent_nation.name,
            },
        )
        await _append_war_log(
            session,
            nation_id=opponent_nation.nation_id,
            actor_id=None,
            action_type=action_type,
            metadata={
                **metadata,
                "opponent_name": declaring_nation.name,
            },
        )

        result = {
            "war_id": war.id,
            "status": war.status,
            "winner_id": winner_id,
            "winner_name": winner_name,
            "loser_id": loser_id,
            "loser_name": loser_name,
            "reparation_amount": reparation,
            "declaring_nation_id": declaring_nation.nation_id,
            "opponent_nation_id": opponent_nation.nation_id,
            "declaring_name": declaring_nation.name,
            "opponent_name": opponent_nation.name,
            "declaring_rate": declaring_rate,
            "opponent_rate": opponent_rate,
            "declaring_group_id": declaring_nation.group_id,
            "opponent_group_id": opponent_nation.group_id,
            "ended_at": ended_at,
        }

    if result["status"] == DRAW_STATUS:
        message = (
            "🤝 <b>جنگ به تساوی رسید</b>\n\n"
            f"🏴 <b>{html.escape(result['declaring_name'])}</b> و "
            f"<b>{html.escape(result['opponent_name'])}</b> "
            "در زمان پایان نرخ ارز یکسان داشتند.\n"
            "💰 هیچ غرامتی منتقل نشد."
        )
    else:
        winner_rate = (
            result["declaring_rate"]
            if result["winner_id"] == result["declaring_nation_id"]
            else result["opponent_rate"]
        )
        message = (
            "🏆 <b>نتیجه جنگ</b>\\n\\n"
            f"🥇 برنده: <b>{html.escape(result['winner_name'])}</b>\\n"
            f"💱 نرخ برنده: <b>{winner_rate}</b> ΩXR\\n"
            f"💸 غرامت منتقل‌شده: <b>{result['reparation_amount']}</b> ΩXR"
        )

    await _send_to_groups(
        bot,
        [result["declaring_group_id"], result["opponent_group_id"]],
        message,
    )
    return result


async def _append_war_log(
    session: AsyncSession,
    *,
    nation_id: int,
    actor_id: int | None,
    action_type: str,
    metadata: dict,
) -> NationLog:
    log = NationLog(
        nation_id=nation_id,
        actor_id=actor_id,
        action_type=action_type,
        event_metadata=metadata,
    )
    session.add(log)
    await session.flush()
    return log


async def resolve_expired_wars(bot: Bot | None = None) -> int:
    now = _now()

    async with async_session() as session:
        war_ids = list(
            (
                await session.execute(
                    select(NationWar.id)
                    .where(
                        NationWar.status == ACTIVE_STATUS,
                        NationWar.ends_at <= now,
                    )
                    .order_by(NationWar.ends_at.asc(), NationWar.id.asc())
                    .limit(100)
                )
            ).scalars().all()
        )

    resolved = 0
    for war_id in war_ids:
        async with async_session() as session:
            try:
                result = await resolve_war(war_id, session, bot=bot)
                if result is not None:
                    resolved += 1
            except Exception:
                logger.exception("War resolution failed for war %s", war_id)

    return resolved
