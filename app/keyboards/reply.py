from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.ui.navigation import MAIN_MENU_LABELS


def new_player_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎯 قدم بعدی"),
                KeyboardButton(text="💰 پول من"),
            ],
            [
                KeyboardButton(text="🌍 ملت من"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    assert len(MAIN_MENU_LABELS) == 7
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=MAIN_MENU_LABELS[0]),
                KeyboardButton(text=MAIN_MENU_LABELS[1]),
            ],
            [
                KeyboardButton(text=MAIN_MENU_LABELS[2]),
                KeyboardButton(text=MAIN_MENU_LABELS[3]),
            ],
            [
                KeyboardButton(text=MAIN_MENU_LABELS[4]),
                KeyboardButton(text=MAIN_MENU_LABELS[5]),
            ],
            [
                KeyboardButton(text=MAIN_MENU_LABELS[6]),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
