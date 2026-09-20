from __future__ import annotations

from aiogram.enums import ButtonStyle

from app.keyboards.inline import (
    listed_currencies_keyboard,
    market_keyboard,
)


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_market_keyboard_uses_compact_market_tools():
    markup = market_keyboard()
    callbacks = _callbacks(markup)

    assert markup.inline_keyboard[0][0].text == "📋 ارزهای لیست شده"
    assert markup.inline_keyboard[0][0].callback_data == "market_listed_currencies"
    assert markup.inline_keyboard[0][0].style == ButtonStyle.PRIMARY

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


def test_legacy_market_keyboard_wrapper_is_also_compact():
    from app.handlers.market import market_keyboard_for_nation

    callbacks = _callbacks(market_keyboard_for_nation(123))
    assert "market_chart_select" in callbacks
    assert not any(
        callback.startswith("market_chart:")
        for callback in callbacks
    )

def test_listed_currencies_keyboard_paginates_with_previous_and_next():
    first = listed_currencies_keyboard(page=0, total_pages=10)
    first_callbacks = _callbacks(first)
    assert "market_listed:1" in first_callbacks
    assert "market_listed:-1" not in first_callbacks
    assert first.inline_keyboard[0][1].text == "صفحه 1 از 10"

    last = listed_currencies_keyboard(page=9, total_pages=10)
    last_callbacks = _callbacks(last)
    assert "market_listed:8" in last_callbacks
    assert "market_listed:10" not in last_callbacks
    assert last.inline_keyboard[0][1].text == "صفحه 10 از 10"
