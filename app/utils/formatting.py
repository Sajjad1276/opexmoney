from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")
_FA_TO_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬−", "0123456789.,-")


def to_fa(number) -> str:
    return str(number).translate(_FA_DIGITS)


def from_fa(value: str) -> str:
    return value.translate(_FA_TO_LATIN)


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
    value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return to_fa(f"{value:.2f}")


def fmt_rate(value: Decimal) -> str:
    value = Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return to_fa(f"{value:.4f}")


def fmt_pct(value: float | Decimal) -> str:
    return to_fa(f"{float(value):+.2f}") + "٪"


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


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert Gregorian date to Persian calendar without an external dependency."""
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for i in range(gm2):
        g_day_no += g_days_in_month[i]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    g_day_no += gd2
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    for i in range(11):
        if j_day_no < j_days_in_month[i]:
            jm = i + 1
            jd = j_day_no + 1
            return jy, jm, jd
        j_day_no -= j_days_in_month[i]
    return jy, 12, j_day_no + 1


def today_jalali() -> str:
    from datetime import datetime

    y, m, d = gregorian_to_jalali(datetime.now().year, datetime.now().month, datetime.now().day)
    return to_fa(f"{y:04d}/{m:02d}/{d:02d}")
