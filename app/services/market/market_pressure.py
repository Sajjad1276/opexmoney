from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, RateHistory
from app.services.market.market_state import CurrencyMarketState, get_currency_state
from config import settings

logger = logging.getLogger(__name__)

PRESSURE_TTL_SECONDS = 900


@dataclass(frozen=True)
class PriceImpact:
    base_rate: Decimal
    adjusted_rate: Decimal
    impact_pct: Decimal
    cause: str
    market_state: CurrencyMarketState


@dataclass(frozen=True)
class ReceiptLine:
    label: str
    impact_pct: Decimal
    emoji: str


@dataclass(frozen=True)
class PriceReceipt:
    currency_code: str
    period_label: str
    total_change_pct: Decimal
    lines: list[ReceiptLine]
    summary: str


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _fa_digits(value: int) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


async def record_trade_pressure(
    *,
    nation_id: int,
    side: str,
    volume: Decimal,
    buyer_home_nation_id: int | None = None,
) -> None:
    if side not in {"buy", "sell"} or volume <= 0:
        return

    try:
        from app.core.redis import get_redis

        redis = get_redis()
        if redis is None:
            return

        key = f"pressure:{int(nation_id)}"

        # Redis HINCRBY only accepts integers. HINCRBYFLOAT preserves the Decimal
        # trade volume without rounding it to whole dollars.
        field = "buy_volume" if side == "buy" else "sell_volume"
        await redis.hincrbyfloat(key, field, str(volume))

        if (
            side == "buy"
            and buyer_home_nation_id is not None
            and buyer_home_nation_id != nation_id
        ):
            await redis.hincrbyfloat(
                key,
                "foreign_buy",
                str(volume),
            )

        await redis.hincrby(key, "tx_count", 1)
        await redis.hset(key, "last_update", str(int(time.time())))

        # Refresh the rolling 15-minute window after every successful trade.
        try:
            await redis.expire(key, PRESSURE_TTL_SECONDS)
        except Exception:
            logger.debug(
                "Could not refresh pressure TTL for nation=%s",
                nation_id,
                exc_info=True,
            )
    except Exception:
        logger.exception(
            "Market pressure Redis update failed for nation=%s",
            nation_id,
        )


async def _read_pressure_signals(
    redis,
    nation_id: int,
) -> tuple[Decimal, Decimal]:
    if redis is None:
        return Decimal("0"), Decimal("0")

    try:
        pressure = await redis.hgetall(f"pressure:{int(nation_id)}")
        if not isinstance(pressure, dict):
            return Decimal("0"), Decimal("0")

        buy_vol = _to_decimal(pressure.get("buy_volume"))
        sell_vol = _to_decimal(pressure.get("sell_volume"))
        foreign = _to_decimal(pressure.get("foreign_buy"))

        pressure_signal = (
            (buy_vol - sell_vol)
            / max(buy_vol + sell_vol, Decimal("1"))
        )
        foreign_signal = foreign / max(buy_vol, Decimal("1"))

        return (
            _clamp(pressure_signal, Decimal("-1"), Decimal("1")),
            max(Decimal("0"), foreign_signal),
        )
    except Exception:
        return Decimal("0"), Decimal("0")


def pressure_signal_from_state(state: CurrencyMarketState) -> Decimal:
    return _clamp(
        (state.buy_pressure - state.sell_pressure)
        / max(
            state.buy_pressure + state.sell_pressure,
            Decimal("1"),
        ),
        Decimal("-1"),
        Decimal("1"),
    )


def _impact_for_ratio(ratio: Decimal) -> Decimal:
    if ratio <= 0:
        return Decimal("0")
    if ratio < Decimal("0.01"):
        return (ratio * Decimal("9")).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
    span = (ratio - Decimal("0.01")) / Decimal("0.09")
    impact = Decimal("0.09") + span * Decimal("1.91")
    return min(Decimal("2"), impact).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )


async def calculate_price_impact(
    session: AsyncSession,
    nation_id: int,
    action: str,
    amount: Decimal,
) -> PriceImpact:
    if action not in {"buy", "sell"}:
        raise ValueError("عملیات معامله باید buy یا sell باشد.")
    if amount <= 0:
        raise ValueError("مقدار معامله باید بیشتر از صفر باشد.")

    nation = await session.get(Nation, nation_id)
    if nation is None or not nation.is_active:
        raise ValueError("ملت فعال پیدا نشد.")

    state = await get_currency_state(session, nation_id=nation_id)
    base_rate = Decimal(str(nation.exchange_rate))
    liquidity = state.liquidity
    trade_units = amount / base_rate if action == "buy" and base_rate > 0 else amount
    ratio = trade_units / liquidity if liquidity > 0 else Decimal("1")
    impact_pct = _impact_for_ratio(ratio)
    direction = Decimal("1") if action == "buy" else Decimal("-1")
    adjusted_rate = (
        base_rate
        * (Decimal("1") + direction * impact_pct / Decimal("100"))
    ).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )

    cause = (
        f"فشار خرید؛ اثر تخمینی این معامله {impact_pct}٪ است."
        if action == "buy"
        else f"فشار فروش؛ اثر تخمینی این معامله {impact_pct}٪ است."
    )

    return PriceImpact(
        base_rate=base_rate,
        adjusted_rate=adjusted_rate,
        impact_pct=impact_pct,
        cause=cause,
        market_state=state,
    )


_RECEIPT_SPECS = (
    (
        "activity_score",
        "فعالیت اعضای ملت",
        Decimal("0.30"),
    ),
    (
        "trade_score",
        "حجم معاملات",
        Decimal("0.25"),
    ),
    (
        "growth_score",
        "رشد جمعیت ملت",
        Decimal("0.20"),
    ),
    (
        "pressure_signal",
        "فشار بازار",
        Decimal("0.15"),
    ),
    (
        "foreign_signal",
        "تقاضای خارجی",
        Decimal("0.10"),
    ),
)


def _label_for_factor(
    factor_name: str,
    value: Decimal,
) -> str:
    if factor_name == "pressure_signal":
        return "تقاضای خرید" if value > 0 else "فشار فروش" if value < 0 else "فشار بازار"
    return dict(
        (name, label)
        for name, label, _ in _RECEIPT_SPECS
    )[factor_name]


def _emoji_for_value(value: Decimal) -> str:
    if value > 0:
        return "📈"
    if value < 0:
        return "📉"
    return "➡️"


def _summary(
    currency_code: str,
    dominant_cause: str,
    factors: dict[str, Decimal],
) -> str:
    value = factors.get(dominant_cause, Decimal("0"))

    if dominant_cause == "activity_score":
        return f"رشد فعالیت اعضا باعث تقویت {currency_code} شد"

    if dominant_cause == "trade_score":
        return f"حجم معاملات باعث تقویت {currency_code} شد"

    if dominant_cause == "growth_score":
        if value >= 0:
            return f"رشد جمعیت ملت باعث تقویت {currency_code} شد"
        return f"افت جمعیت ملت {currency_code} را تضعیف کرد"

    if dominant_cause == "pressure_signal":
        if value > 0:
            return f"موج خرید قیمت {currency_code} رو بالا برد"
        if value < 0:
            return f"فشار فروش قیمت {currency_code} رو پایین آورد"
        return f"فشار بازار اثر معناداری روی {currency_code} نداشت"

    if dominant_cause == "foreign_signal":
        return f"تقاضای خارجی {currency_code} را تقویت کرد"

    return f"عوامل بازار روی {currency_code} اثر گذاشتند"


async def build_price_receipt(
    session: AsyncSession,
    nation_id: int,
    last_n: int = 3,
) -> PriceReceipt:
    nation = await session.get(Nation, nation_id)
    if nation is None:
        raise ValueError("ملت پیدا نشد.")

    limit = max(1, int(last_n))
    rows = list(reversed((
        await session.execute(
            select(RateHistory)
            .where(RateHistory.nation_id == nation_id)
            .order_by(RateHistory.calculated_at.desc())
            .limit(limit)
        )
    ).scalars().all()))

    factor_averages: dict[str, Decimal] = {}
    for factor_name, _label, _weight in _RECEIPT_SPECS:
        values = [
            _to_decimal(getattr(row, factor_name, None))
            for row in rows
        ]
        factor_averages[factor_name] = (
            sum(values, Decimal("0")) / Decimal(len(values))
            if values
            else Decimal("0")
        )

    weighted_abs = {
        factor_name: abs(factor_averages[factor_name]) * weight
        for factor_name, _label, weight in _RECEIPT_SPECS
    }
    dominant_cause = (
        max(weighted_abs, key=weighted_abs.get)
        if weighted_abs
        else "activity_score"
    )

    if len(rows) >= 2 and _to_decimal(rows[0].rate) > 0:
        total_change = (
            (
                _to_decimal(rows[-1].rate)
                - _to_decimal(rows[0].rate)
            )
            / _to_decimal(rows[0].rate)
            * Decimal("100")
        )
    else:
        total_change = Decimal("0")

    lines = []
    for factor_name, _label, weight in _RECEIPT_SPECS:
        impact = (
            factor_averages[factor_name]
            * weight
            * settings.rate_base_step
            * Decimal("100")
        ).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
        if impact == 0:
            continue
        lines.append(
            ReceiptLine(
                label=_label_for_factor(
                    factor_name,
                    factor_averages[factor_name],
                ),
                impact_pct=impact,
                emoji=_emoji_for_value(impact),
            )
        )

    lines.sort(
        key=lambda line: abs(line.impact_pct),
        reverse=True,
    )

    period_minutes = 15 * len(rows) if rows else 0
    return PriceReceipt(
        currency_code=nation.currency_code,
        period_label=f"{_fa_digits(period_minutes)} دقیقه گذشته",
        total_change_pct=total_change.quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        ),
        lines=lines,
        summary=_summary(
            nation.currency_code,
            dominant_cause,
            factor_averages,
        ),
    )
