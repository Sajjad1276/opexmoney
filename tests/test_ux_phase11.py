from __future__ import annotations

from app.keyboards.inline import more_menu_keyboard
from app.keyboards.reply import main_menu_keyboard
from app.ui.navigation import HOME_CALLBACK, MAIN_MENU_LABELS, MORE_MENU_CALLBACKS


def test_navigation_contract_matches_rendered_keyboards():
    texts = [
        button.text
        for row in main_menu_keyboard().keyboard
        for button in row
    ]
    assert texts == list(MAIN_MENU_LABELS)

    callbacks = [
        button.callback_data
        for row in more_menu_keyboard().inline_keyboard
        for button in row
        if button.callback_data is not None
    ]
    assert callbacks == list(MORE_MENU_CALLBACKS)
    assert HOME_CALLBACK in callbacks
