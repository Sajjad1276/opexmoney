from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable

from aiogram import BaseMiddleware

from app.states.founder import FounderStates
from app.states.governance import GovernanceStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates
from app.utils.name_filter import is_valid_trader_name
from app.utils.validators import validate_nation_name


class IntentType(StrEnum):
    SYSTEM_COMMAND = "system_command"
    STATE_INPUT = "state_input"
    NAVIGATION_TEXT = "navigation_text"
    OFF_TOPIC = "off_topic"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class StepDefinition:
    state: object
    expected_input_desc_fa: str
    validator: Callable[[str, object | None], bool]
    on_valid: Callable[..., Awaitable[None]]


async def _noop(*args, **kwargs) -> None:
    return None


def _decimal_validator(text: str, _: object | None = None) -> bool:
    try:
        value = Decimal(
            (text or "")
            .replace("٬", "")
            .replace(",", "")
            .replace("٫", ".")
        )
    except (InvalidOperation, ValueError):
        return False
    return value > Decimal("0")


def _governance_validator(text: str, context: object | None = None) -> bool:
    return bool((text or "").strip())


STEP_DEFINITIONS = {
    OnboardingStates.SET_USERNAME_PLAYER.state: StepDefinition(
        state=OnboardingStates.SET_USERNAME_PLAYER,
        expected_input_desc_fa="اسم معامله‌گرت، ۳ تا ۱۵ حرف انگلیسی یا عدد",
        validator=lambda text, _: is_valid_trader_name(text or ""),
        on_valid=_noop,
    ),
    OnboardingStates.SELECT_NATION.state: StepDefinition(
        state=OnboardingStates.SELECT_NATION,
        expected_input_desc_fa="انتخاب یک ملت از دکمه‌ها",
        validator=lambda _text, _ctx: False,
        on_valid=_noop,
    ),
    FounderStates.WAITING_GROUP_ADMIN.state: StepDefinition(
        state=FounderStates.WAITING_GROUP_ADMIN,
        expected_input_desc_fa="اضافه کردن ربات به گروه و ادمین کردن آن",
        validator=lambda _text, _ctx: False,
        on_valid=_noop,
    ),
    FounderStates.SET_NATION_NAME.state: StepDefinition(
        state=FounderStates.SET_NATION_NAME,
        expected_input_desc_fa="نام انگلیسی ملت، با حروف انگلیسی و فاصله",
        validator=lambda text, _: validate_nation_name(text or "")[0],
        on_valid=_noop,
    ),
    FounderStates.CONFIRM.state: StepDefinition(
        state=FounderStates.CONFIRM,
        expected_input_desc_fa="تأیید یا لغو تأسیس ملت با دکمه‌ها",
        validator=lambda _text, _ctx: False,
        on_valid=_noop,
    ),
    MarketStates.WAITING_BUY_AMOUNT.state: StepDefinition(
        state=MarketStates.WAITING_BUY_AMOUNT,
        expected_input_desc_fa="مقدار خرید به ΩXR",
        validator=_decimal_validator,
        on_valid=_noop,
    ),
    MarketStates.WAITING_SELL_AMOUNT.state: StepDefinition(
        state=MarketStates.WAITING_SELL_AMOUNT,
        expected_input_desc_fa="مقدار فروش ارز",
        validator=_decimal_validator,
        on_valid=_noop,
    ),
    GovernanceStates.WAITING_VALUE.state: StepDefinition(
        state=GovernanceStates.WAITING_VALUE,
        expected_input_desc_fa="مقدار پیشنهادی قانون",
        validator=_governance_validator,
        on_valid=_noop,
    ),
    GovernanceStates.CONFIRM_PROPOSAL.state: StepDefinition(
        state=GovernanceStates.CONFIRM_PROPOSAL,
        expected_input_desc_fa="تأیید یا لغو ثبت طرح با دکمه‌ها",
        validator=lambda _text, _ctx: False,
        on_valid=_noop,
    ),
}


SYSTEM_COMMANDS = {"start", "cancel", "help"}
NAVIGATION_TEXTS = {
    "💹 بازار",
    "🌍 ملت‌ها",
    "📊 پورتفولیو",
    "⚡ مأموریت",
    "🏆 رتبه‌بندی",
    "⚙️ تنظیمات",
}


async def classify_intent(message, current_state, registry=None) -> IntentType:
    text = (message.text or "").strip()
    if text.startswith("/"):
        command = text[1:].split("@", 1)[0].split(None, 1)[0].casefold()
        if command in SYSTEM_COMMANDS:
            return IntentType.SYSTEM_COMMAND

    if text in NAVIGATION_TEXTS:
        return IntentType.NAVIGATION_TEXT

    definition = (registry or STEP_DEFINITIONS).get(current_state)
    if definition is not None and definition.validator(text, None):
        return IntentType.STATE_INPUT

    if any(token in text for token in ("چطور", "چرا", "کمک", "؟")):
        return IntentType.OFF_TOPIC

    return IntentType.AMBIGUOUS


async def dispatch_navigation_text(message, state) -> None:
    from app.handlers.market import market_button
    from app.handlers.nation import open_nations
    from app.handlers.sections import missions, portfolio, ranking, settings

    actions = {
        "💹 بازار": market_button,
        "🌍 ملت‌ها": open_nations,
        "📊 پورتفولیو": portfolio,
        "⚡ مأموریت": missions,
        "🏆 رتبه‌بندی": ranking,
        "⚙️ تنظیمات": settings,
    }
    action = actions.get((message.text or "").strip())
    if action is not None:
        await action(message, state) if state is not None else await action(message)


def expected_input_text(current_state) -> str:
    definition = STEP_DEFINITIONS.get(current_state)
    return definition.expected_input_desc_fa if definition else "ادامه‌ی مرحله‌ی فعلی"


class IntentRouterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get("state")
        current_state = await state.get_state() if state is not None else None
        intent = await classify_intent(event, current_state)

        if intent == IntentType.OFF_TOPIC:
            definition = STEP_DEFINITIONS.get(current_state)
            desc = definition.expected_input_desc_fa if definition else "ادامه‌ی مرحله‌ی فعلی"
            await event.answer(
                f"ℹ️ هنوز در مرحله‌ی «{desc}» هستی.\n"
                "برای راهنمایی از /help استفاده کن یا /cancel بزن."
            )
            return

        if intent == IntentType.AMBIGUOUS and current_state is not None:
            desc = expected_input_text(current_state)
            await event.answer(
                f"⚠️ متوجه نشدم. الان منتظر «{desc}» هستم.\n"
                "/cancel"
            )
            return

        if intent == IntentType.NAVIGATION_TEXT:
            await dispatch_navigation_text(event, state)
            return

        data["intent_type"] = intent.value
        return await handler(event, data)


intent_router_middleware = IntentRouterMiddleware()
