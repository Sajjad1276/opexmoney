from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.services.market_intelligence import (
    format_change_text,
    get_consecutive_trend_days,
    get_risk_label,
    get_smart_insight,
)


def test_format_change_text_rounds_and_marks_direction():
    assert format_change_text(6.30) == "▲ ۶ درصد رشد در ۲۴ ساعت گذشته"
    assert format_change_text(12.7) == "▲ ۱۳ درصد رشد در ۲۴ ساعت گذشته"
    assert format_change_text(0) == "➡️ بدون تغییر"
    assert format_change_text(-6.3, "7d") == "▼ ۶ درصد افت در ۷ روز گذشته"


def test_risk_label():
    assert get_risk_label([1.0, 1.001, 0.999, 1.0005]) == "🟢 کم‌ریسک"
    assert get_risk_label([1.0, 1.05, 0.95, 1.04, 0.96]) in {
        "🟡 پرنوسان",
        "🔴 سقوط آزاد",
    }


def test_signed_daily_trend():
    start = datetime(2026, 1, 1)
    down = [
        (start + timedelta(days=i), Decimal(str(1.0 - i * 0.05)))
        for i in range(5)
    ]
    up = [
        (start + timedelta(days=i), Decimal(str(1.0 + i * 0.05)))
        for i in range(5)
    ]
    assert get_consecutive_trend_days(down) == 5
    assert get_consecutive_trend_days(up) == -5


@pytest.mark.asyncio
async def test_smart_insight_combines_conditions():
    text = await get_smart_insight(
        currency_code="ARY",
        change_24h=-8.0,
        change_7d=-12.7,
        all_currencies_changes={
            "ARY": -8.0,
            "USD": 15.0,
            "PRS": 2.0,
        },
        consecutive_down_days=4,
    )
    assert "بدترین عملکرد" in text
    assert "روند نزولی" in text
    assert "پایین‌تره" in text
