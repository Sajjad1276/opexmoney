from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean, pstdev

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, RateHistory, Transaction


_FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")


WINDOW_LABELS = {
    "1h": "۱ ساعت گذشته",
    "6h": "۶ ساعت گذشته",
    "24h": "۲۴ ساعت گذشته",
    "7d": "۷ روز گذشته",
}


def _fa(value: object) -> str:
    return str(value).translate(_FA_DIGITS)


def _rounded_percent(value: float | Decimal) -> int:
    return int(
        Decimal(str(abs(value))).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def format_percent_value(value: float | Decimal) -> str:
    return f"{_fa(_rounded_percent(value))}٪"


def format_volume(value: Decimal | float | int) -> str:
    number = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(number):,}".translate(_FA_DIGITS)


def format_change_text(change_pct: float, window: str = "24h") -> str:
    rounded = _rounded_percent(change_pct)
    if rounded == 0:
        return "➡️ بدون تغییر"

    window_label = WINDOW_LABELS.get(window, window)
    value = _fa(rounded)

    if change_pct > 0:
        return f"▲ {value} درصد رشد در {window_label}"
    return f"▼ {value} درصد افت در {window_label}"


def get_risk_label(price_history: list[float]) -> str:
    if not price_history:
        return "🟢 کم‌ریسک"

    values = [float(value) for value in price_history if float(value) >= 0]
    if not values:
        return "🟢 کم‌ریسک"

    average = mean(values)
    if average <= 0:
        return "🔴 سقوط آزاد"

    volatility = pstdev(values) / average
    if volatility < 0.02:
        return "🟢 کم‌ریسک"
    if volatility < 0.06:
        return "🟡 پرنوسان"
    return "🔴 سقوط آزاد"


def _risk_title(label: str) -> str:
    return label.split(" ", 1)[1] if " " in label else label


def _daily_last_rates(
    price_history: list[tuple[datetime, Decimal]],
) -> list[tuple[datetime.date, Decimal]]:
    daily: dict[object, Decimal] = {}
    for timestamp, rate in price_history:
        daily[timestamp.date()] = rate
    return sorted(daily.items(), key=lambda item: item[0])


def get_consecutive_trend_days(
    price_history: list[tuple[datetime, Decimal]],
) -> int:
    """
    Return a signed streak length.

    Positive means consecutive daily closes are falling.
    Negative means consecutive daily closes are rising.
    Zero means no directional streak can be established.
    """
    daily = _daily_last_rates(price_history)
    if len(daily) < 2:
        return 0

    last = daily[-1][1]
    previous = daily[-2][1]

    if last == previous:
        return 0

    direction = -1 if last > previous else 1
    streak = 2

    index = len(daily) - 2
    while index > 0:
        current = daily[index][1]
        before = daily[index - 1][1]

        if direction == 1 and current < before:
            streak += 1
        elif direction == -1 and current > before:
            streak += 1
        else:
            break
        index -= 1

    return streak * direction


def _trend_text(streak: int) -> str | None:
    if streak >= 3:
        return f"⚠️ روند نزولی {_fa(streak)} روزه"
    if streak <= -3:
        return f"📈 روند صعودی {_fa(abs(streak))} روزه"
    return None


async def get_smart_insight(
    currency_code: str,
    change_24h: float,
    change_7d: float,
    all_currencies_changes: dict[str, float],
    consecutive_down_days: int,
) -> str:
    insights: list[str] = []

    if len(all_currencies_changes) > 1 and currency_code in all_currencies_changes:
        best_code = max(all_currencies_changes, key=all_currencies_changes.get)
        worst_code = min(all_currencies_changes, key=all_currencies_changes.get)

        if currency_code == worst_code and currency_code != best_code:
            insights.append("📉 این ارز امروز بدترین عملکرد رو داشته")
        elif currency_code == best_code and currency_code != worst_code:
            insights.append("🚀 این ارز امروز بهترین عملکرد رو داشته")

    trend = _trend_text(consecutive_down_days)
    if trend is not None:
        insights.append(trend)

    weekly = _rounded_percent(change_7d)
    if weekly > 0:
        insights.append(f"نسبت به هفته پیش {_fa(weekly)}٪ بالاتره")
    elif weekly < 0:
        insights.append(f"نسبت به هفته پیش {_fa(weekly)}٪ پایین‌تره")

    if not insights:
        rounded = _rounded_percent(change_24h)
        if rounded == 0:
            return "ℹ️ فعلاً تغییر برجسته‌ای در روند این ارز دیده نمی‌شه"
        return "ℹ️ این ارز امروز حرکت ملایمی داشته"

    return "؛ ".join(insights)


async def _load_market_snapshot(session: AsyncSession) -> list[dict]:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)

    nations = list(
        (
            await session.execute(
                select(Nation)
                .where(Nation.is_active.is_(True))
                .order_by(Nation.currency_code.asc())
            )
        ).scalars().all()
    )

    if not nations:
        return []

    history_rows = (
        await session.execute(
            select(
                RateHistory.nation_id,
                RateHistory.rate,
                RateHistory.calculated_at,
            )
            .where(
                RateHistory.nation_id.in_([nation.nation_id for nation in nations]),
                RateHistory.calculated_at >= cutoff,
            )
            .order_by(
                RateHistory.nation_id.asc(),
                RateHistory.calculated_at.asc(),
            )
        )
    ).all()

    volume_rows = (
        await session.execute(
            select(
                Transaction.nation_id,
                func.coalesce(func.sum(Transaction.spend_xr), 0),
            )
            .where(
                Transaction.nation_id.in_([nation.nation_id for nation in nations]),
                Transaction.created_at >= cutoff_24h,
                Transaction.transaction_type.in_(("buy", "sell")),
            )
            .group_by(Transaction.nation_id)
        )
    ).all()

    histories: dict[int, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for row in history_rows:
        histories[int(row.nation_id)].append(
            (row.calculated_at, Decimal(str(row.rate)))
        )

    volumes = {
        int(row[0]): Decimal(str(row[1] or 0))
        for row in volume_rows
    }

    raw: list[dict] = []
    for nation in nations:
        history_7d = histories.get(nation.nation_id, [])
        history_24h = [
            item for item in history_7d
            if item[0] >= cutoff_24h
        ]

        if history_24h:
            base_24h = history_24h[0][1]
        else:
            base_24h = Decimal(str(nation.rate_24h_open or nation.exchange_rate))

        if history_7d:
            base_7d = history_7d[0][1]
        else:
            base_7d = base_24h

        current = Decimal(str(nation.exchange_rate))
        change_24h = (
            (current - base_24h) / base_24h * Decimal("100")
            if base_24h > 0
            else Decimal("0")
        )
        change_7d = (
            (current - base_7d) / base_7d * Decimal("100")
            if base_7d > 0
            else Decimal("0")
        )

        risk_history = [float(rate) for _, rate in history_24h]
        if not risk_history:
            risk_history = [float(current)]
        elif risk_history[-1] != float(current):
            risk_history.append(float(current))

        raw.append(
            {
                "nation": nation,
                "history_7d": history_7d,
                "history_24h": history_24h,
                "current_rate": current,
                "change_24h": float(change_24h),
                "change_7d": float(change_7d),
                "risk_label": get_risk_label(risk_history),
                "volume_24h": volumes.get(nation.nation_id, Decimal("0")),
                "trend_streak": get_consecutive_trend_days(history_7d),
            }
        )

    changes = {
        item["nation"].currency_code: item["change_24h"]
        for item in raw
    }

    for item in raw:
        item["all_changes"] = changes
        item["insight"] = await get_smart_insight(
            currency_code=item["nation"].currency_code,
            change_24h=item["change_24h"],
            change_7d=item["change_7d"],
            all_currencies_changes=changes,
            consecutive_down_days=item["trend_streak"],
        )

    return raw


async def get_market_mood(session: AsyncSession) -> str:
    snapshot = await _load_market_snapshot(session)
    if not snapshot:
        return "🟡 بازار در تعادله"

    average_change = sum(item["change_24h"] for item in snapshot) / len(snapshot)

    if average_change > 2:
        return "🟢 بازار امروز صعودیه"
    if average_change < -2:
        return "🔴 بازار امروز نزولیه"
    return "🟡 بازار در تعادله"


async def get_top_mover(session: AsyncSession) -> dict:
    snapshot = await _load_market_snapshot(session)
    if not snapshot:
        return {
            "winner": (None, 0.0),
            "loser": (None, 0.0),
        }

    winner = max(snapshot, key=lambda item: item["change_24h"])
    loser = min(snapshot, key=lambda item: item["change_24h"])

    return {
        "winner": (
            winner["nation"].currency_code,
            winner["change_24h"],
        ),
        "loser": (
            loser["nation"].currency_code,
            loser["change_24h"],
        ),
    }


async def get_volume_24h(
    session: AsyncSession,
    currency_code: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(Transaction.spend_xr), 0))
        .join(Nation, Nation.nation_id == Transaction.nation_id)
        .where(
            Nation.currency_code == currency_code.upper(),
            Nation.is_active.is_(True),
            Transaction.created_at >= datetime.utcnow() - timedelta(hours=24),
            Transaction.transaction_type.in_(("buy", "sell")),
        )
    )
    return Decimal(str(value or 0))


async def get_market_overview(
    session: AsyncSession,
    home_nation_id: int | None,
    limit: int = 12,
) -> dict:
    snapshot = await _load_market_snapshot(session)
    if not snapshot:
        return {
            "mood": "🟡 بازار در تعادله",
            "top_mover": {
                "winner": (None, 0.0),
                "loser": (None, 0.0),
            },
            "currencies": [],
            "all_changes": {},
        }

    all_changes = {
        item["nation"].currency_code: item["change_24h"]
        for item in snapshot
    }

    winner = max(snapshot, key=lambda item: item["change_24h"])
    loser = min(snapshot, key=lambda item: item["change_24h"])

    average_change = sum(all_changes.values()) / len(all_changes)
    if average_change > 2:
        mood = "🟢 بازار امروز صعودیه"
    elif average_change < -2:
        mood = "🔴 بازار امروز نزولیه"
    else:
        mood = "🟡 بازار در تعادله"

    snapshot.sort(
        key=lambda item: (
            0 if item["nation"].nation_id == home_nation_id else 1,
            -abs(item["change_24h"]),
            item["nation"].currency_code,
        )
    )

    visible = snapshot[: max(1, min(int(limit), 20))]

    return {
        "mood": mood,
        "top_mover": {
            "winner": (
                winner["nation"].currency_code,
                winner["change_24h"],
            ),
            "loser": (
                loser["nation"].currency_code,
                loser["change_24h"],
            ),
        },
        "currencies": visible,
        "all_changes": all_changes,
    }
