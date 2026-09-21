from __future__ import annotations

import html
from decimal import Decimal, ROUND_HALF_UP

from app.services.rules.effects import apply_trade_benefit, calculate_trade_fee
from config import settings

_FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")
_FA_TO_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬−", "0123456789.,-")


def to_fa(number) -> str:
    """Return user-facing numbers using Latin/English digits."""
    return str(number)


def from_fa(value: str) -> str:
    return value.translate(_FA_TO_LATIN)


def get_rate_change(nation) -> float:
    if not nation.rate_24h_open:
        return 0.0
    return float(
        (nation.exchange_rate - nation.rate_24h_open)
        / nation.rate_24h_open
        * Decimal("100")
    )


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


def calc_trade(
    spend: Decimal,
    rate: Decimal,
    is_buy: bool,
    *,
    fee_rate_percent: Decimal = Decimal("0.5"),
    benefit_multiplier: Decimal = Decimal("1"),
) -> dict[str, Decimal]:
    spend = Decimal(str(spend))
    rate = Decimal(str(rate))
    fee = calculate_trade_fee(spend, Decimal(str(fee_rate_percent)))
    if is_buy:
        net = spend - fee
        receive = net / rate
    else:
        receive = (spend * rate) - fee
        net = receive

    if benefit_multiplier != Decimal("1"):
        receive = apply_trade_benefit(receive, benefit_multiplier)
        net = receive

    return {
        "spend": spend,
        "fee": fee,
        "net": net,
        "receive": receive,
    }


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = (
        365 * gy2
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
    )
    for i in range(gm2):
        g_day_no += g_days_in_month[i]
    if gm2 > 1 and (
        (gy % 4 == 0 and gy % 100 != 0)
        or gy % 400 == 0
    ):
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

    y, m, d = gregorian_to_jalali(
        datetime.now().year,
        datetime.now().month,
        datetime.now().day,
    )
    return to_fa(f"{y:04d}/{m:02d}/{d:02d}")


def imperial_datetime(now=None) -> tuple[str, str]:
    """Return the canonical game date/time for every user-facing timestamp.

    The date uses the Iranian Imperial calendar (Solar Hijri year + 1180).
    The clock is the game's configured local time and is not offset by the
    Imperial calendar.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if now is None:
        now = datetime.now(ZoneInfo(settings.temporal_timezone))

    jalali_year, jalali_month, jalali_day = gregorian_to_jalali(
        now.year,
        now.month,
        now.day,
    )
    imperial_year = jalali_year + 1180
    return (
        to_fa(f"{imperial_year:04d}/{jalali_month:02d}/{jalali_day:02d}"),
        to_fa(now.strftime("%H:%M")),
    )


def progress_bar(current: int, total: int, length: int = 6) -> str:
    if total <= 0:
        return "░" * length
    filled = min(round(current / total * length), length)
    return "█" * filled + "░" * (length - filled)


def _rule_percent(value: Decimal) -> str:
    return to_fa(f"{Decimal(str(value)).normalize():f}") + "٪"


def _direction(change: float) -> str:
    if change > 0:
        return "🟢"
    if change < 0:
        return "🔴"
    return "🟡"


def _risk_title(label: str) -> str:
    return label.split(" ", 1)[1] if " " in label else label


def format_market_page(user, overview: dict, active: int, trade_count: int) -> str:
    from app.services.market_intelligence import format_change_text, format_percent_value, format_volume
    currencies = overview.get("currencies") or []
    if not currencies:
        return "⚠️ هنوز ارز فعالی برای نمایش بازار وجود ندارد."

    if trade_count < 2:
        lines = [
            "💹 <b>بازار OPEX</b>",
            "<blockquote>⁠</blockquote>",
            "اینجا با <b>دلار</b> ارز ملت‌ها رو می‌خری و می‌فروشی.",
            "🧠 قانون ساده: وقتی قیمت یک ارز بالا بره، ارزش دارایی‌ات بیشتر می‌شه؛ اگر پایین بیاد، کمتر می‌شه.",
            "",
            f"💎 دلار تو: <b>{fmt_amount(user.xr_balance)}</b>",
            "",
            "📌 <b>برای شروع فقط این دو چیز رو ببین:</b>",
            "قیمت فعلی و تغییر ۲۴ ساعت اخیر.",
            "",
        ]
        for item in currencies[:6]:
            nation = item["nation"]
            price = fmt_rate(item["current_rate"])
            change = float(item["change_24h"])
            direction = _direction_emoji(change)
            lines.append(
                f"{direction} <b>{html.escape(nation.currency_code)}</b> · "
                f"<b>{price} دلار</b> · {format_change_text(change, '24h')}"
            )
        lines.extend([
            "",
            "🎯 <b>حرکت پیشنهادی:</b> یک ارز رو انتخاب کن و خرید اولت رو امتحان کن.",
        ])
        return "\n".join(lines)

    lines = [
        "💹 <b>بازار OPEX</b>",
        "<blockquote>⁠</blockquote>",
        f"💰 دلار: <b>{fmt_amount(user.xr_balance)}</b> · "
        f"{html.escape(user.home_nation_id and currencies[0]['nation'].currency_code or '—')}",
        "",
        "<blockquote>⁠</blockquote>",
        overview["mood"],
        "<blockquote>⁠</blockquote>",
    ]
    winner_code, winner_pct = overview["top_mover"]["winner"]
    loser_code, loser_pct = overview["top_mover"]["loser"]
    if winner_code:
        lines.append(f"🏆 بهترین: {_format_mover(winner_code, winner_pct)}")
    if loser_code:
        lines.append(f"💀 بدترین: {_format_mover(loser_code, loser_pct)}")
    lines.append("")

    for item in currencies:
        nation = item["nation"]
        code = html.escape(nation.currency_code)
        price = fmt_rate(item["current_rate"])
        change = float(item["change_24h"])
        direction = _direction_emoji(change)
        risk_title = html.escape(_risk_title(item["risk_label"]))
        lines.extend([
            f"{direction} <b>{code}</b> [{risk_title}]  <b>{price} OPX</b>",
            f"   {format_change_text(change, '24h')}",
            f"   📊 حجم ۲۴ ساعت: <b>{format_volume(item['volume_24h'])} OPX</b>",
            f"   <i>{html.escape(item['insight'])}</i>",
            "",
        ])
        if sum(len(line) + 1 for line in lines) > 3300:
            lines.append("… فقط بخشی از ارزها در این صفحه نمایش داده شد.")
            break

    lines.extend([
        "<blockquote>⁠</blockquote>",
        f"⏱ بروزرسانی نرخ‌ها هر ۱۵ دقیقه · 👥 {to_fa(active)} عضو فعال ملت اصلی",
    ])
    return "\n".join(lines)


def _direction_emoji(change: float) -> str:
    if change > 0:
        return "🟢"
    if change < 0:
        return "🔴"
    return "🟡"


def _format_mover(code: str | None, pct: float) -> str:
    from app.services.market_intelligence import format_percent_value
    if not code:
        return "—"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "➡️"
    return f"{html.escape(code)} {arrow} {format_percent_value(pct)}"

def format_listed_currencies(page) -> str:
    if page.total_count == 0:
        return "📋 <b>ارزهای لیست شده</b>\n\nفعلاً هیچ ارز فعالی برای نمایش وجود ندارد."
    from app.services.market.market_service import LISTED_CURRENCIES_PAGE_SIZE
    start_index = page.page * LISTED_CURRENCIES_PAGE_SIZE + 1
    lines = [
        "📋 <b>ارزهای لیست شده</b>",
        "",
        f"تعداد کل: <b>{to_fa(page.total_count)}</b> ارز",
        "مرتب‌سازی: <b>کمترین قیمت فعلی → بیشترین قیمت فعلی</b>",
        "",
        "برای استفاده در بخش‌های دیگر، روی خود کد ارز بزن تا راحت کپی شود.",
        "",
    ]
    lines.extend(
        f"{to_fa(index)}. <code>{html.escape(code)}</code>"
        for index, code in enumerate(page.currency_codes, start=start_index)
    )
    return "\n".join(lines)


def format_alert_currency_prompt() -> str:
    return "🔔 <b>ثبت هشدار قیمت</b>\n\nکد ارز را بنوی.\nمثال: <code>OPX</code>"


def format_chart_currency_prompt() -> str:
    return "📊 <b>نمودار ارز</b>\n\nکد ارز را بنوی.\nمثال: <code>OPX</code>"


def format_alert_target_prompt(code: str, current_rate: Decimal | None = None) -> str:
    if current_rate is not None:
        return (
            f"🔔 <b>قیمت هدف {html.escape(code)}</b>\n\n"
            f"قیمت فعلی: <b>{fmt_rate(current_rate)} OPX</b>\n"
            "قیمت موردنظر برای هشدار را بنوی.\n"
            "مثال: <code>1.5</code>"
        )
    return (
        "🔔 <b>هشدار قیمت</b>\n\n"
        f"قیمت هدف برای <b>{html.escape(code)}</b> را بنوی.\n"
        "مثال: <code>1.5</code> یا <code>1.5 below</code>"
    )


def format_alert_created(alert, *, command: bool = False) -> str:
    arrow = "▲" if alert.direction == "above" else "▼"
    if command:
        return f"✅ هشدار ثبت شد\n🔔 {html.escape(alert.currency_code)} · هدف: <b>{fmt_rate(alert.target_price)} OPX</b> {arrow}"
    return f"✅ هشدار {html.escape(alert.currency_code)} ثبت شد.\nهدف: <b>{fmt_rate(alert.target_price)} OPX</b> {arrow}"


def format_alert_list(alerts) -> tuple[str, list[tuple[str, str]]]:
    lines = ["🔔 <b>هشدارهای قیمت من</b>", "<blockquote>⁠</blockquote>"]
    actions: list[tuple[str, str]] = []
    if not alerts:
        lines.append("هنوز هشداری ثبت نکردی.")
    else:
        for alert in alerts[:20]:
            status = "✅ فعال شده" if alert.triggered else "⏳ فعال"
            arrow = "▲" if alert.direction == "above" else "▼"
            lines.append(
                f"• <b>{html.escape(alert.currency_code)}</b> · "
                f"{fmt_rate(alert.target_price)} OPX {arrow} · {status}"
            )
            actions.append(
                (f"🗑 حذف {alert.currency_code} #{alert.id}", f"alert:delete:{alert.id}")
            )
    return "\n".join(lines), actions


def format_buy_market(user, nations) -> str:
    lines = [
        "📈 <b>خرید ارز</b>",
        "<blockquote>⁠</blockquote>",
        f"💰 دلار موجود: <b>{fmt_amount(user.xr_balance)}</b>",
        "",
        "<b>کدوم ارز می‌خوای بخری؟</b>",
    ]
    lines.extend(
        f"🏛 <code>{html.escape(n.currency_code)}</code> · {html.escape(n.name)} · "
        f"<b>{fmt_rate(n.exchange_rate)} دلار</b>"
        for n in nations
    )
    return "\n".join(lines)


def format_buy_currency(nation, user) -> str:
    return (
        f"📈 <b>خرید <code>{html.escape(nation.currency_code)}</code></b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} دلار</code>\n"
        f"💰 موجودی: <b>{fmt_amount(user.xr_balance)} دلار</b>\n\n"
        "<b>چقدر دلار خرج می‌کنی؟</b>\n<i>حداقل 10 دلار</i>"
    )


def format_buy_preview(result) -> str:
    signal = "\n📈 <i>بازار الان با تو راه میاد.</i>\n" if result.peak_multiplier > Decimal("1") else ""
    return (
        "📈 <b>تأیید خرید</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📤 پرداخت:   <b>{fmt_amount(result.spend)} دلار</b>\n"
        f"📥 دریافت:   <b>{fmt_amount(result.receive)} {html.escape(result.nation.currency_code)}</b>\n\n"
        "<blockquote>⁠</blockquote>\n"
        f"💹 نرخ: <code>1 {html.escape(result.nation.currency_code)} = {fmt_rate(result.nation.exchange_rate)} دلار</code>\n"
        f"📋 کارمزد: <b>{fmt_amount(result.fee)} دلار</b> ({_rule_percent(result.fee_rate)})\n"
        f"{signal}\n"
        "<blockquote>⁠</blockquote>\n"
        "<b>موجودی بعد از معامله:</b>\n"
        f"دلار: <b>{fmt_amount(result.user.xr_balance - result.spend)}</b>\n"
        f"{html.escape(result.nation.currency_code)}: <b>{fmt_amount(result.current_holding + result.receive)}</b>\n"
        "<blockquote>⁠</blockquote>"
    )


def format_buy_completed(result) -> str:
    signal = "\n📈 بازار الان با تو راه میاد." if result.peak_multiplier > Decimal("1") else ""
    return (
        "✅ <b>خرید انجام شد.</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📤 پرداختی: <s>{fmt_amount(result.spend)} دلار</s>\n"
        f"📥 دریافتی: <b>{fmt_amount(result.receive)} {html.escape(result.nation.currency_code)}</b>\n"
        f"{signal}\n"
        "<blockquote>⁠</blockquote>\n"
        "💰 موجودی:\n"
        f"دلار: <b>{fmt_amount(result.user.xr_balance)}</b>\n"
        f"{html.escape(result.nation.currency_code)}: <b>{fmt_amount(result.holding.amount)}</b>"
    )


def format_sell_market(active_holdings, inactive_holdings) -> str:
    lines = ["📉 <b>فروش ارز</b>", "<blockquote>⁠</blockquote>"]
    if not active_holdings and not inactive_holdings:
        return "📉 <b>فروش ارز</b>\n<blockquote>⁠</blockquote>\nهنوز ارزی برای فروش نداری.\n\nاز 📈 خرید ارز شروع کن."
    if active_holdings:
        lines.append("<b>ارزهای قابل فروش:</b>")
        lines.extend(
            f"💱 <code>{html.escape(h.currency_code)}</code> · موجودی: <b>{fmt_amount(h.amount)}</b>"
            for h in active_holdings
        )
    if inactive_holdings:
        lines.extend(["", "🚫 <b>ارزهای غیرقابل فروش:</b>"])
        lines.extend(
            f"• <code>{html.escape(h.currency_code)}</code> ({html.escape(h.nation_name)}) · ملت این ارز منحل شده - قابل فروش نیست"
            for h in inactive_holdings
        )
    return "\n".join(lines)


def format_sell_currency(nation, holding) -> str:
    return (
        f"📉 <b>فروش <code>{html.escape(nation.currency_code)}</code></b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"💹 نرخ: <code>1 {html.escape(nation.currency_code)} = {fmt_rate(nation.exchange_rate)} دلار</code>\n"
        f"💰 موجودی: <b>{fmt_amount(holding.amount)} {html.escape(nation.currency_code)}</b>\n\n"
        "<b>چقدر می‌فروشی؟</b>"
    )


def format_sell_preview(result) -> str:
    xr_after = result.user.xr_balance + result.receive
    currency_after = result.current_holding - result.spend
    signal = "\n📈 <i>بازار الان با تو راه میاد.</i>\n" if result.peak_multiplier > Decimal("1") else ""
    return (
        "📉 <b>تأیید فروش</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📤 فروش:     <b>{fmt_amount(result.spend)} {html.escape(result.nation.currency_code)}</b>\n"
        f"📥 دریافت:   <b>{fmt_amount(result.receive)} دلار</b>\n\n"
        "<blockquote>⁠</blockquote>\n"
        f"💹 نرخ: <code>1 {html.escape(result.nation.currency_code)} = {fmt_rate(result.nation.exchange_rate)} دلار</code>\n"
        f"📋 کارمزد: <b>{fmt_amount(result.fee)} دلار</b> ({_rule_percent(result.fee_rate)})\n"
        f"{signal}\n"
        "<blockquote>⁠</blockquote>\n"
        "<b>موجودی بعد از معامله:</b>\n"
        f"دلار: <b>{fmt_amount(xr_after)}</b>\n"
        f"{html.escape(result.nation.currency_code)}: <b>{fmt_amount(currency_after)}</b>\n"
        "<blockquote>⁠</blockquote>"
    )


def format_sell_completed(result) -> str:
    signal = "\n📈 بازار الان با تو راه میاد." if result.peak_multiplier > Decimal("1") else ""
    return (
        "✅ <b>فروش انجام شد.</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"📤 فروختی:  <b>{fmt_amount(result.spend)} {html.escape(result.nation.currency_code)}</b>\n"
        f"📥 دریافتی: <b>{fmt_amount(result.receive)} دلار</b>\n"
        f"{signal}\n"
        "<blockquote>⁠</blockquote>\n"
        "💰 موجودی:\n"
        f"دلار: <b>{fmt_amount(result.user.xr_balance)}</b>\n"
        f"{html.escape(result.nation.currency_code)}: <b>{fmt_amount(result.holding.amount)}</b>"
    )


def format_market_history(user, rows) -> str:
    lines = ["📜 <b>تاریخچه</b>", "<blockquote>⁠</blockquote>"]
    if not rows:
        lines.append("هنوز معامله‌ای انجام ندادی.")
    for transaction, code in rows:
        icon = "📈" if transaction.transaction_type == "buy" else "📉"
        lines.append(
            f"{icon} <code>{html.escape(code)}</code> · "
            f"{fmt_amount(transaction.amount)} · {fmt_rate(transaction.rate)} دلار"
        )
    return "\n".join(lines)
