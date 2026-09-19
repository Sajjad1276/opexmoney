from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💹 بازار"),
                KeyboardButton(text="📊 پورتفولیو"),
            ],
            [
                KeyboardButton(text="⚡ مأموریت"),
                KeyboardButton(text="🌍 ملت‌ها"),
            ],
            [
                KeyboardButton(text="🏆 رتبه‌بندی"),
                KeyboardButton(text="🎓 آکادمی"),
            ],
            [
                KeyboardButton(text="🏦 خزانه"),
                KeyboardButton(text="📜 قوانین"),
            ],
            [
                KeyboardButton(text="⚙️ تنظیمات"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
