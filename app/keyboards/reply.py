from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


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
                KeyboardButton(text="👤 پروفایل"),
            ],
            [
                KeyboardButton(text="🎓 آکادمی"),
            ],
            [
                KeyboardButton(text="🛟 پشتیبانی هوشمند"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
