from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, NationMember, NationMemberRole, NationTreasury, TreasuryLog, User
from app.services.nation_service import get_user_active_nation, is_user_active_in_nation
from app.services.economic_event_service import record_economic_event
from app.services.user_service import sync_user_balance


MIN_DEPOSIT = Decimal("10")


async def get_member_role(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
) -> str | None:
    if not await is_user_active_in_nation(
        session,
        user_id,
        nation_id,
    ):
        return None
    role = await session.scalar(
        select(NationMember.role)
        .where(
            NationMember.user_id == user_id,
            NationMember.nation_id == nation_id,
            NationMember.is_active.is_(True),
        )
        .limit(1)
    )
    if role is None:
        return None
    return role.value if isinstance(role, NationMemberRole) else str(role)


async def _ensure_treasury(
    session: AsyncSession,
    nation_id: int,
    *,
    lock_nation: bool = True,
) -> NationTreasury | None:
    if lock_nation:
        nation = await session.get(Nation, nation_id, with_for_update=True)
        if nation is None:
            return None

    treasury = await session.scalar(
        select(NationTreasury)
        .where(NationTreasury.nation_id == nation_id)
        .with_for_update()
    )
    if treasury is None:
        treasury = NationTreasury(
            nation_id=nation_id,
            balance_xr=Decimal("0"),
            balance_local=Decimal("0"),
            total_deposited=Decimal("0"),
        )
        session.add(treasury)
        await session.flush()

    return treasury


async def get_treasury(
    session: AsyncSession,
    nation_id: int,
) -> dict | None:
    treasury = await _ensure_treasury(session, nation_id)

    if treasury is None:
        return None

    row = (
        await session.execute(
            select(
                Nation.name,
                Nation.currency_code,
                NationTreasury.balance_xr,
                NationTreasury.balance_local,
                NationTreasury.total_deposited,
                NationTreasury.last_deposit_at,
            )
            .join(
                Nation,
                Nation.nation_id == NationTreasury.nation_id,
            )
            .where(NationTreasury.nation_id == nation_id)
        )
    ).one_or_none()

    if row is None:
        return None

    return {
        "nation_name": str(row.name),
        "currency_code": str(row.currency_code),
        "balance_xr": Decimal(str(row.balance_xr)),
        "balance_local": Decimal(str(row.balance_local)),
        "total_deposited": Decimal(str(row.total_deposited)),
        "last_deposit_at": row.last_deposit_at,
    }


async def get_treasury_logs(
    session: AsyncSession,
    nation_id: int,
    limit: int = 10,
) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    rows = (
        await session.execute(
            select(
                TreasuryLog.action,
                TreasuryLog.amount_xr,
                TreasuryLog.note,
                TreasuryLog.created_at,
                User.username,
            )
            .outerjoin(
                User,
                User.user_id == TreasuryLog.actor_id,
            )
            .where(TreasuryLog.nation_id == nation_id)
            .order_by(
                TreasuryLog.created_at.desc(),
                TreasuryLog.id.desc(),
            )
            .limit(limit)
        )
    ).all()

    return [
        {
            "actor_username": row.username,
            "action": str(row.action),
            "amount_xr": Decimal(str(row.amount_xr)),
            "note": row.note,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def deposit_to_treasury(
    session: AsyncSession,
    user_id: int,
    nation_id: int,
    amount_xr: Decimal,
) -> dict:
    amount_xr = Decimal(str(amount_xr))

    if amount_xr <= 0:
        return {"ok": False, "reason": "invalid_amount"}

    nation = await session.get(Nation, nation_id, with_for_update=True)
    if nation is None or not nation.is_active:
        return {"ok": False, "reason": "nation_not_found"}

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return {"ok": False, "reason": "user_not_found"}

    if not await is_user_active_in_nation(
        session,
        user_id,
        nation_id,
        lock=True,
    ):
        return {"ok": False, "reason": "membership_required"}

    if user.xr_balance < amount_xr:
        return {
            "ok": False,
            "reason": "insufficient_balance",
            "balance": user.xr_balance,
        }

    if amount_xr < MIN_DEPOSIT:
        return {
            "ok": False,
            "reason": "below_minimum",
            "minimum": MIN_DEPOSIT,
        }

    treasury = await _ensure_treasury(session, nation_id, lock_nation=False)
    if treasury is None:
        return {"ok": False, "reason": "treasury_not_found"}

    user.xr_balance -= amount_xr
    treasury.balance_xr += amount_xr
    treasury.total_deposited += amount_xr
    treasury.last_deposit_at = datetime.utcnow()

    nation.treasury = treasury.balance_xr

    session.add(
        TreasuryLog(
            nation_id=nation_id,
            actor_id=user_id,
            action="deposit",
            amount_xr=amount_xr,
        )
    )
    record_economic_event(
        session,
        nation_id=nation_id,
        event_type="TREASURY_DEPOSIT",
        actor_id=user_id,
        amount_xr=amount_xr,
        metadata={"source": "treasury"},
    )

    await sync_user_balance(session, user_id)

    return {
        "ok": True,
        "new_balance": user.xr_balance,
        "treasury_balance": treasury.balance_xr,
    }


async def withdraw_from_treasury(
    session: AsyncSession,
    actor_id: int,
    nation_id: int,
    amount_xr: Decimal,
    note: str | None = None,
) -> dict:
    amount_xr = Decimal(str(amount_xr))

    if amount_xr <= 0:
        return {"ok": False, "reason": "invalid_amount"}

    nation = await session.get(Nation, nation_id, with_for_update=True)
    if nation is None or not nation.is_active:
        return {"ok": False, "reason": "nation_not_found"}

    if not await is_user_active_in_nation(
        session,
        actor_id,
        nation_id,
        lock=True,
    ):
        return {"ok": False, "reason": "membership_required"}

    member = await session.scalar(
        select(NationMember)
        .where(
            NationMember.user_id == actor_id,
            NationMember.nation_id == nation_id,
            NationMember.is_active.is_(True),
        )
        .with_for_update()
    )
    role = (
        member.role.value
        if member is not None and isinstance(member.role, NationMemberRole)
        else str(member.role)
        if member is not None
        else None
    )
    if role != NationMemberRole.FOUNDER.value:
        return {"ok": False, "reason": "permission_denied"}

    user = await session.get(User, actor_id, with_for_update=True)
    if user is None:
        return {"ok": False, "reason": "user_not_found"}

    treasury = await _ensure_treasury(session, nation_id, lock_nation=False)
    if treasury is None:
        return {
            "ok": False,
            "reason": "insufficient_treasury",
            "balance": Decimal("0"),
        }

    if treasury.balance_xr < amount_xr:
        return {
            "ok": False,
            "reason": "insufficient_treasury",
            "balance": treasury.balance_xr,
        }

    treasury.balance_xr -= amount_xr
    user.xr_balance += amount_xr
    nation.treasury = treasury.balance_xr

    session.add(
        TreasuryLog(
            nation_id=nation_id,
            actor_id=actor_id,
            action="withdraw",
            amount_xr=amount_xr,
            note=note,
        )
    )
    record_economic_event(
        session,
        nation_id=nation_id,
        event_type="TREASURY_WITHDRAW",
        actor_id=actor_id,
        amount_xr=amount_xr,
        metadata={"source": "treasury", "note": note},
    )

    await sync_user_balance(session, actor_id)

    return {
        "ok": True,
        "withdrawn": amount_xr,
        "treasury_balance": treasury.balance_xr,
    }


async def get_full_report(
    session: AsyncSession,
    nation_id: int,
    limit: int = 50,
) -> list[dict]:
    return await get_treasury_logs(
        session,
        nation_id,
        limit=max(1, min(int(limit), 50)),
    )
