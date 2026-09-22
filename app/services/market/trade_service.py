from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, TradePreview, User
from app.database.session import async_session
from app.services.mission_service import increment_mission
from app.services.world_action_service import execute_trade_action
from app.services.temporal_service import get_peak_multiplier
from app.services.user_service import sync_user_balance
from app.services.rules.resolver import resolve
from app.utils.formatting import calc_trade


@dataclass(frozen=True)
class TradePreviewResult:
    nation: Nation
    user: User
    side: str
    spend: Decimal
    receive: Decimal
    fee: Decimal
    fee_rate: Decimal
    peak_multiplier: Decimal
    current_holding: Decimal
    preview_rate: Decimal


@dataclass(frozen=True)
class TradeExecutionResult:
    nation: Nation
    user: User
    holding: CurrencyHolding
    side: str
    spend: Decimal
    receive: Decimal
    fee: Decimal
    peak_multiplier: Decimal
    rate: Decimal


def parse_trade_amount(raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip().replace("٬", "").replace(",", "").replace("٫", "."))
    except InvalidOperation as exc:
        raise ValueError("فقط عدد وارد کن.") from exc
    if value.is_nan() or value.is_infinite():
        raise ValueError("مقدار معامله معتبر نیست.")
    return value


async def _trade_parameters(session: AsyncSession, *, user_id: int, nation_id: int) -> tuple[Decimal, Decimal]:
    fee_rate = Decimal(str(await resolve(session, "market.tx_fee", player_id=user_id, nation_id=nation_id)))
    peak_multiplier = Decimal(str(await get_peak_multiplier(session, user_id)))
    return fee_rate, peak_multiplier


async def prepare_buy_preview(session: AsyncSession, *, user_id: int, nation_id: int, spend: Decimal) -> TradePreviewResult:
    if spend < Decimal("10"):
        raise ValueError("حداقل مقدار خرید 10 دلار است.")
    user = await session.get(User, user_id)
    nation = await session.get(Nation, nation_id)
    if user is None or nation is None or not nation.is_active:
        raise ValueError("اطلاعات معامله پیدا نشد.")
    if Decimal(str(user.xr_balance or 0)) < spend:
        raise ValueError(f"موجودی کافی نیست. موجودی: {user.xr_balance} دلار")
    fee_rate, peak_multiplier = await _trade_parameters(session, user_id=user_id, nation_id=nation_id)
    calc = calc_trade(spend, nation.exchange_rate, True, fee_rate_percent=fee_rate, benefit_multiplier=peak_multiplier)
    holding = await session.scalar(select(CurrencyHolding).where(
        CurrencyHolding.user_id == user_id, CurrencyHolding.nation_id == nation_id
    ))
    current = Decimal(str(holding.amount if holding is not None else 0))
    session.add(TradePreview(user_id=user_id, nation_id=nation_id, side="buy", spend=spend, preview_rate=nation.exchange_rate))
    return TradePreviewResult(nation, user, "buy", spend, Decimal(str(calc["receive"])), Decimal(str(calc["fee"])), fee_rate, peak_multiplier, current, nation.exchange_rate)


async def execute_buy(session: AsyncSession, *, user_id: int, nation_id: int, spend: Decimal) -> TradeExecutionResult:
    user = await session.get(User, user_id, with_for_update=True)
    nation = await session.get(Nation, nation_id, with_for_update=True)
    preview = await session.scalar(
        select(TradePreview)
        .where(
            TradePreview.user_id == user_id,
            TradePreview.nation_id == nation_id,
            TradePreview.side == "buy",
            TradePreview.spend == spend,
        )
        .order_by(TradePreview.created_at.desc())
        .limit(1)
    ) if user is not None else None

    if user is None or nation is None or not nation.is_active or preview is None:
        raise ValueError("پیش‌نمایش منقضی شد. دوباره مقدار را وارد کن.")
    if (
        preview.preview_rate <= 0
        or abs(nation.exchange_rate - preview.preview_rate) / preview.preview_rate > Decimal("0.01")
    ):
        raise ValueError("نرخ تغییر کرده است. یک پیش‌نمایش جدید بگیر.")

    result = await execute_trade_action(
        session,
        user_id=user_id,
        nation_id=nation_id,
        side="buy",
        amount=spend,
    )
    await session.delete(preview)

    for key, mission_amount in (
        ("DAILY_TRADE_1", 1),
        ("WEEKLY_BUY_5", 1),
        ("WEEKLY_TRADE_VOLUME", int(result.receive)),
        ("FIRST_TRADE", 1),
    ):
        try:
            await increment_mission(session, user_id, key, amount=mission_amount)
        except Exception:
            continue

    return TradeExecutionResult(
        result.nation,
        result.user,
        result.holding,
        "buy",
        result.spend,
        result.receive,
        result.fee,
        result.peak_multiplier,
        result.rate,
    )


async def prepare_sell_preview(session: AsyncSession, *, user_id: int, nation_id: int, amount: Decimal) -> TradePreviewResult:
    if amount <= 0:
        raise ValueError("مقدار باید بیشتر از صفر باشد.")
    nation = await session.get(Nation, nation_id)
    user = await session.get(User, user_id)
    holding = await session.scalar(select(CurrencyHolding).where(CurrencyHolding.user_id == user_id, CurrencyHolding.nation_id == nation_id))
    if nation is None or user is None or not nation.is_active or holding is None:
        raise ValueError("موجودی این ارز پیدا نشد.")
    if Decimal(str(holding.amount or 0)) < amount:
        raise ValueError("موجودی کافی نیست.")
    fee_rate, peak_multiplier = await _trade_parameters(session, user_id=user_id, nation_id=nation_id)
    calc = calc_trade(amount, nation.exchange_rate, False, fee_rate_percent=fee_rate, benefit_multiplier=peak_multiplier)
    session.add(TradePreview(user_id=user_id, nation_id=nation_id, side="sell", spend=amount, preview_rate=nation.exchange_rate))
    return TradePreviewResult(nation, user, "sell", amount, Decimal(str(calc["receive"])), Decimal(str(calc["fee"])), fee_rate, peak_multiplier, Decimal(str(holding.amount)), nation.exchange_rate)


async def execute_sell(session: AsyncSession, *, user_id: int, nation_id: int, amount: Decimal) -> TradeExecutionResult:
    user = await session.get(User, user_id, with_for_update=True)
    nation = await session.get(Nation, nation_id, with_for_update=True)
    preview = await session.scalar(
        select(TradePreview)
        .where(
            TradePreview.user_id == user_id,
            TradePreview.nation_id == nation_id,
            TradePreview.side == "sell",
            TradePreview.spend == amount,
        )
        .order_by(TradePreview.created_at.desc())
        .limit(1)
    ) if user is not None else None

    if user is None or nation is None or not nation.is_active or preview is None:
        raise ValueError("پیش‌نمایش منقضی شد. دوباره مقدار را وارد کن.")
    if (
        preview.preview_rate <= 0
        or abs(nation.exchange_rate - preview.preview_rate) / preview.preview_rate > Decimal("0.01")
    ):
        raise ValueError("نرخ تغییر کرده است. یک پیش‌نمایش جدید بگیر.")

    result = await execute_trade_action(
        session,
        user_id=user_id,
        nation_id=nation_id,
        side="sell",
        amount=amount,
    )
    await session.delete(preview)

    for key, mission_amount in (
        ("DAILY_TRADE_1", 1),
        ("WEEKLY_TRADE_VOLUME", int(amount)),
        ("FIRST_TRADE", 1),
    ):
        try:
            await increment_mission(session, user_id, key, amount=mission_amount)
        except Exception:
            continue

    return TradeExecutionResult(
        result.nation,
        result.user,
        result.holding,
        "sell",
        result.spend,
        result.receive,
        result.fee,
        result.peak_multiplier,
        result.rate,
    )


async def prepare_buy_preview_for_user(*, user_id: int, nation_id: int, spend: Decimal) -> TradePreviewResult:
    async with async_session() as session:
        async with session.begin():
            return await prepare_buy_preview(session, user_id=user_id, nation_id=nation_id, spend=spend)


async def execute_buy_for_user(*, user_id: int, nation_id: int, spend: Decimal) -> TradeExecutionResult:
    async with async_session() as session:
        async with session.begin():
            return await execute_buy(session, user_id=user_id, nation_id=nation_id, spend=spend)


async def prepare_sell_preview_for_user(*, user_id: int, nation_id: int, amount: Decimal) -> TradePreviewResult:
    async with async_session() as session:
        async with session.begin():
            return await prepare_sell_preview(session, user_id=user_id, nation_id=nation_id, amount=amount)


async def execute_sell_for_user(*, user_id: int, nation_id: int, amount: Decimal) -> TradeExecutionResult:
    async with async_session() as session:
        async with session.begin():
            return await execute_sell(session, user_id=user_id, nation_id=nation_id, amount=amount)
