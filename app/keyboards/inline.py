from decimal import Decimal

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_start", style=ButtonStyle.DANGER),
    ]])



def first_trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚡ اولین معامله‌ام رو انجام بده", callback_data="first_trade_tutorial", style=ButtonStyle.SUCCESS),
    ]])


def trade_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید معامله", callback_data="confirm_first_trade", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="⏭ بعداً", callback_data="skip_first_trade"),
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


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎮 شروع بازی", callback_data="start_game", style=ButtonStyle.PRIMARY)],
            [InlineKeyboardButton(text="❓ راهنما", callback_data="show_help")],
        ]
    )


def nation_selection_keyboard(nations) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🏛 {nation.name} · {nation.currency_code} · {nation.member_count} نفر",
            callback_data=f"join_{nation.nation_id}",
            style=ButtonStyle.SUCCESS,
        )]
        for nation in nations
    ]
    rows.append([
        InlineKeyboardButton(
            text="🏛 ساخت ملت جدید",
            callback_data="create_nation",
            style=ButtonStyle.PRIMARY,
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def nation_panel_keyboard(is_founder: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_founder:
        rows.append([InlineKeyboardButton(
            text="👑 پنل مدیریت",
            callback_data="founder_panel",
            style=ButtonStyle.PRIMARY,
        )])
    rows.extend([
        [InlineKeyboardButton(text="🌍 ملت‌های من", callback_data="my_nations")],
        [InlineKeyboardButton(text="🔍 کاوش ملت‌ها", callback_data="explore_nations")],
        [InlineKeyboardButton(text="🏛 تأسیس ملت", callback_data="create_nation", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تأیید", callback_data="confirm_trade", style=ButtonStyle.SUCCESS),
                InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_trade", style=ButtonStyle.DANGER),
            ]
        ]
    )
