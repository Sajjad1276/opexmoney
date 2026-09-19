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
        [InlineKeyboardButton(text="🎮 شروع بازی", callback_data="start_game", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="❓ راهنما", callback_data="show_help", style=ButtonStyle.PRIMARY)],
    ])


def market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خرید", callback_data="market_buy", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="🔴 فروش", callback_data="market_sell", style=ButtonStyle.DANGER),
        ],
        [InlineKeyboardButton(text="📜 تاریخچه", callback_data="market_history", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="🔄 بروزرسانی", callback_data="market_refresh", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")],
    ])



def nation_panel_keyboard(
    is_founder: bool = False,
    nation_id: int | None = None,
    *,
    is_manager: bool | None = None,
) -> InlineKeyboardMarkup:
    can_manage = is_founder if is_manager is None else is_manager
    buttons = [
        [InlineKeyboardButton(text="🌍 ملت‌های من", callback_data="my_nations", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="🔍 کاوش ملت‌ها", callback_data="explore_nations", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="🏛 تأسیس ملت", callback_data="found_nation", style=ButtonStyle.SUCCESS)],
    ]

    if nation_id is not None:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🏦 خزانه",
                    callback_data=f"treasury:show:{nation_id}:nations",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="📜 قانون اساسی",
                    callback_data="governance_main",
                    style=ButtonStyle.PRIMARY,
                ),
            ]
        )
        if can_manage:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="👑 پنل مدیریت",
                        callback_data=f"nm:panel:{nation_id}",
                        style=ButtonStyle.PRIMARY,
                    ),
                    InlineKeyboardButton(
                        text="⚔️ جنگ",
                        callback_data=f"nm:wars:{nation_id}",
                        style=ButtonStyle.DANGER,
                    ),
                ]
            )
    else:
        buttons.append(
            [InlineKeyboardButton(text="📜 قانون اساسی", callback_data="governance_main", style=ButtonStyle.PRIMARY)]
        )

    buttons.append(
        [InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_to_group_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ افزودن ربات به گروه",
                url=url,
                style=ButtonStyle.PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="🔎 بررسی و دریافت اطلاعات",
                callback_data="founder_check_group",
                style=ButtonStyle.SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="cancel_founder",
                style=ButtonStyle.DANGER,
            )
        ],
    ])


def confirm_found_nation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأسیس ملت", callback_data="confirm_found", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_founder", style=ButtonStyle.DANGER),
    ]])





def founder_flag_selection_keyboard() -> InlineKeyboardMarkup:
    flags = [
        ("🚩", "پرچم سرخ"),
        ("🏳", "پرچم سفید"),
        ("🎌", "پرچم‌های دوقلو"),
        ("🏁", "شطرنجی"),
        ("🇮🇷", "ایران"),
        ("🇫🇮", "فنلاند"),
        ("🇺🇸", "آمریکا"),
        ("🇯🇵", "ژاپن"),
        ("🇩🇪", "آلمان"),
        ("🇧🇷", "برزیل"),
        ("🇫🇷", "فرانسه"),
        ("🇹🇷", "ترکیه"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{flag} {label}",
                callback_data=f"founder_flag:{flag}",
                style=ButtonStyle.PRIMARY,
            )
            for flag, label in flags[index:index + 3]
        ]
        for index in range(0, len(flags), 3)
    ]
    rows.append([
        InlineKeyboardButton(
            text="🏴 پیش‌فرض",
            callback_data="founder_flag:default",
            style=ButtonStyle.PRIMARY,
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="❌ انصراف",
            callback_data="cancel_founder",
            style=ButtonStyle.DANGER,
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def nation_selection_keyboard(nations) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{getattr(nation, 'flag_emoji', None) or '🏴'} {nation.name} ({nation.currency_code}) · {nation.member_count} نفر",
            callback_data=f"join_nation:{nation.nation_id}",
            style=ButtonStyle.PRIMARY,
        )]
        for nation in nations
    ]
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
            InlineKeyboardButton(text="100", callback_data=f"buyq_100_{nation_id}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="500", callback_data=f"buyq_500_{nation_id}", style=ButtonStyle.PRIMARY),
        ],
        [
            InlineKeyboardButton(text="1000", callback_data=f"buyq_1000_{nation_id}", style=ButtonStyle.PRIMARY),
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
            InlineKeyboardButton(text="25%", callback_data=f"sellq_25_{currency_code}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="50%", callback_data=f"sellq_50_{currency_code}", style=ButtonStyle.PRIMARY),
        ],
        [
            InlineKeyboardButton(text="75%", callback_data=f"sellq_75_{currency_code}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="همه", callback_data=f"sellq_100_{currency_code}", style=ButtonStyle.PRIMARY),
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="market_sell", style=ButtonStyle.DANGER)],
    ])


def governance_main_keyboard(is_founder: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📜 قوانین فعال", callback_data="gov_active", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="📝 ثبت طرح جدید", callback_data="gov_new", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="🗳 رأی‌گیری‌های جاری", callback_data="gov_voting", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="📚 تاریخ قوانین", callback_data="gov_history:0", style=ButtonStyle.PRIMARY)],
    ]
    if is_founder:
        rows.append([InlineKeyboardButton(text="👑 لغو فوری قانون", callback_data="gov_revoke_list", style=ButtonStyle.DANGER)])
    rows.append([InlineKeyboardButton(text="↩️ بازگشت", callback_data="back_to_dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_rule_keyboard(rules: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"gov_rule:{key}", style=ButtonStyle.PRIMARY)]
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
    from app.services.rules.registry import RULE_REGISTRY

    rows = [
        [InlineKeyboardButton(
            text=f"🗳 {RULE_REGISTRY.get(proposal.rule_key).title_fa if proposal.rule_key in RULE_REGISTRY else proposal.rule_key} · #{proposal.id}",
            callback_data=f"gov_proposal:{proposal.id}",
            style=ButtonStyle.PRIMARY,
        )]
        for proposal in proposals
    ]
    rows.append([InlineKeyboardButton(text="↩️ قوانین", callback_data="governance_main", style=ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def governance_history_keyboard(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"gov_history:{max(0, offset - 8)}", style=ButtonStyle.PRIMARY)])
    if has_next:
        rows.append([InlineKeyboardButton(text="بعدی ➡️", callback_data=f"gov_history:{offset + 8}", style=ButtonStyle.PRIMARY)])
    rows.append([InlineKeyboardButton(text="↩️ قوانین", callback_data="governance_main", style=ButtonStyle.PRIMARY)])
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
