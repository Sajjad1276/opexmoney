from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def calculate_trade_fee(amount: Decimal, fee_percent: Decimal) -> Decimal:
    return (
        Decimal(str(amount))
        * Decimal(str(fee_percent))
        / Decimal("100")
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def apply_trade_benefit(amount: Decimal, multiplier: Decimal) -> Decimal:
    return (
        Decimal(str(amount)) * Decimal(str(multiplier))
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def calculate_wealth_tax(wealth: Decimal, threshold: Decimal, rate_percent: Decimal) -> Decimal:
    taxable = max(Decimal("0"), Decimal(str(wealth)) - Decimal(str(threshold)))
    return (
        taxable * Decimal(str(rate_percent)) / Decimal("100")
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def apply_tariff(amount: Decimal, tariff_percent: Decimal) -> Decimal:
    return (
        Decimal(str(amount))
        * Decimal(str(tariff_percent))
        / Decimal("100")
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def apply_rate_volatility(delta: Decimal, multiplier: Decimal) -> Decimal:
    return Decimal(str(delta)) * Decimal(str(multiplier))
