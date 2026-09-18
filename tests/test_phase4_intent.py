from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.intent_router import IntentType, classify_intent
from app.states.founder import FounderStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates


@pytest.mark.asyncio
async def test_all_five_intent_types():
    def msg(text: str):
        return SimpleNamespace(text=text)

    cases = [
        (
            "system_command",
            msg("/cancel"),
            FounderStates.SET_NATION_NAME.state,
            IntentType.SYSTEM_COMMAND,
        ),
        (
            "state_input",
            msg("100"),
            MarketStates.WAITING_SELL_AMOUNT.state,
            IntentType.STATE_INPUT,
        ),
        (
            "navigation_text",
            msg("💹 بازار"),
            None,
            IntentType.NAVIGATION_TEXT,
        ),
        (
            "off_topic",
            msg("چطور ادامه بدم؟"),
            OnboardingStates.SET_USERNAME_PLAYER.state,
            IntentType.OFF_TOPIC,
        ),
        (
            "ambiguous",
            msg("maybe"),
            FounderStates.CONFIRM.state,
            IntentType.AMBIGUOUS,
        ),
    ]

    for label, message, state, expected in cases:
        actual = await classify_intent(message, state)
        print(f"INTENT|{label}|expected={expected.value}|actual={actual.value}")
        assert actual is expected
