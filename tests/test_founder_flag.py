from app.keyboards.inline import founder_flag_selection_keyboard, nation_selection_keyboard


def test_founder_flag_keyboard_has_expected_options_and_default():
    keyboard = founder_flag_selection_keyboard()
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    flag_callbacks = [item for item in callbacks if item.startswith("founder_flag:")]

    assert len(flag_callbacks) == 13
    assert "founder_flag:default" in flag_callbacks
    assert len(set(flag_callbacks)) == len(flag_callbacks)
    assert "cancel_founder" in callbacks


def test_nation_selection_keyboard_uses_persisted_flag():
    class NationStub:
        flag_emoji = "🇯🇵"
        name = "Sakura"
        currency_code = "SAK"
        member_count = 7
        nation_id = 42

    keyboard = nation_selection_keyboard([NationStub()])
    button = keyboard.inline_keyboard[0][0]

    assert button.text.startswith("🇯🇵 Sakura")
    assert button.callback_data == "confirm_nation:42"
