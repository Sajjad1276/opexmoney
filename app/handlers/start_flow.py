from __future__ import annotations

import asyncio
import html
import json
import os
import time
import urllib.request
import logging
import re
from datetime import datetime, time as dt_time
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import CurrencyHolding, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from app.handlers.nation_management import _join_user
from app.filters.profanity import profanity_filter
from app.utils.name_filter import TRADER_NAME_RE, is_blocked_trader_name, is_valid_trader_name
from app.keyboards.inline import (
    cancel_keyboard,
    first_trade_keyboard,
    suggested_name_keyboard,
    nation_selection_keyboard,
    trade_confirmation_keyboard,
    welcome_keyboard,
)
from app.keyboards.reply import main_menu_keyboard, new_player_menu_keyboard
from app.services.nation_service import get_active_nations, get_nation_rank
from app.services.dashboard_service import build_live_dashboard, render_live_dashboard
from app.services.mission_service import check_permanent_missions, increment_mission
from app.services.founder_service import cancel_draft
from app.services.user_service import get_registration_status, get_user, is_fully_registered, sync_user_balance, username_exists
from app.states.founder import FounderStates
from app.states.onboarding import OnboardingStates
from app.handlers.onboarding import _begin_registration, _send_expired, _start_timed_state, _state_is_alive
from app.utils.formatting import rtl_html
from app.utils.ui import safe_edit_caption as _safe_edit_caption, safe_edit_text as _safe_edit_text, user_mention
from config import settings
from app.utils.formatting import (
    fmt_amount,
    fmt_pct,
    fmt_rate,
    get_rate_change,
    get_rate_emoji,
    imperial_datetime,
    to_fa,
)
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel

router = Router(name="start_flow")
logger = logging.getLogger(__name__)

RLM = "\u200f"
ONBOARDING_TIMEOUT = 300
NATIONS_PER_PAGE = 5
INITIAL_BALANCE = Decimal("500.00")






def _welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="شروع بازی 🎮",
                    callback_data="start_game",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        ]
    )


def _nation_profile_keyboard(nation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="همین ملت رو می‌خوام ✅",
                    callback_data="confirm_nation:{0}".format(nation_id),
                    style=ButtonStyle.SUCCESS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="برگشت 🔙",
                    callback_data="back_to_nations",
                )
            ],
        ]
    )


def _nation_page_keyboard(
    nations: list[Nation],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for nation in nations:
        rows.append(
            [
                InlineKeyboardButton(
                    text="{0} {1} · {2} · {3} نفر".format(
                        html.escape(nation.flag_emoji or "🏴"),
                        html.escape(nation.name),
                        html.escape(nation.currency_code),
                        to_fa(nation.member_count),
                    ),
                    callback_data="confirm_nation:{0}".format(nation.nation_id),
                )
            ]
        )

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ قبلی",
                callback_data="nation_page:{0}".format(page - 1),
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="بعدی ➡️",
                callback_data="nation_page:{0}".format(page + 1),
            )
        )
    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            text="🏛 تأسیس ملت",
            callback_data="start_founder",
            style=ButtonStyle.SUCCESS,
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)




async def _safe_edit_text(call: CallbackQuery, text: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_text"):
            return False
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Message edit failed: %s", exc)
        return False


async def _safe_edit_caption(call: CallbackQuery, caption: str, reply_markup=None) -> bool:
    try:
        if call.message is None or not hasattr(call.message, "edit_caption"):
            return False
        await call.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except TelegramBadRequest as exc:
        logger.info("Caption edit failed: %s", exc)
        return False


START_CAPTION = """🌐 <b>{bot_name}</b>
سلام {user_name}.

بازارهای OPEX هر روز
میلیاردها واحد ارز جابه‌جا می‌کنن.
تو کجا می‌ایستی؟"""
)