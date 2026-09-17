from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💹 می‌خوام بازی کنم",
                    callback_data="start_player",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="🏛 می‌خوام ملت بسازم",
                    callback_data="start_founder",
                    style=ButtonStyle.PRIMARY,
                ),
            ]
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="cancel_start",
                    style=ButtonStyle.DANGER,
                )
            ]
        ]
    )


def nation_keyboard(nations) -> InlineKeyboardMarkup:
    rows = []
    for nation in nations:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"پیوستن به {nation.name}",
                    callback_data=f"join_nation_{nation.nation_id}",
                    style=ButtonStyle.SUCCESS,
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def no_nation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏛 ساخت ملت",
                    callback_data="start_founder",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        ]
    )


def first_trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ اولین معامله‌ام رو انجام بده",
                    callback_data="first_trade_tutorial",
                    style=ButtonStyle.SUCCESS,
                )
            ]
        ]
    )


def trade_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید معامله",
                    callback_data="confirm_first_trade",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="❌ بعداً",
                    callback_data="skip_first_trade",
                    style=ButtonStyle.DANGER,
                ),
            ]
        ]
    )
