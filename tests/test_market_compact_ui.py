from __future__ import annotations

from app.keyboards.inline import market_keyboard


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_market_keyboard_uses_compact_market_tools():
    callbacks = _callbacks(market_keyboard())

    assert "market_chart_select" in callbacks
    assert "alert_create" in callbacks
    assert "alert:list" in callbacks

    assert not any(
        callback.startswith("alert:set:")
        for callback in callbacks
    )
    assert not any(
        callback.startswith("market_chart:")
        for callback in callbacks
    )


def test_market_keyboard_keeps_trade_actions():
    callbacks = _callbacks(market_keyboard())

    assert "market_buy" in callbacks
    assert "market_sell" in callbacks
    assert "market_history" in callbacks
    assert "market_refresh" in callbacks
