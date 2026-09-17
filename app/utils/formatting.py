from __future__ import annotations

from decimal import Decimal

_FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")


def to_fa(number) -> str:
    return str(number).translate(_FA_DIGITS)


def get_rate_change(nation) -> float:
    if not nation.rate_24h_open:
        return 0.0
    return float((nation.exchange_rate - nation.rate_24h_open) / nation.rate_24h_open * Decimal("100"))


def get_rate_emoji(change: float) -> str:
    if change > 0.5:
        return "📈"
    if change < -0.5:
        return "📉"
    return "➡️"


def fmt_amount(value: Decimal) -> str:
    return to_fa(f"{value:.2f}")


def fmt_rate(value: Decimal) -> str:
    return to_fa(f"{value:.4f}")


def fmt_pct(value: float) -> str:
    return to_fa(f"{value:+.2f}") + "٪"


def calc_trade(spend: Decimal, rate: Decimal, is_buy: bool) -> dict[str, Decimal]:
    spend = Decimal(str(spend))
    rate = Decimal(str(rate))
    fee = spend * Decimal("0.005")
    if is_buy:
        net = spend - fee
        receive = net / rate
    else:
        receive = (spend * rate) - fee
        net = receive
    return {"spend": spend, "fee": fee, "net": net, "receive": receive}
