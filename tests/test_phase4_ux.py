from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from app.services.intent_router import (
    IntentType,
    STEP_DEFINITIONS,
    classify_intent,
)
from app.services.keyboard_state import KeyboardKind, KeyboardStateManager
from app.services.onboarding_draft import get_draft, save_draft
from config import settings


@pytest.mark.asyncio
async def test_intent_router_five_intents():
    assert await classify_intent(SimpleNamespace(text="/cancel"), None) == IntentType.SYSTEM_COMMAND
    assert await classify_intent(SimpleNamespace(text="💹 بازار"), None) == IntentType.NAVIGATION_TEXT
    assert (
        await classify_intent(
            SimpleNamespace(text="Trader7"),
            "OnboardingStates:SET_USERNAME_PLAYER",
        )
        == IntentType.STATE_INPUT
    )
    assert (
        await classify_intent(
            SimpleNamespace(text="چطور ادامه بدم؟"),
            "MarketStates:WAITING_BUY_AMOUNT",
        )
        == IntentType.OFF_TOPIC
    )
    assert (
        await classify_intent(
            SimpleNamespace(text="hello"),
            "MarketStates:WAITING_BUY_AMOUNT",
        )
        == IntentType.AMBIGUOUS
    )
    print("INTENT|PASS|SYSTEM_COMMAND|NAVIGATION_TEXT|STATE_INPUT|OFF_TOPIC|AMBIGUOUS")


def test_all_fsm_states_have_step_definitions():
    expected = {
        "OnboardingStates:SET_USERNAME_PLAYER",
        "OnboardingStates:SELECT_NATION",
        "FounderStates:WAITING_GROUP_ADMIN",
        "FounderStates:SET_NATION_NAME",
        "FounderStates:CONFIRM",
        "MarketStates:WAITING_BUY_AMOUNT",
        "MarketStates:WAITING_SELL_AMOUNT",
        "GovernanceStates:WAITING_VALUE",
        "GovernanceStates:CONFIRM_PROPOSAL",
    }
    assert expected.issubset(STEP_DEFINITIONS.keys())
    print(f"STEP-DEFINITIONS|PASS|count={len(STEP_DEFINITIONS)}")


@pytest.mark.asyncio
async def test_draft_survives_new_memory_storage():
    user_id = 930001
    from app.database.models import User

    async with __import__("app.database.session", fromlist=["async_session"]).async_session() as session:
        async with session.begin():
            session.add(User(
                user_id=user_id,
                username="PersistentiaPlayer",
                role="player",
                balance=0,
                xr_balance=0,
            ))
            await save_draft(
                session,
                player_id=user_id,
                step_key="FounderStates:SET_NATION_NAME",
                payload={"nation_name": "Persistentia", "group_id": -100930001},
            )

    first = MemoryStorage()
    second = MemoryStorage()
    assert first is not second

    async with __import__("app.database.session", fromlist=["async_session"]).async_session() as session:
        draft = await get_draft(session, user_id)
        assert draft is not None
        assert draft.payload["nation_name"] == "Persistentia"
        assert draft.step_key == "FounderStates:SET_NATION_NAME"
        print("RESTART|PASS|MemoryStorage replaced|draft remains in PostgreSQL")

    async with __import__("app.database.session", fromlist=["async_session"]).async_session() as session:
        async with session.begin():
            await session.delete(draft)


def test_production_storage_requires_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(settings, "allow_memory_fsm_dev", False)

    from main import build_storage

    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        build_storage()

    monkeypatch.setattr(settings, "allow_memory_fsm_dev", True)
    storage = build_storage()
    assert isinstance(storage, MemoryStorage)
    print("STORAGE|PASS|production=REDIS_REQUIRED|dev_memory=explicit")


@pytest.mark.asyncio
async def test_keyboard_manager_transition_contract():
    class Bot:
        def __init__(self):
            self.sent = []
            self.removed = 0

        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            self.sent.append((chat_id, text, reply_markup))
            self.message_id = len(self.sent)
            return SimpleNamespace(message_id=self.message_id)

        async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
            self.removed += 1

    manager = KeyboardStateManager()
    bot = Bot()

    await manager.send_message(
        bot,
        chat_id=930002,
        text="main",
        kind=KeyboardKind.REPLY,
        name="main_menu",
        markup=object(),
    )
    await manager.send_message(
        bot,
        chat_id=930002,
        text="wizard",
        kind=KeyboardKind.INLINE,
        name="wizard",
        markup=object(),
    )
    assert bot.removed == 1
    assert len(bot.sent) == 2

    await manager.send_message(
        bot,
        chat_id=930002,
        text="main again",
        kind=KeyboardKind.REPLY,
        name="main_menu",
        markup=object(),
    )
    assert len(bot.sent) == 3
    assert bot.removed == 2

    async with __import__("app.database.session", fromlist=["async_session"]).async_session() as session:
        row = await session.get(__import__("app.database.models", fromlist=["KeyboardState"]).KeyboardState, 930002)
        assert row.kind == KeyboardKind.REPLY.value
        assert row.name == "main_menu"
        await session.delete(row)
        await session.commit()
    print("KEYBOARD|PASS|reply->inline->reply")
