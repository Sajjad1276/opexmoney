from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='💹 می‌خوام بازی کنم', callback_data='start_player'),
            InlineKeyboardButton(text='🏛 می‌خوام ملت بسازم', callback_data='start_founder')
        ]
    ])
