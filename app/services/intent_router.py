from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.inline import add_to_group_keyboard, restart_confirmation_keyboard
from app.keyboards.reply import main_menu_keyboard
from app.services.draft_service import clear_draft, get_draft
from app.services.keyboard_state import keyboard_manager
from app.states.founder import FounderStates
from app.states.governance import GovernanceStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates
from app.utils.name_filter import is_valid_trader_name
from app.utils.validators import validate_nation_name


class IntentType(str, Enum):
    SYSTEM_COMMAND = "system_command"
    STATE_INPUT = "state_input"
    NAVIGATION_TEXT = "navigation_text"
    OFF_TOPIC = "off_topic"
    AMBIGUOUS = "ambiguous"


Validator = Callable[[str], bool]
Action = Callable[..., Awaitable[None]]


def _decimal_input(value: str) -> bool:
    try:
        number = Decimal(value.strip().replace(",", "").replace("٬", "").replace("٫", "."))
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite()



@dataclass(frozen=True)
class StepDefinition:
    state: Any
    expected_input_desc_fa: str
    validator: Validator
    on_valid: Action


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None


def _looks_like_form_input(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    return not any(marker in lowered for marker in QUESTION_MARKERS)


def _looks_like_numeric_step(value: str) -> bool:
    return _looks_like_form_input(value)


STEP_REGISTRY: dict[str, StepDefinition] = {
    OnboardingStates.SET_USERNAME_PLAYER.state: StepDefinition(
        OnboardingStates.SET_USERNAME_PLAYER,
        "اسم معامله‌گرت را بفرست.",
        is_valid_trader_name,
        _noop,
    ),
    OnboardingStates.SELECT_NATION.state: StepDefinition(
        OnboardingStates.SELECT_NATION,
        "یک ملت را با دکمه انتخاب کن.",
        lambda value: False,
        _noop,
    ),
    FounderStates.WAITING_GROUP_ADMIN.state: StepDefinition(
        FounderStates.WAITING_GROUP_ADMIN,
        "گروه پایتخت را وصل کن.",
        lambda value: False,
        _noop,
    ),
    FounderStates.SET_NATION_NAME.state: StepDefinition(
        FounderStates.SET_NATION_NAME,
        "نام انگلیسی ملت را بفرست.",
        lambda value: validate_nation_name(value)[0],
        _noop,
    ),
    FounderStates.CONFIRM.state: StepDefinition(
        FounderStates.CONFIRM,
        "تأسیس ملت را با دکمه تأیید کن.",
        lambda value: False,
        _noop,
    ),
    MarketStates.WAITING_BUY_AMOUNT.state: StepDefinition(
        MarketStates.WAITING_BUY_AMOUNT,
        "مقدار ΩXR برای خرید را به عدد بفرست.",
        _decimal_input,
        _noop,
    ),
    MarketStates.WAITING_SELL_AMOUNT.state: StepDefinition(
        MarketStates.WAITING_SELL_AMOUNT,
        "مقدار ارز برای فروش را به عدد بفرست.",
        _decimal_input,
        _noop,
    ),
    GovernanceStates.WAITING_VALUE.state: StepDefinition(
        GovernanceStates.WAITING_VALUE,
        "مقدار پیشنهادی قانون را به عدد بفرست.",
        _decimal_input,
        _noop,
    ),
    GovernanceStates.CONFIRM_PROPOSAL.state: StepDefinition(
        GovernanceStates.CONFIRM_PROPOSAL,
        "طرح قانون را بررسی و تأیید یا لغو کن.",
        lambda value: False,
        _noop,
    ),
}

COMMANDS = {"/start", "/cancel", "/help"}
REPLY_NAVIGATION_TEXTS = {
    "💹 بازار",
    "📊 پورتفولیو",
    "⚡ مأموریت",
    "🌍 ملت‌ها",
    "🏆 رتبه‌بندی",
    "⚙️ تنظیمات",
}
QUESTION_MARKERS = ("چطور", "چرا", "کمک", "راهنما", "؟", "?", "چی", "کجاست")


def _command_name(text: str) -> str:
    token = text.strip().split()[0].casefold()
    return token.split("@", 1)[0]


async def classify_intent(message: Message, current_state: str | None, registry: Any | None = None) -> IntentType:
    text = (message.text or "").strip()
    if text.startswith("/") and _command_name(text) in COMMANDS:
        return IntentType.SYSTEM_COMMAND
    if current_state is None and text in REPLY_NAVIGATION_TEXTS:
        return IntentType.NAVIGATION_TEXT
    definition = STEP_REGISTRY.get(current_state or "")
    if definition is not None and definition.validator(text):
        return IntentType.STATE_INPUT
    if any(marker in text.casefold() for marker in QUESTION_MARKERS):
        return IntentType.OFF_TOPIC
    return IntentType.AMBIGUOUS


def _state_description(current_state: str | None) -> str:
    return STEP_REGISTRY.get(current_state or "", StepDefinition(None, "ورودی موردنیاز این مرحله", lambda value: False, _noop)).expected_input_desc_fa


async def cancel_current_flow(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ الان فرآیند فعالی نداری.")
        return
    await state.clear()
    await clear_draft(message.from_user.id)
    await keyboard_manager.send(message, "❌ فرآیند فعلی لغو شد.\n\nهر وقت آماده بودی از منوی اصلی ادامه بده.", kind="reply:main", markup=main_menu_keyboard())


async def _request_restart(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        from app.handlers.start import start
        await start(message, state)
        return
    expected = _state_description(current_state)
    await state.update_data(_pending_system_command="start")
    await keyboard_manager.send_ephemeral_inline(
        message,
        f"⚠️ وسط یک مرحله هستی.\n{expected}\n\nاگر /start را ادامه بدهی، مرحله فعلی لغو می‌شود. ادامه بدهم؟",
        restart_confirmation_keyboard(),
        parse_mode="HTML",
    )


async def _execute_navigation(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "💹 بازار":
        from app.handlers.market import open_market
        await open_market(message)
    elif text == "🌍 ملت‌ها":
        from app.handlers.nation import open_nations
        await open_nations(message)
    elif text == "📊 پورتفولیو":
        from app.handlers.sections import portfolio
        await portfolio(message)
    elif text == "⚡ مأموریت":
        from app.handlers.sections import missions
        await missions(message)
    elif text == "🏆 رتبه‌بندی":
        from app.handlers.sections import ranking
        await ranking(message)
    elif text == "⚙️ تنظیمات":
        from app.handlers.sections import settings
        await settings(message)


async def handle_system_command(message: Message, state: FSMContext) -> None:
    command = _command_name((message.text or "").strip())
    if command == "/cancel":
        await cancel_current_flow(message, state)
    elif command == "/help":
        from app.handlers.start import _show_help
        await _show_help(message, preserve_keyboard=await state.get_state() is not None)
    elif command == "/start":
        await _request_restart(message, state)


async def _resume_prompt(message: Message, state: FSMContext, draft: Any) -> None:
    step = draft.step_key
    payload = dict(draft.payload or {})
    await state.update_data(**payload)

    if step == "onboarding.set_username":
        target = OnboardingStates.SET_USERNAME_PLAYER
    elif step == "onboarding.select_nation":
        target = OnboardingStates.SELECT_NATION
    elif step == "founder.waiting_group_admin":
        target = FounderStates.WAITING_GROUP_ADMIN
    elif step == "founder.set_nation_name":
        target = FounderStates.SET_NATION_NAME
    elif step == "founder.confirm":
        target = FounderStates.CONFIRM
    elif step == "market.waiting_buy_amount":
        target = MarketStates.WAITING_BUY_AMOUNT
    elif step == "market.waiting_sell_amount":
        target = MarketStates.WAITING_SELL_AMOUNT
    elif step == "governance.waiting_value":
        target = GovernanceStates.WAITING_VALUE
    elif step == "governance.confirm_proposal":
        target = GovernanceStates.CONFIRM_PROPOSAL
    else:
        await state.clear()
        await clear_draft(message.from_user.id)
        await message.answer("⚠️ مرحله ذخیره‌شده شناخته نشد. فرآیند قدیمی پاک شد.")
        return

    await state.set_state(target)

    if step == "onboarding.select_nation":
        from app.handlers.onboarding_fix import show_nation_selection
        from app.database.session import async_session
        async with async_session() as session:
            await show_nation_selection(message, session, state)
        return

    if step == "founder.waiting_group_admin":
        from app.handlers.founder import founder_cancel_keyboard
        await message.answer(
            "🏛 ادامه تأسیس ملت\nگروه پایتخت هنوز متصل نشده. لینک قبلی را استفاده کن و بعد از ادمین شدن ربات ادامه بده.",
            reply_markup=founder_cancel_keyboard(),
        )
        return

    if step == "founder.set_nation_name":
        from app.handlers.founder import founder_cancel_keyboard
        await keyboard_manager.send(
            message,
            "🏛 مرحله بعد: نام انگلیسی ملتت را بفرست.",
            kind="inline:founder",
            markup=founder_cancel_keyboard(),
        )
        return

    if step == "founder.confirm":
        from app.keyboards.inline import confirm_found_nation_keyboard
        await message.answer(
            f"📋 تأسیس ذخیره‌شده آماده تأیید است.\n🏛 {payload.get('nation_name', 'ملت')}\n💱 {payload.get('currency_code', '---')}",
            reply_markup=confirm_found_nation_keyboard(),
        )
        return

    if step == "market.waiting_buy_amount":
        from app.keyboards.inline import buy_amount_keyboard
        await keyboard_manager.send(
            message,
            "📈 مقدار ΩXR برای خرید را وارد کن.",
            kind="inline:market-buy",
            markup=buy_amount_keyboard(int(payload["nation_id"])),
        )
        return

    if step == "market.waiting_sell_amount":
        from app.keyboards.inline import sell_amount_keyboard
        await keyboard_manager.send(
            message,
            "📉 مقدار ارز برای فروش را وارد کن.",
            kind="inline:market-sell",
            markup=sell_amount_keyboard(str(payload["currency_code"])),
        )
        return

    if step == "governance.waiting_value":
        from app.keyboards.inline import governance_cancel_keyboard
        await keyboard_manager.send(
            message,
            "📜 مقدار پیشنهادی قانون را وارد کن.",
            kind="inline:governance-value",
            markup=governance_cancel_keyboard(),
        )
        return

    if step == "governance.confirm_proposal":
        from app.keyboards.inline import governance_confirm_keyboard
        await keyboard_manager.send(
            message,
            "📋 طرح ذخیره‌شده آماده تأیید است.",
            kind="inline:governance-confirm",
            markup=governance_confirm_keyboard(),
        )
        return

    await keyboard_manager.send(
        message,
        _state_description(target.state),
        kind="inline:resume",
        markup=None,
    )


class IntentRoutingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)
        if getattr(getattr(event, "chat", None), "type", None) != "private":
            return await handler(event, data)
        state: FSMContext | None = data.get("state")
        current_state = await state.get_state() if state is not None else None
        intent = await classify_intent(event, current_state, STEP_REGISTRY)
        if intent == IntentType.SYSTEM_COMMAND:
            await handle_system_command(event, state)
            return None
        if intent == IntentType.NAVIGATION_TEXT:
            await _execute_navigation(event, state)
            return None
        if intent == IntentType.OFF_TOPIC:
            await event.answer(f"ℹ️ الان منتظر این هستم: {_state_description(current_state)}\nاین مرحله را ترک نکردم.")
            return None
        if intent == IntentType.AMBIGUOUS and current_state is not None:
            await event.answer(f"ℹ️ متوجه نشدم. الان منتظر این هستم: {_state_description(current_state)}\nیا /cancel بزن.")
            return None
        return await handler(event, data)


interaction_router = Router(name="intent_router")


@interaction_router.callback_query(F.data == "confirm_restart")
async def confirm_restart(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("_pending_system_command") != "start":
        await call.answer("این درخواست منقضی شده.", show_alert=True)
        return
    await state.clear()
    await clear_draft(call.from_user.id)
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass
        from app.handlers.start import start
        from app.services.keyboard_state import keyboard_manager
        await keyboard_manager.send(call.message, "🌐 شروع دوباره.", kind="reply:main", markup=main_menu_keyboard())
        # Start sends the actual welcome screen in the next step.
        await start(call.message, state)
    await call.answer()


@interaction_router.callback_query(F.data == "keep_wizard")
async def keep_wizard(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("_pending_system_command") != "start":
        await call.answer("این درخواست منقضی شده.", show_alert=True)
        return
    await state.update_data(_pending_system_command=None)
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass
    await call.answer("ادامه می‌دهیم.")


@interaction_router.callback_query(F.data == "resume_draft")
async def resume_draft(call: CallbackQuery, state: FSMContext) -> None:
    draft = await get_draft(call.from_user.id)
    if draft is None:
        await call.answer("داده‌ای برای ادامه پیدا نشد.", show_alert=True)
        return
    if call.message:
        await call.message.delete()
        await _resume_prompt(call.message, state, draft)
    await call.answer("ادامه داده شد.")


@interaction_router.callback_query(F.data == "discard_draft")
async def discard_draft(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await clear_draft(call.from_user.id)
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass
        await keyboard_manager.send(call.message, "فرآیند ذخیره‌شده پاک شد.", kind="reply:main", markup=main_menu_keyboard())
    await call.answer()