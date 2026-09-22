from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    Transaction,
    User,
    UserActivity,
)
from app.services.market.market_pressure import record_trade_pressure
from app.services.temporal_service import get_peak_multiplier
from app.services.rules.resolver import resolve
from app.utils.formatting import calc_trade


@dataclass(frozen=True)
class WorldTradeResult:
    nation: Nation
    user: User
    holding: CurrencyHolding
    side: str
    spend: Decimal
    receive: Decimal
    fee: Decimal
    peak_multiplier: Decimal
    rate: Decimal


async def _trade_parameters(
    session: AsyncSession,
    *,
    user_id: int,
    nation_id: int,
) -> tuple[Decimal, Decimal]:
    fee_rate = Decimal(
        str(
            await resolve(
                session,
                "market.tx_fee",
                player_id=user_id,
                nation_id=nation_id,
            )
        )
    )
    peak_multiplier = Decimal(str(await get_peak_multiplier(session, user_id)))
    return fee_rate, peak_multiplier


async def execute_trade_action(
    session: AsyncSession,
    *,
    user_id: int,
    nation_id: int,
    side: str,
    amount: Decimal,
) -> WorldTradeResult:
    """Canonical market mutation used by human and AI actors.

    One successful trade updates the same world records regardless of actor:
    player cash, currency holding, nation volume, transaction history,
    player activity, and short-window market pressure.
    """
    if side not in {"buy", "sell"}:
        raise ValueError("عملیات معامله معتبر نیست.")
    if amount <= 0:
        raise ValueError("مقدار معامله باید بیشتر از صفر باشد.")

    user = await session.get(User, user_id, with_for_update=True)
    nation = await session.get(Nation, nation_id, with_for_update=True)
    if user is None or nation is None or not nation.is_active:
        raise ValueError("اطلاعات معامله پیدا نشد.")

    fee_rate, peak_multiplier = await _trade_parameters(
        session,
        user_id=user_id,
        nation_id=nation_id,
    )
    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user_id,
            CurrencyHolding.nation_id == nation_id,
        )
        .with_for_update()
    )

    rate = Decimal(str(nation.exchange_rate))

    if side == "buy":
        if Decimal(str(user.xr_balance or 0)) < amount:
            raise ValueError("موجودی کافی نیست.")

        calc = calc_trade(
            amount,
            rate,
            True,
            fee_rate_percent=fee_rate,
            benefit_multiplier=peak_multiplier,
        )
        if holding is None:
            holding = CurrencyHolding(
                user_id=user.user_id,
                nation_id=nation.nation_id,
                amount=Decimal("0"),
            )
            session.add(holding)
            await session.flush()

        user.xr_balance -= amount
        holding.amount += calc["receive"]
        spend_xr = amount
        trade_volume = amount
        pressure_volume = amount
        pressure_home_nation = user.home_nation_id
    else:
        if holding is None:
            raise ValueError("موجودی این ارز پیدا نشد.")
        if Decimal(str(holding.amount or 0)) < amount:
            raise ValueError("موجودی کافی نیست.")

        calc = calc_trade(
            amount,
            rate,
            False,
            fee_rate_percent=fee_rate,
            benefit_multiplier=peak_multiplier,
        )
        holding.amount -= amount
        user.xr_balance += calc["receive"]
        spend_xr = amount * rate
        trade_volume = spend_xr
        pressure_volume = spend_xr
        pressure_home_nation = None

    user_balance = Decimal("0")
    if user.home_nation_id == nation.nation_id:
        user_balance = Decimal(str(holding.amount))
    user.balance = user_balance
    user.xr_balance = Decimal(str(user.xr_balance or 0))

    nation.trade_volume_24h += trade_volume

    session.add(
        Transaction(
            user_id=user.user_id,
            nation_id=nation.nation_id,
            transaction_type=side,
            spend_xr=spend_xr,
            amount=Decimal(str(calc["receive"])) if side == "buy" else amount,
            fee_xr=calc["fee"],
            rate=rate,
        )
    )
    session.add(
        UserActivity(
            user_id=user.user_id,
            nation_id=nation.nation_id,
            activity_type=ActivityType.TRADE,
        )
    )

    await record_trade_pressure(
        nation_id=nation.nation_id,
        side=side,
        volume=pressure_volume,
        buyer_home_nation_id=pressure_home_nation,
    )

    return WorldTradeResult(
        nation=nation,
        user=user,
        holding=holding,
        side=side,
        spend=amount,
        receive=Decimal(str(calc["receive"])),
        fee=Decimal(str(calc["fee"])),
        peak_multiplier=peak_multiplier,
        rate=rate,
    )
