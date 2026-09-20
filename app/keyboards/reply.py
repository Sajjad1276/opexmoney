from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💹 بازار"),
                KeyboardButton(text="💼 دارایی‌های من"),
            ],
            [
                KeyboardButton(text="🎯 مأموریت"),
                KeyboardButton(text="🌍 ملت من"),
            ],
            [
                KeyboardButton(text="🏆 رتبه‌بندی"),
                KeyboardButton(text="🎓 آکادمی"),
            ],
            [
                KeyboardButton(text="☰ بیشتر"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
