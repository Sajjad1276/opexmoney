from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='💹 بازار'), KeyboardButton(text='📊 پورتفولیو')],
            [KeyboardButton(text='⚡ مأموریت'), KeyboardButton(text='🌍 ملت‌ها')],
            [KeyboardButton(text='🏆 رتبه‌بندی'), KeyboardButton(text='⚙️ تنظیمات')],
        ],
        resize_keyboard=True
    )
