from __future__ import annotations

import asyncio
import math
import threading
from datetime import datetime
from io import BytesIO
from statistics import mean, pstdev

import arabic_reshaper
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from bidi.algorithm import get_display
from matplotlib.font_manager import FontProperties, findSystemFonts
from matplotlib.patches import Circle, FancyBboxPatch


_RENDER_LOCK = threading.RLock()
_RLM = "\u200f"

_WINDOW_LABELS = {
    "1h": "۱ ساعت",
    "6h": "۶ ساعت",
    "24h": "۲۴ ساعت",
    "7d": "۷ روز",
    # Kept for old inline messages. It is not shown in the new keyboard.
    "72h": "۷۲ ساعت",
}


def _find_persian_font() -> str | None:
    candidates = (
        "Vazirmatn",
        "Vazir",
        "NotoSansArabic",
        "NotoNaskhArabic",
        "DejaVuSans",
    )
    for path in findSystemFonts():
        normalized = path.lower().replace(" ", "").replace("-", "")
        if any(
            candidate.lower().replace(" ", "").replace("-", "") in normalized
            for candidate in candidates
        ):
            return path
    return None


_FONT_PATH = _find_persian_font()
_FONT_NORMAL = (
    FontProperties(fname=_FONT_PATH)
    if _FONT_PATH
    else FontProperties()
)
_FONT_BOLD = (
    FontProperties(fname=_FONT_PATH, weight="bold")
    if _FONT_PATH
    else FontProperties(weight="bold")
)


def _fa_text(text: str) -> str:
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


def _fa_digits(value: object) -> str:
    return str(value).translate(
        str.maketrans(
            "0123456789.-",
            "۰۱۲۳۴۵۶۷۸۹٫−",
        )
    )


def _format_axis_value(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"{value:.2f}"
    if absolute >= 1:
        return f"{value:.3f}"
    return f"{value:.4f}"


def _format_price(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"{value:.2f}"
    if absolute >= 1:
        return f"{value:.3f}"
    return f"{value:.4f}"


def calculate_volatility(rates: list[float]) -> float:
    values = [float(value) for value in rates if math.isfinite(float(value))]
    if not values:
        return 0.0

    average = mean(values)
    if average == 0:
        return 0.0

    return pstdev(values) / abs(average)


def classify_risk(rates: list[float]) -> tuple[str, str, str]:
    volatility = calculate_volatility(rates)

    if volatility < 0.02:
        return "کم‌ریسک", "low", "#16A34A"
    if volatility < 0.06:
        return "پرنوسان", "medium", "#CA8A04"
    return "سقوط آزاد", "high", "#DC2626"


def detect_three_day_downtrend(
    rates: list[float],
    timestamps: list[datetime],
) -> bool:
    daily_last: dict[object, float] = {}

    for timestamp, rate in zip(timestamps, rates):
        daily_last[timestamp.date()] = float(rate)

    if len(daily_last) < 3:
        return False

    ordered_days = sorted(daily_last)
    last_three = ordered_days[-3:]
    values = [daily_last[day] for day in last_three]

    return values[0] > values[1] > values[2]


def _safe_percentage(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100.0


def build_smart_insight(
    *,
    market_status: str | None,
    rates: list[float],
    timestamps: list[datetime],
    change_7d: float | None,
    three_day_downtrend: bool = False,
) -> str:
    if market_status == "best":
        return _fa_text("بهترین عملکرد امروز")

    if market_status == "worst":
        return _fa_text("بدترین عملکرد امروز")

    if three_day_downtrend or detect_three_day_downtrend(rates, timestamps):
        return _fa_text("روند نزولی ۳ روزه")

    if change_7d is not None:
        rounded = round(abs(change_7d))
        if change_7d > 0:
            return _fa_text(
                f"نسبت به هفته پیش {_fa_digits(rounded)}٪ بالاتره"
            )
        if change_7d < 0:
            return _fa_text(
                f"نسبت به هفته پیش {_fa_digits(rounded)}٪ پایین‌تره"
            )

    if len(rates) >= 2:
        change = _safe_percentage(
            float(rates[0]),
            float(rates[-1]),
        )
        if change > 0:
            return _fa_text("امروز روند این ارز مثبت بوده")
        if change < 0:
            return _fa_text("امروز روند این ارز منفی بوده")

    return _fa_text("امروز قیمت این ارز تقریباً بدون تغییر بوده")


def _draw_gradient(
    ax,
    x: np.ndarray,
    values: np.ndarray,
    baseline: float,
    color: str,
) -> None:
    layers = 48
    for index in range(layers):
        lower = index / layers
        upper = (index + 1) / layers
        y1 = baseline + (values - baseline) * lower
        y2 = baseline + (values - baseline) * upper
        alpha = 0.008 + upper * 0.075
        ax.fill_between(
            x,
            y1,
            y2,
            color=color,
            alpha=alpha,
            linewidth=0,
        )


def _render_currency_chart(
    *,
    currency_code: str,
    nation_name: str,
    nation_flag: str,
    rates: list[float],
    timestamps: list[datetime],
    current_rate: float | None,
    window: str,
    base_currency: str,
    market_status: str | None,
    change_7d: float | None,
    three_day_downtrend: bool = False,
) -> BytesIO:
    if window not in _WINDOW_LABELS:
        raise ValueError("invalid_window")

    if len(rates) < 3:
        raise ValueError("not_enough_data")

    if len(rates) != len(timestamps):
        raise ValueError("history_length_mismatch")

    values = [float(rate) for rate in rates]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("invalid_rate")

    if current_rate is not None:
        current = float(current_rate)
        if not math.isfinite(current):
            raise ValueError("invalid_current_rate")
        if values[-1] != current:
            values.append(current)
            timestamps = [*timestamps, datetime.utcnow()]

    line_color = (
        "#16A34A"
        if values[-1] >= values[0]
        else "#DC2626"
    )
    trend_icon = "▲" if values[-1] >= values[0] else "▼"
    trend_word = "رشد" if values[-1] >= values[0] else "افت"

    change_pct = _safe_percentage(
        values[0],
        values[-1],
    )

    risk_label, risk_icon, risk_color = classify_risk(values)

    insight = build_smart_insight(
        market_status=market_status,
        rates=values,
        timestamps=timestamps,
        change_7d=change_7d,
        three_day_downtrend=three_day_downtrend,
    )

    chart_values = np.asarray(values, dtype=float)
    x = np.arange(len(chart_values), dtype=float)

    minimum = float(np.min(chart_values))
    maximum = float(np.max(chart_values))

    if math.isclose(minimum, maximum):
        padding = max(abs(maximum) * 0.04, 0.01)
    else:
        padding = (maximum - minimum) * 0.12

    y_min = minimum - padding
    y_max = maximum + padding

    fig = plt.figure(
        figsize=(800 / 150, 450 / 150),
        dpi=150,
        facecolor="#EEF1F4",
    )

    card = FancyBboxPatch(
        (0.02, 0.025),
        0.96,
        0.95,
        transform=fig.transFigure,
        boxstyle="round,pad=0.008,rounding_size=0.03",
        facecolor="#FFFFFF",
        edgecolor="none",
        linewidth=0,
        zorder=0,
    )
    fig.patches.append(card)

    ax = fig.add_axes(
        [0.095, 0.205, 0.84, 0.43],
        facecolor="#FFFFFF",
    )
    ax.set_axisbelow(True)

    ticks = np.linspace(y_min, y_max, 5)
    ax.set_yticks(ticks)
    ax.set_yticklabels(
        [_format_axis_value(float(value)) for value in ticks],
        fontproperties=_FONT_NORMAL,
        fontsize=8.5,
        color="#9CA3AF",
    )

    ax.tick_params(
        axis="y",
        length=0,
        pad=6,
    )
    ax.set_xticks([])

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.grid(
        axis="y",
        color="#E5E7EB",
        linewidth=0.7,
        alpha=0.8,
    )
    ax.grid(
        axis="x",
        visible=False,
    )

    _draw_gradient(
        ax=ax,
        x=x,
        values=chart_values,
        baseline=y_min,
        color=line_color,
    )

    ax.plot(
        x,
        chart_values,
        color=line_color,
        linewidth=2.6,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=5,
    )

    last_x = x[-1]
    last_y = chart_values[-1]

    ax.scatter(
        [last_x],
        [last_y],
        s=130,
        color=line_color,
        alpha=0.12,
        linewidths=0,
        zorder=6,
    )
    ax.scatter(
        [last_x],
        [last_y],
        s=48,
        color=line_color,
        edgecolors="#FFFFFF",
        linewidths=2,
        zorder=7,
    )

    ax.set_xlim(x[0], x[-1])
    ax.set_ylim(y_min, y_max)

    # Header badge.
    badge = FancyBboxPatch(
        (0.075, 0.87),
        0.105,
        0.052,
        transform=fig.transFigure,
        boxstyle="round,pad=0.007,rounding_size=0.016",
        facecolor="#F3F4F6",
        edgecolor="#E5E7EB",
        linewidth=0.7,
    )
    fig.patches.append(badge)

    fig.text(
        0.1275,
        0.896,
        window,
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        color="#374151",
    )

    # Risk badge.
    risk_badge = FancyBboxPatch(
        (0.075, 0.803),
        0.22,
        0.052,
        transform=fig.transFigure,
        boxstyle="round,pad=0.007,rounding_size=0.016",
        facecolor=risk_color,
        edgecolor="none",
        linewidth=0,
        alpha=0.10,
    )
    fig.patches.append(risk_badge)

    # Matplotlib stays emoji-free: use a real colored patch for risk status.
    risk_marker = Circle(
        (0.092, 0.829),
        0.008,
        transform=fig.transFigure,
        facecolor=risk_color,
        edgecolor="none",
        zorder=5,
    )
    fig.patches.append(risk_marker)

    fig.text(
        0.105,
        0.829,
        _fa_text(risk_label),
        ha="left",
        va="center",
        fontproperties=_FONT_BOLD,
        fontsize=8.5,
        color=risk_color,
    )

    # Nation identity stays emoji-free in the chart. Telegram can still show the flag.
    nation_title = _fa_text(
        f"{nation_name} · {currency_code}".strip(" ·")
    )
    fig.text(
        0.925,
        0.897,
        nation_title,
        ha="right",
        va="center",
        fontproperties=_FONT_BOLD,
        fontsize=10.5,
        color="#111827",
    )

    # Current price.
    price_text = (
        f"{_format_price(values[-1])} {base_currency}"
    )
    fig.text(
        0.925,
        0.835,
        price_text,
        ha="right",
        va="center",
        fontsize=22,
        fontweight="bold",
        color="#111827",
    )

    change_text = (
        f"{trend_icon} {_fa_digits(round(abs(change_pct), 2))}٪ "
        f"{trend_word} در {_WINDOW_LABELS[window]} گذشته"
    )
    fig.text(
        0.925,
        0.777,
        _fa_text(change_text),
        ha="right",
        va="center",
        fontproperties=_FONT_NORMAL,
        fontsize=9,
        color=line_color,
    )

    # Footer insight.
    fig.text(
        0.095,
        0.095,
        insight,
        ha="left",
        va="center",
        fontproperties=_FONT_NORMAL,
        fontsize=9,
        color="#4B5563",
    )

    fig.text(
        0.925,
        0.095,
        f"{currency_code}/{base_currency}",
        ha="right",
        va="center",
        fontsize=8.5,
        color="#9CA3AF",
    )

    output = BytesIO()

    fig.savefig(
        output,
        format="png",
        dpi=150,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        pad_inches=0,
    )
    plt.close(fig)

    output.name = "opex-market-chart.png"
    output.seek(0)
    return output


def render_price_chart(
    rates: list[float],
    *,
    timestamps: list[datetime] | None = None,
    currency_code: str = "",
    nation_name: str = "",
    nation_flag: str = "",
    current_rate: float | None = None,
    window: str = "24h",
    base_currency: str = "OPX",
    market_status: str | None = None,
    change_7d: float | None = None,
    three_day_downtrend: bool = False,
    width: int = 800,
    height: int = 450,
) -> BytesIO:
    """
    Synchronous renderer kept for internal and backward-compatible use.

    Width and height are intentionally fixed by the requested design.
    The arguments are accepted to keep existing callers readable.
    """
    if width != 800 or height != 450:
        raise ValueError("chart_size_must_be_800x450")

    if timestamps is None:
        timestamps = [
            datetime.utcnow()
            for _ in rates
        ]

    with _RENDER_LOCK:
        return _render_currency_chart(
            currency_code=currency_code,
            nation_name=nation_name,
            nation_flag=nation_flag,
            rates=rates,
            timestamps=timestamps,
            current_rate=current_rate,
            window=window,
            base_currency=base_currency,
            market_status=market_status,
            change_7d=change_7d,
            three_day_downtrend=three_day_downtrend,
        )


async def generate_currency_chart(
    currency_code: str,
    nation_name: str,
    nation_flag: str,
    price_history: list[float],
    timestamps: list[datetime],
    window: str = "24h",
    base_currency: str = "OPX",
    current_rate: float | None = None,
    market_status: str | None = None,
    change_7d: float | None = None,
    three_day_downtrend: bool = False,
) -> BytesIO:
    """
    Async chart API for Telegram handlers.

    Rendering runs in a worker thread so Matplotlib does not block
    aiogram's event loop.
    """
    return await asyncio.to_thread(
        render_price_chart,
        price_history,
        timestamps=timestamps,
        currency_code=currency_code,
        nation_name=nation_name,
        nation_flag=nation_flag,
        current_rate=current_rate,
        window=window,
        base_currency=base_currency,
        market_status=market_status,
        change_7d=change_7d,
        three_day_downtrend=three_day_downtrend,
    )
