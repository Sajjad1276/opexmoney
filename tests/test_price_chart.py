from datetime import datetime, timedelta
from types import SimpleNamespace

import logging
import re

import pytest

from app.services.chart_service import _empty_chart_data
from app.utils import price_chart


@pytest.mark.parametrize(
    "rates",
    [
        [1.000, 1.001, 0.999],
        [1.000, 1.040, 0.960],
        [1.000, 1.200, 0.800],
    ],
)
def test_chart_rendering_is_emoji_free(caplog, recwarn, rates):
    caplog.set_level(logging.WARNING)

    output = price_chart.render_price_chart(
        rates,
        timestamps=[
            datetime.utcnow() - timedelta(minutes=20),
            datetime.utcnow() - timedelta(minutes=10),
            datetime.utcnow(),
        ],
        currency_code="TST",
        nation_name="Test Nation",
        nation_flag="🌍",
        current_rate=rates[-1],
    )

    assert output.getvalue().startswith(b"\x89PNG")

    source = __import__("inspect").getsource(price_chart)
    assert not re.search(r"[\U0001F000-\U0001FAFF]", source)

    warning_text = "\\n".join(str(item.message) for item in recwarn)
    log_text = "\\n".join(record.getMessage() for record in caplog.records)
    combined = f"{warning_text}\\n{log_text}".lower()
    assert "missing from current font" not in combined
    assert "emoji missing" not in combined


def test_chart_metadata_uses_stored_nation_flag():
    nation = SimpleNamespace(
        exchange_rate=1.25,
        currency_code="IRX",
        name="Iran Max",
        flag_emoji="🇮🇷",
    )

    data = _empty_chart_data(nation, window_hours=24)

    assert data["nation_flag"] == "🇮🇷"


def test_chart_metadata_defaults_to_black_flag_when_flag_is_empty():
    nation = SimpleNamespace(
        exchange_rate=1.25,
        currency_code="IRX",
        name="Iran Max",
        flag_emoji=None,
    )

    data = _empty_chart_data(nation, window_hours=24)

    assert data["nation_flag"] == "🏴"
