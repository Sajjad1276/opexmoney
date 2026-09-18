from __future__ import annotations

import html
import re

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database.models import Nation, User
from app.database.session import async_session
from app.keyboards.inline import (
    add_to_group_keyboard,
    buy_amount_keyboard,
    cancel_keyboard,
    confirm_found_nation_keyboard,
    founder_cancel_keyboard,
    governance_confirm_keyboard,
    sell_amount_keyboard,
    nation_selection_keyboard,
)
from app.services.intent_router import expected_input_text
from app.services.keyboard_state import KeyboardKind, keyboard_manager
from app.services.nation_service import get_active_nations
from app.services.onboarding_draft import get_draft
from app.services.rules.registry import RULE_REGISTRY, get_rule
from app.services.rules.resolver import resolve
from app.states.founder import FounderStates
from app.states.governance import GovernanceStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates
from app.utils.formatting import fmt_amount


_ADMIN_LINK_RIGHTS = "delete_messages+restrict_members+invite_users+pin_messages+manage_topics"


async def resume_from_draft(
    message: Message,
    state: FSMContext,
) -> bool:
    async with async_session() as session:
        draft = await get_draft(session, message.from_user.id)
        if draft is None:
            return False

        payload = draft.payload or {}
        step = draft.step_key

        if step == OnboardingStates.SET_USERNAME_PLAYER.state:
            await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text="💹 <b>ثبت‌نامت از همین‌جا ادامه پیدا می‌کنه.</b>\nاسم معامله‌گرت رو بفرست.",
                kind=KeyboardKind.INLINE,
                name="onboarding_username",
                markup=cancel_keyboard(),
                parse_mode="HTML",
            )
            return True

        if step == OnboardingStates.SELECT_NATION.state:
            user = await session.get(User, message.from_user.id)
            nations = await get_active_nations(session, limit=3)
            await state.set_state(OnboardingStates.SELECT_NATION)
            await state.update_data(username=payload.get("username") or getattr(user, "username", None))
            text = "🌍 <b>ادامه ثبت‌نام</b>\n\nملتت رو انتخاب کن."
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=text,
                kind=KeyboardKind.INLINE,
                name="nation_selection",
                markup=nation_selection_keyboard(nations),
                parse_mode="HTML",
            )
            return True

        if step == FounderStates.WAITING_GROUP_ADMIN.state:
            await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
            await state.update_data(
                founder_user_id=message.from_user.id,
                **payload,
            )
            me = await message.bot.get_me()
            group_link = (
                f"https://t.me/{me.username}"
                f"?startgroup=founder_{message.from_user.id}"
                f"&admin={_ADMIN_LINK_RIGHTS}"
            )
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=(
                    "🏛 <b>تأسیس ملت ادامه دارد.</b>\n"
                    "ربات را به گروه پایتخت اضافه و ادمین کن.\n"
                    "بعد از اتصال گروه، مرحله بعد باز می‌شود."
                ),
                kind=KeyboardKind.INLINE,
                name="founder_group",
                markup=add_to_group_keyboard(group_link),
                parse_mode="HTML",
            )
            return True

        if step == FounderStates.SET_NATION_NAME.state:
            await state.set_state(FounderStates.SET_NATION_NAME)
            await state.update_data(**{**payload, "founder_user_id": message.from_user.id})
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=(
                    "🎉 <b>گروه قبلاً متصل شده.</b>\n"
                    f"🏛 پایتخت: <b>{html.escape(str(payload.get('group_title', 'گروه')))}</b>\n"
                    "حالا اسم انگلیسی ملتت رو بفرست."
                ),
                kind=KeyboardKind.INLINE,
                name="founder_cancel",
                markup=founder_cancel_keyboard(),
                parse_mode="HTML",
            )
            return True

        if step == FounderStates.CONFIRM.state:
            await state.set_state(FounderStates.CONFIRM)
            await state.update_data(founder_user_id=message.from_user.id, **payload)
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=(
                    "📋 <b>ادامه تأسیس ملت</b>\n"
                    f"🏛 نام ملت: <b>{html.escape(str(payload.get('nation_name', '—')))}</b>\n"
                    f"💱 کد ارز: <b>{html.escape(str(payload.get('currency_code', '—')))}</b>\n"
                    f"🗺 پایتخت: <b>{html.escape(str(payload.get('group_title', 'گروه')))}</b>"
                ),
                kind=KeyboardKind.INLINE,
                name="founder_confirm",
                markup=confirm_found_nation_keyboard(),
                parse_mode="HTML",
            )
            return True

        if step == MarketStates.WAITING_BUY_AMOUNT.state:
            nation_id = int(payload["nation_id"])
            nation = await session.get(Nation, nation_id)
            if nation is None:
                return False
            await state.set_state(MarketStates.WAITING_BUY_AMOUNT)
            await state.update_data(nation_id=nation_id)
            from app.keyboards.inline import market_keyboard
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=f"📈 <b>خرید {html.escape(nation.currency_code)}</b>\nمقدار ΩXR را وارد کن.",
                kind=KeyboardKind.INLINE,
                name="buy_amount",
                markup=buy_amount_keyboard(nation_id),
                parse_mode="HTML",
            )
            return True

        if step == MarketStates.WAITING_SELL_AMOUNT.state:
            nation_id = int(payload["nation_id"])
            nation = await session.get(Nation, nation_id)
            if nation is None:
                return False
            await state.set_state(MarketStates.WAITING_SELL_AMOUNT)
            await state.update_data(nation_id=nation_id)
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=f"📉 <b>فروش {html.escape(nation.currency_code)}</b>\nمقدار فروش را وارد کن.",
                kind=KeyboardKind.INLINE,
                name="sell_amount",
                markup=sell_amount_keyboard(nation.currency_code),
                parse_mode="HTML",
            )
            return True

        if step == GovernanceStates.WAITING_VALUE.state:
            key = payload.get("rule_key")
            if key not in RULE_REGISTRY:
                return False
            rule = get_rule(key)
            user = await session.get(User, message.from_user.id)
            current = await resolve(
                session,
                key,
                nation_id=user.home_nation_id if user else None,
                player_id=message.from_user.id,
            )
            await state.set_state(GovernanceStates.WAITING_VALUE)
            await state.update_data(rule_key=key)
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=(
                    f"⚙️ <b>{html.escape(rule.title_fa)}</b>\n"
                    f"مقدار فعلی: <b>{html.escape(str(current))}</b>\n"
                    "مقدار پیشنهادی را به عدد بفرست."
                ),
                kind=KeyboardKind.NONE,
                name="governance_value",
                markup=None,
                parse_mode="HTML",
            )
            return True

        if step == GovernanceStates.CONFIRM_PROPOSAL.state:
            await state.set_state(GovernanceStates.CONFIRM_PROPOSAL)
            await state.update_data(**payload)
            await keyboard_manager.send_message(
                message.bot,
                chat_id=message.chat.id,
                text=(
                    "📋 <b>ادامه طرح قانون</b>\n"
                    f"قانون: <b>{html.escape(str(payload.get('rule_key', '—')))}</b>\n"
                    f"مقدار پیشنهادی: <b>{html.escape(str(payload.get('proposed_value', '—')))}</b>"
                ),
                kind=KeyboardKind.INLINE,
                name="governance_confirm",
                markup=governance_confirm_keyboard(),
                parse_mode="HTML",
            )
            return True

    return False
