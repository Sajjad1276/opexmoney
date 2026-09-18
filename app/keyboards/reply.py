from aiogram.enums import ButtonStyle
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_welcome_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 شروع بازی")],
            [KeyboardButton(text="❓ راهنما")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💹 بازار", style=ButtonStyle.SUCCESS),
                KeyboardButton(text="📊 پورتفولیو", style=ButtonStyle.PRIMARY),
            ],
            [
                KeyboardButton(text="⚡ مأموریت", style=ButtonStyle.SUCCESS),
                KeyboardButton(text="🌍 ملت‌ها", style=ButtonStyle.PRIMARY),
            ],
            [
                KeyboardButton(text="🏆 رتبه‌بندی", style=ButtonStyle.PRIMARY),
                KeyboardButton(text="⚙️ تنظیمات", style=ButtonStyle.PRIMARY),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
