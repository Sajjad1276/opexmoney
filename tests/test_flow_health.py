from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.diagnostics.flow_health import assert_flow_health
from app.keyboards.inline import (
    buy_amount_keyboard,
    cancel_keyboard,
    confirm_found_nation_keyboard,
    confirm_trade_keyboard,
    first_trade_keyboard,
    governance_confirm_keyboard,
    governance_history_keyboard,
    governance_main_keyboard,
    governance_proposal_list_keyboard,
    governance_revoke_keyboard,
    governance_rule_keyboard,
    governance_vote_keyboard,
    market_buy_keyboard,
    market_keyboard,
    nation_panel_keyboard,
    nation_selection_keyboard,
    sell_amount_keyboard,
    sell_currency_keyboard,
    trade_confirmation_keyboard,
    trade_preview_keyboard,
    welcome_keyboard,
)
from app.keyboards.reply import main_menu_keyboard, new_player_menu_keyboard


def _button_callback_values(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


def _assert_markup_has_no_broken_buttons(markup) -> None:
    assert markup.inline_keyboard, "keyboard is empty"
    for row in markup.inline_keyboard:
        assert row, "keyboard contains an empty row"
        for button in row:
            assert button.text, "button text is empty"
            assert button.callback_data or button.url, (
                f"button '{button.text}' has neither callback_data nor url"
            )


def test_global_flow_contract_has_no_orphaned_controls():
    report = assert_flow_health()
    assert report.metrics["callback_buttons"] > 0
    assert report.metrics["callback_handlers"] > 0
    assert report.metrics["reply_buttons"] > 0
    assert report.metrics["reply_handlers"] > 0


@pytest.mark.parametrize(
    "factory,args",
    [
        (cancel_keyboard, ()),
        (first_trade_keyboard, ()),
        (trade_confirmation_keyboard, ()),
        (welcome_keyboard, ()),
        (market_keyboard, ()),
        (confirm_trade_keyboard, ()),
        (nation_panel_keyboard, ()),
        (nation_panel_keyboard, (True,)),
        (confirm_found_nation_keyboard, ()),
        (governance_main_keyboard, ()),
        (governance_main_keyboard, (True,)),
        (governance_confirm_keyboard, ()),
        (buy_amount_keyboard, (101,)),
        (sell_amount_keyboard, ("USD",)),
        (trade_preview_keyboard, (101, "100", "buy")),
        (trade_preview_keyboard, (101, "50", "sell")),
        (governance_history_keyboard, (0, False)),
        (governance_history_keyboard, (8, True)),
        (
            nation_selection_keyboard,
            ([SimpleNamespace(name="Test", currency_code="TST", nation_id=101, member_count=1)],),
        ),
        (
            market_buy_keyboard,
            ([SimpleNamespace(currency_code="TST", nation_id=101)],),
        ),
        (
            sell_currency_keyboard,
            ([SimpleNamespace(currency_code="TST")],),
        ),
        (
            governance_rule_keyboard,
            ([("market.tx_fee", "کارمزد")],),
        ),
        (
            governance_vote_keyboard,
            (101,),
        ),
        (
            governance_proposal_list_keyboard,
            ([SimpleNamespace(id=101, rule_key="market.tx_fee")],),
        ),
        (
            governance_revoke_keyboard,
            ([SimpleNamespace(id=101, rule_key="market.tx_fee")],),
        ),
    ],
)
def test_all_inline_keyboard_factories_render_valid_buttons(factory, args):
    markup = factory(*args)
    _assert_markup_has_no_broken_buttons(markup)
    for callback in _button_callback_values(markup):
        assert len(callback.encode("utf-8")) <= 64, (
            f"Telegram callback_data exceeds 64 bytes: {callback!r}"
        )


def test_dynamic_keyboard_callbacks_are_not_empty():
    markup = market_buy_keyboard(
        [SimpleNamespace(currency_code="TST", nation_id=101)]
    )
    assert _button_callback_values(markup) == ["buy_101", "market_main"]

    markup = nation_selection_keyboard(
        [SimpleNamespace(name="Test", currency_code="TST", nation_id=101, member_count=1)]
    )
    assert _button_callback_values(markup) == ["confirm_nation:101", "start_founder"]

    markup = trade_preview_keyboard(101, "100", "buy")
    assert "cbuy_101_100" in _button_callback_values(markup)

    markup = trade_preview_keyboard(101, "100", "sell")
    assert "csell_101_100" in _button_callback_values(markup)


def test_new_player_reply_keyboard_is_focused():
    texts = [
        button.text
        for row in new_player_menu_keyboard().keyboard
        for button in row
    ]
    assert texts == ["🎯 قدم بعدی", "💰 پول من", "🌍 ملت من"]


def test_main_menu_return_button_is_explicit():
    callbacks = _button_callback_values(market_keyboard())
    assert "back_to_dashboard" in callbacks


def test_first_trade_keyboard_starts_the_trade_directly():
    callbacks = _button_callback_values(first_trade_keyboard())
    assert callbacks == ["confirm_first_trade", "skip_first_trade"]


def test_main_reply_keyboard_has_only_text_buttons():
    markup = main_menu_keyboard()
    rows = markup.keyboard
    assert rows
    texts = [button.text for row in rows for button in row]
    assert texts
    assert all(text.strip() for text in texts)


def test_required_main_menu_sections_exist():
    texts = [
        button.text
        for row in main_menu_keyboard().keyboard
        for button in row
    ]
    required = {
        "💹 بازار",
        "💼 دارایی‌های من",
        "🎯 مأموریت",
        "🌍 ملت من",
        "🏆 رتبه‌بندی",
        "⚙️ تنظیمات",
    }
    assert required.issubset(set(texts))
