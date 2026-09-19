from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.handlers.chart import chart_keyboard
from app.services.chart_service import VISIBLE_WINDOWS, WINDOWS
from app.utils.price_chart import (
    build_smart_insight,
    calculate_volatility,
    classify_risk,
    detect_three_day_downtrend,
    generate_currency_chart,
)


def _png_size(raw: bytes) -> tuple[int, int]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    return width, height


def test_chart_window_contract():
    assert VISIBLE_WINDOWS == ("1h", "6h", "24h", "7d")
    assert all(key in WINDOWS for key in VISIBLE_WINDOWS)

    markup = chart_keyboard(101, "24h")
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert callbacks == [
        "chart:101:1h",
        "chart:101:6h",
        "chart:101:24h",
        "chart:101:7d",
        "back_to_market",
    ]


def test_chart_risk_classification():
    stable = [1.0, 1.001, 0.999, 1.0005, 1.0002]
    volatile = [1.0, 1.03, 0.98, 1.04, 0.99]
    extreme = [1.0, 1.2, 0.8, 1.25, 0.75]

    assert calculate_volatility(stable) < 0.02
    assert classify_risk(stable)[0] == "کم‌ریسک"
    assert classify_risk(volatile)[0] == "پرنوسان"
    assert classify_risk(extreme)[0] == "سقوط آزاد"


def test_three_day_downtrend_and_smart_insight():
    base = datetime(2026, 9, 16, 12, 0)
    timestamps = [
        base,
        base + timedelta(days=1),
        base + timedelta(days=2),
    ]
    rates = [1.20, 1.10, 1.00]

    assert detect_three_day_downtrend(rates, timestamps) is True
    insight = build_smart_insight(
        market_status=None,
        rates=rates,
        timestamps=timestamps,
        change_7d=None,
    )
    assert insight


@pytest.mark.asyncio
async def test_generate_currency_chart_returns_800x450_png():
    base = datetime(2026, 9, 19, 10, 0)
    timestamps = [
        base + timedelta(minutes=index * 15)
        for index in range(12)
    ]
    rates = [
        0.82,
        0.83,
        0.825,
        0.84,
        0.855,
        0.85,
        0.87,
        0.88,
        0.895,
        0.91,
        0.925,
        0.937,
    ]

    image = await generate_currency_chart(
        currency_code="ARY",
        nation_name="Arya",
        nation_flag="🌍",
        price_history=rates,
        timestamps=timestamps,
        window="24h",
        base_currency="OPX",
        current_rate=0.937,
        market_status="best",
        change_7d=12.0,
    )

    raw = image.getvalue()

    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert _png_size(raw) == (800, 450)
    assert len(raw) > 1_000


def test_chart_rejects_inconsistent_history():
    with pytest.raises(ValueError, match="history_length_mismatch"):
        from app.utils.price_chart import render_price_chart

        render_price_chart(
            [1.0, 1.1, 1.2],
            timestamps=[datetime.utcnow()],
        )
