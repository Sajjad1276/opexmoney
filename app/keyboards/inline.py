from decimal import Decimal

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💹 می‌خوام بازی کنم", callback_data="start_player", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="🏛 می‌خوام ملت بسازم", callback_data="start_founder", style=ButtonStyle.PRIMARY),
    ]])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_start", style=ButtonStyle.DANGER)
    ]])


def nation_keyboard(nations) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"پیوستن به {nation.name}", callback_data=f"join_nation_{nation.nation_id}", style=ButtonStyle.SUCCESS)
    ] for nation in nations])


def no_nation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏛 ساخت ملت", callback_data="start_founder", style=ButtonStyle.PRIMARY)
    ]])


def first_trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚡ اولین معامله‌ام رو انجام بده", callback_data="first_trade_tutorial", style=ButtonStyle.SUCCESS)
    ]])


def trade_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید معامله", callback_data="confirm_first_trade", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="❌ بعداً", callback_data="skip_first_trade", style=ButtonStyle.DANGER),
    ]])


def market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 خرید", callback_data="market_buy", style=ButtonStyle.SUCCESS), InlineKeyboardButton(text="📉 فروش", callback_data="market_sell", style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text="📊 نمودار", callback_data="market_chart"), InlineKeyboardButton(text="📜 تاریخچه", callback_data="market_history")],
        [InlineKeyboardButton(text="🔄 آپدیت", callback_data="market_refresh", style=ButtonStyle.PRIMARY)],
    ])


def market_buy_keyboard(nations) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"خرید {n.currency_code}", callback_data=f"buy_{n.nation_id}", style=ButtonStyle.SUCCESS)] for n in nations]
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="market_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_amount_keyboard(nation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="۱۰۰", callback_data=f"buyq_100_{nation_id}", style=ButtonStyle.PRIMARY), InlineKeyboardButton(text="۵۰۰", callback_data=f"buyq_500_{nation_id}", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="۱۰۰۰", callback_data=f"buyq_1000_{nation_id}", style=ButtonStyle.PRIMARY), InlineKeyboardButton(text="همه", callback_data=f"buyq_all_{nation_id}", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_buy", style=ButtonStyle.DANGER)],
    ])


def trade_preview_keyboard(nation_id: int, spend: Decimal | str, side: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأیید و خرید" if side == "buy" else "✅ تأیید و فروش", callback_data=f"c{side}_{nation_id}_{spend}", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_buy" if side == "buy" else "market_sell", style=ButtonStyle.DANGER)],
    ])


def sell_currency_keyboard(holdings) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"فروش {h.currency_code}", callback_data=f"sell_{h.currency_code}", style=ButtonStyle.DANGER)] for h in holdings]
    rows.append([InlineKeyboardButton(text="📈 برو به خرید", callback_data="market_buy", style=ButtonStyle.SUCCESS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sell_amount_keyboard(currency_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="۲۵٪", callback_data=f"sellq_25_{currency_code}", style=ButtonStyle.PRIMARY), InlineKeyboardButton(text="۵۰٪", callback_data=f"sellq_50_{currency_code}", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="۷۵٪", callback_data=f"sellq_75_{currency_code}", style=ButtonStyle.PRIMARY), InlineKeyboardButton(text="همه", callback_data=f"sellq_100_{currency_code}", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_sell", style=ButtonStyle.DANGER)],
    ])
