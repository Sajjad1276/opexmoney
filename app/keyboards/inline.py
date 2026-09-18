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


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 شروع بازی", callback_data="start_game")],
        [InlineKeyboardButton(text="❓ راهنما", callback_data="show_help")],
    ])


def market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خرید", callback_data="market_buy", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="🔴 فروش", callback_data="market_sell", style=ButtonStyle.DANGER),
        ],
        [InlineKeyboardButton(text="📜 تاریخچه", callback_data="market_history")],
        [InlineKeyboardButton(text="🔄 بروزرسانی", callback_data="market_refresh")],
        [InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")],
    ])


def confirm_trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید", callback_data="confirm_trade", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_trade", style=ButtonStyle.DANGER),
    ]])


def nation_panel_keyboard(is_founder: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🌍 ملت‌های من", callback_data="my_nations")],
        [InlineKeyboardButton(text="🔍 کاوش ملت‌ها", callback_data="explore_nations")],
        [InlineKeyboardButton(text="🏛 تأسیس ملت", callback_data="found_nation")],
        [InlineKeyboardButton(text="📜 قانون اساسی", callback_data="governance_main")],
    ]
    if is_founder:
        buttons.insert(0, [
            InlineKeyboardButton(text="👑 پنل مدیریت", callback_data="founder_panel")
        ])
    buttons.append([
        InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_to_group_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ افزودن ربات به گروه", url=url),
    ], [
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_founder"),
    ]])


def confirm_found_nation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأسیس ملت", callback_data="confirm_found"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_founder"),
    ]])


def nation_selection_keyboard(nations) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🏴 {nation.name} ({nation.currency_code}) · {nation.member_count} نفر",
            callback_data=f"join_nation:{nation.nation_id}",
        )]
        for nation in nations
    ]
    rows.append([
        InlineKeyboardButton(
            text="🏛 ساخت ملت جدید",
            callback_data="found_nation",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_buy_keyboard(nations) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"خرید {nation.currency_code}",
                callback_data=f"buy_{nation.nation_id}",
                style=ButtonStyle.SUCCESS,
            )
        ]
        for nation in nations
    ]
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="market_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_amount_keyboard(nation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="۱۰۰", callback_data=f"buyq_100_{nation_id}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="۵۰۰", callback_data=f"buyq_500_{nation_id}", style=ButtonStyle.PRIMARY),
        ],
        [
            InlineKeyboardButton(text="۱۰۰۰", callback_data=f"buyq_1000_{nation_id}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="همه", callback_data=f"buyq_all_{nation_id}", style=ButtonStyle.PRIMARY),
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_buy", style=ButtonStyle.DANGER)],
    ])


def trade_preview_keyboard(nation_id: int, spend: Decimal | str, side: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ تأیید و خرید" if side == "buy" else "✅ تأیید و فروش",
                callback_data=f"c{side}_{nation_id}_{spend}",
                style=ButtonStyle.SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="market_buy" if side == "buy" else "market_sell",
                style=ButtonStyle.DANGER,
            )
        ],
    ])


def sell_currency_keyboard(holdings) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"فروش {holding.currency_code}",
                callback_data=f"sell_{holding.currency_code}",
                style=ButtonStyle.DANGER,
            )
        ]
        for holding in holdings
    ]
    rows.append([InlineKeyboardButton(text="📈 برو به خرید", callback_data="market_buy", style=ButtonStyle.SUCCESS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sell_amount_keyboard(currency_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="۲۵٪", callback_data=f"sellq_25_{currency_code}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="۵۰٪", callback_data=f"sellq_50_{currency_code}", style=ButtonStyle.PRIMARY),
        ],
        [
            InlineKeyboardButton(text="۷۵٪", callback_data=f"sellq_75_{currency_code}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="همه", callback_data=f"sellq_100_{currency_code}", style=ButtonStyle.PRIMARY),
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_sell", style=ButtonStyle.DANGER)],
    ])


def governance_main_keyboard(is_founder: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📜 قوانین فعال", callback_data="gov_active")],
        [InlineKeyboardButton(text="📝 ثبت طرح جدید", callback_data="gov_new")],
        [InlineKeyboardButton(text="🗳 رأی‌گیری‌های جاری", callback_data="gov_voting")],
        [InlineKeyboardButton(text="📚 تاریخ قوانین", callback_data="gov_history:0")],
    ]
    if is_founder:
        rows.append([InlineKeyboardButton(text="👑 لغو فوری قانون", callback_data="gov_revoke_list")])
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_rule_keyboard(rules: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"gov_rule:{key}")]
        for key, title in rules
    ]
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="gov_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ثبت طرح", callback_data="gov_confirm", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="❌ انصراف", callback_data="gov_cancel", style=ButtonStyle.DANGER),
    ]])


def governance_vote_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ موافق", callback_data=f"gov_vote:{proposal_id}:for", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="❌ مخالف", callback_data=f"gov_vote:{proposal_id}:against", style=ButtonStyle.DANGER),
        ],
        [InlineKeyboardButton(text="⚪ ممتنع", callback_data=f"gov_vote:{proposal_id}:abstain")],
    ])


def governance_proposal_list_keyboard(proposals) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🗳 {proposal.rule_key} · #{proposal.id}",
            callback_data=f"gov_proposal:{proposal.id}",
        )]
        for proposal in proposals
    ]
    rows.append([InlineKeyboardButton(text="↩️ قوانین", callback_data="governance_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_history_keyboard(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"gov_history:{max(0, offset - 8)}")])
    if has_next:
        rows.append([InlineKeyboardButton(text="بعدی ➡️", callback_data=f"gov_history:{offset + 8}")])
    rows.append([InlineKeyboardButton(text="↩️ قوانین", callback_data="governance_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_revoke_keyboard(overrides) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"لغو {override.rule_key} #{override.id}",
            callback_data=f"gov_revoke:{override.id}",
            style=ButtonStyle.DANGER,
        )]
        for override in overrides
    ]
    rows.append([InlineKeyboardButton(text="↩️ قوانین", callback_data="governance_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
