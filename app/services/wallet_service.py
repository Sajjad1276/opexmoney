from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, User


@dataclass(frozen=True)
class WalletSnapshot:
    user_id: int
    nation_id: int | None
    local_balance: Decimal
    xr_balance: Decimal


async def snapshot_user_wallet(
    session: AsyncSession,
    user_id: int,
    *,
    lock: bool = False,
) -> WalletSnapshot:
    statement = select(User).where(User.user_id == user_id).limit(1)
    if lock:
        statement = statement.with_for_update()
    user = await session.scalar(statement)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    local_balance = Decimal("0")
    if user.home_nation_id is not None:
        holding_stmt = (
            select(CurrencyHolding.amount)
            .where(
                CurrencyHolding.user_id == user_id,
                CurrencyHolding.nation_id == user.home_nation_id,
            )
            .limit(1)
        )
        if lock:
            holding_stmt = holding_stmt.with_for_update()
        holding_amount = await session.scalar(holding_stmt)
        if holding_amount is not None:
            local_balance = Decimal(str(holding_amount))

    return WalletSnapshot(
        user_id=user_id,
        nation_id=user.home_nation_id,
        local_balance=local_balance,
        xr_balance=Decimal(str(user.xr_balance or Decimal("0"))),
    )


async def reconcile_user_wallet(
    session: AsyncSession,
    user_id: int,
) -> WalletSnapshot:
    snapshot = await snapshot_user_wallet(session, user_id, lock=True)
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    user.balance = snapshot.local_balance
    user.xr_balance = snapshot.xr_balance
    await session.flush()
    return snapshot


async def wallet_is_consistent(
    session: AsyncSession,
    user_id: int,
) -> bool:
    snapshot = await snapshot_user_wallet(session, user_id, lock=False)
    user = await session.get(User, user_id)
    if user is None:
        return False
    return Decimal(str(user.balance or Decimal("0"))) == snapshot.local_balance
