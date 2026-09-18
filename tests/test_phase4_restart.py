from __future__ import annotations

from decimal import Decimal

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import delete, select

from app.database.models import BotGroup, OnboardingDraft, User
from app.database.session import async_session
from app.services.draft_service import save_draft, get_draft
from app.states.founder import FounderStates


USER_ID = 982450102


def ctx(storage):
    return FSMContext(storage=storage, key=StorageKey(bot_id=982450999, chat_id=USER_ID, user_id=USER_ID))


@pytest.mark.asyncio
async def test_restart_recovery_from_onboarding_draft():
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(OnboardingDraft).where(OnboardingDraft.player_id == USER_ID))
            await session.execute(delete(BotGroup).where(BotGroup.group_id == -100982450102))
            await session.execute(delete(User).where(User.user_id == USER_ID))
            session.add(User(user_id=USER_ID, username="RestartUser", balance=Decimal("500"), xr_balance=Decimal("0")))

    first_storage = MemoryStorage()
    first = ctx(first_storage)
    await first.set_state(FounderStates.CONFIRM)
    await first.update_data(
        group_id=-100982450102,
        group_title="Restart Capital",
        nation_name="Restart Realm",
        currency_code="RLM",
    )
    await save_draft(
        USER_ID,
        "founder.confirm",
        {
            "group_id": -100982450102,
            "group_title": "Restart Capital",
            "nation_name": "Restart Realm",
            "currency_code": "RLM",
        },
    )

    second_storage = MemoryStorage()
    second = ctx(second_storage)
    assert await second.get_state() is None
    draft = await get_draft(USER_ID)
    assert draft is not None
    print("RESTART|PASS|fsm_lost|draft_present")
    await second.update_data(**dict(draft.payload))
    await second.set_state(FounderStates.CONFIRM)
    data = await second.get_data()
    assert data["nation_name"] == "Restart Realm"
    assert data["currency_code"] == "RLM"
    print("RESTART|PASS|state_recovered_from_db")

    await cleanup_restart()


async def cleanup_restart():
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(OnboardingDraft).where(OnboardingDraft.player_id == USER_ID))
            await session.execute(delete(User).where(User.user_id == USER_ID))
            await session.execute(delete(BotGroup).where(BotGroup.group_id == -100982450102))
