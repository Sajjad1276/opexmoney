from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select

from app.database.models import (
    BotGroup,
    CurrencyHolding,
    KeyboardState,
    Nation,
    OnboardingDraft,
    Transaction,
    User,
    UserActivity,
)
from app.database.session import async_session
from app.handlers import founder as founder_module
from app.handlers import market as market_module
from app.handlers import onboarding_fix
from app.handlers import start as start_module
from app.handlers.interaction import system_cancel
from app.services.keyboard_state import KeyboardKind, keyboard_manager
from app.services.onboarding_draft import get_draft
from app.states.founder import FounderStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates


USER_ID = 920001
FOUNDER_ID = 920002
GROUP_ID = -100920002
NATION_GROUP_ID = -100920001


class FakeBot:
    def __init__(self) -> None:
        self.id = 990001
        self.username = "opex_phase4_test_bot"
        self.next_message_id = 100
        self.sent: list[tuple[int, str, object]] = []
        self.edits: list[tuple[str, int, int]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None, **kwargs):
        self.sent.append((chat_id, text, reply_markup))
        self.next_message_id += 1
        return FakeMessage(
            bot=self,
            chat_id=chat_id,
            message_id=self.next_message_id,
            text=text,
        )

    async def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup=None):
        self.edits.append(("markup", chat_id, message_id))
        return None

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup=None, **kwargs):
        self.edits.append(("text", chat_id, message_id))
        return FakeMessage(
            bot=self,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )

    async def edit_message_caption(self, chat_id: int, message_id: int, caption: str, reply_markup=None, **kwargs):
        self.edits.append(("caption", chat_id, message_id))
        return FakeMessage(
            bot=self,
            chat_id=chat_id,
            message_id=message_id,
            text=caption,
        )

    async def get_me(self):
        return SimpleNamespace(id=self.id, username=self.username)

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status="administrator")


class FakeMessage:
    def __init__(self, *, bot: FakeBot, chat_id: int, message_id: int = 1, text: str = ""):
        self.bot = bot
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id, type="private", title=None, username=None)
        self.from_user = SimpleNamespace(id=chat_id, first_name="Test", username="test")
        self.text = text
        self.photo = None
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return await self.bot.send_message(self.chat.id, text, reply_markup=reply_markup, **kwargs)

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        return await self.bot.edit_message_text(
            self.chat.id,
            self.message_id,
            text,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def edit_caption(self, caption: str, reply_markup=None, **kwargs):
        return await self.bot.edit_message_caption(
            self.chat.id,
            self.message_id,
            caption,
            reply_markup=reply_markup,
            **kwargs,
        )


class FakeCallback:
    def __init__(self, *, bot: FakeBot, user_id: int, message: FakeMessage, data: str):
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id, first_name="Test", username="test")
        self.message = message
        self.data = data
        self.answers: list[tuple] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


@pytest.fixture
def bot():
    return FakeBot()


async def new_state(storage, bot: FakeBot, user_id: int):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(Transaction).where(Transaction.user_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(UserActivity).where(UserActivity.user_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(CurrencyHolding).where(CurrencyHolding.user_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(OnboardingDraft).where(OnboardingDraft.player_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(KeyboardState).where(KeyboardState.chat_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(BotGroup).where(BotGroup.group_id == GROUP_ID)
            )
            await session.execute(
                delete(User).where(User.user_id.in_([USER_ID, FOUNDER_ID]))
            )
            await session.execute(
                delete(Nation).where(Nation.group_id.in_([NATION_GROUP_ID, GROUP_ID]))
            )


@pytest.mark.asyncio
async def test_full_user_journey_and_restart_recovery(bot):
    from aiogram.fsm.storage.memory import MemoryStorage

    storage = MemoryStorage()
    state = await new_state(storage, bot, USER_ID)

    async with async_session() as session:
        async with session.begin():
            session.add(
                Nation(
                    group_id=NATION_GROUP_ID,
                    name="Origin",
                    currency_code="ORG",
                    exchange_rate=Decimal("1.0000"),
                    rate_prev=Decimal("1.0000"),
                    rate_24h_open=Decimal("1.0000"),
                )
            )

    start_message = FakeMessage(bot=bot, chat_id=USER_ID, text="/start")
    await start_module.start(start_message, state)
    assert bot.sent[-1][2] is not None
    assert "OPEX MONEY" in bot.sent[-1][1]

    callback_message = FakeMessage(bot=bot, chat_id=USER_ID)
    await start_module.start_game_callback(
        FakeCallback(bot=bot, user_id=USER_ID, message=callback_message, data="start_game"),
        state,
    )
    assert await state.get_state() == OnboardingStates.SET_USERNAME_PLAYER.state

    name_message = FakeMessage(bot=bot, chat_id=USER_ID, text="Trader920001")
    await onboarding_fix.accept_valid_name(name_message, state)
    assert await state.get_state() == OnboardingStates.SELECT_NATION.state

    async with async_session() as session:
        nation_id = await session.scalar(
            select(Nation.nation_id).where(Nation.group_id == NATION_GROUP_ID)
        )

    join_message = FakeMessage(bot=bot, chat_id=USER_ID)
    join_call = FakeCallback(
        bot=bot,
        user_id=USER_ID,
        message=join_message,
        data=f"join_nation:{nation_id}",
    )
    await start_module.join_nation(join_call, state)
    assert await state.get_state() is None

    tutorial_state = await new_state(storage, bot, USER_ID)
    await tutorial_state.update_data(first_trade_available=True)
    await start_module.first_trade_tutorial(
        FakeCallback(bot=bot, user_id=USER_ID, message=join_message, data="first_trade_tutorial"),
        tutorial_state,
    )
    await start_module.confirm_first_trade(
        FakeCallback(bot=bot, user_id=USER_ID, message=join_message, data="confirm_first_trade"),
        tutorial_state,
    )

    market_state = await new_state(storage, bot, USER_ID)
    market_message = FakeMessage(bot=bot, chat_id=USER_ID)
    await market_module.market_buy(
        FakeCallback(bot=bot, user_id=USER_ID, message=market_message, data="market_buy"),
        market_state,
    )
    async with async_session() as session:
        nation = await session.get(Nation, nation_id)
    await market_module.buy_currency(
        FakeCallback(
            bot=bot,
            user_id=USER_ID,
            message=market_message,
            data=f"buy_{nation_id}",
        ),
        market_state,
    )
    await market_module.buy_amount_message(
        FakeMessage(bot=bot, chat_id=USER_ID, text="40"),
        market_state,
    )
    preview_callback = FakeCallback(
        bot=bot,
        user_id=USER_ID,
        message=market_message,
        data=f"cbuy_{nation_id}_40",
    )
    await market_module.confirm_buy(preview_callback, market_state)

    async with async_session() as session:
        user = await session.get(User, USER_ID)
        tx_count = await session.scalar(
            select(func.count(Transaction.id)).where(Transaction.user_id == USER_ID)
        )
        assert user is not None
        assert user.home_nation_id == nation_id
        assert user.xr_balance >= Decimal("400.00")
        assert tx_count >= 2
        print(
            f"E2E|PASS|player={user.user_id}|nation={nation_id}|transactions={tx_count}|xr={user.xr_balance}"
        )

    wizard_state = await new_state(storage, bot, USER_ID)
    await market_module.buy_currency(
        FakeCallback(
            bot=bot,
            user_id=USER_ID,
            message=market_message,
            data=f"buy_{nation_id}",
        ),
        wizard_state,
    )
    assert await wizard_state.get_state() == MarketStates.WAITING_BUY_AMOUNT.state

    draft_before_cancel = await get_draft_record(USER_ID)
    assert draft_before_cancel is not None

    await system_cancel(market_message, wizard_state)
    assert await wizard_state.get_state() is None
    assert await get_draft_record(USER_ID) is None
    print("E2E|PASS|cancel|state=none|draft=cleared|keyboard=main-menu")

    founder_state = await new_state(storage, bot, FOUNDER_ID)
    async with async_session() as session:
        async with session.begin():
            session.add(User(user_id=FOUNDER_ID, username="Founder920002", role="player"))

    founder_message = FakeMessage(bot=bot, chat_id=FOUNDER_ID)
    await founder_module.start_founder(
        FakeCallback(
            bot=bot,
            user_id=FOUNDER_ID,
            message=founder_message,
            data="found_nation",
        ),
        founder_state,
        bot,
    )
    assert await founder_state.get_state() == FounderStates.WAITING_GROUP_ADMIN.state

    async def get_context(**kwargs):
        return founder_state

    dispatcher = SimpleNamespace(fsm=SimpleNamespace(get_context=get_context))
    group_message = FakeMessage(bot=bot, chat_id=GROUP_ID, text=f"/start founder_{FOUNDER_ID}")
    group_message.chat = SimpleNamespace(
        id=GROUP_ID,
        type="group",
        title="Phase 4 Capital",
        username="phase4capital",
    )
    group_message.from_user = SimpleNamespace(id=FOUNDER_ID, first_name="Founder")

    await founder_module.group_founder_start(group_message, bot, dispatcher)
    assert await founder_state.get_state() == FounderStates.SET_NATION_NAME.state

    nation_name_message = FakeMessage(bot=bot, chat_id=FOUNDER_ID, text="Restartia Kingdom")
    await founder_module.receive_nation_name(nation_name_message, founder_state)
    assert await founder_state.get_state() == FounderStates.CONFIRM.state
    async with async_session() as session:
        draft = await get_draft(session, FOUNDER_ID)
        assert draft is not None
        assert draft.step_key == FounderStates.CONFIRM.state
        print(f"E2E|PASS|founder-draft|step={draft.step_key}|payload={draft.payload}")

    from aiogram.fsm.storage.memory import MemoryStorage
    restarted_storage = MemoryStorage()
    restarted_state = await new_state(restarted_storage, bot, FOUNDER_ID)
    resumed = await start_module.start(
        FakeMessage(bot=bot, chat_id=FOUNDER_ID, text="/start"),
        restarted_state,
    )
    assert resumed is None
    assert await restarted_state.get_state() == FounderStates.CONFIRM.state
    print("E2E|PASS|restart|fsm=cleared|draft=recovered|state=founder.confirm")

    founder_confirm_message = FakeMessage(bot=bot, chat_id=FOUNDER_ID)
    founder_confirm = FakeCallback(
        bot=bot,
        user_id=FOUNDER_ID,
        message=founder_confirm_message,
        data="confirm_found",
    )
    await founder_module.confirm_founder(founder_confirm, restarted_state, bot)

    async with async_session() as session:
        founder = await session.get(User, FOUNDER_ID)
        created_nation = await session.scalar(
            select(Nation).where(Nation.group_id == GROUP_ID)
        )
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == FOUNDER_ID,
                CurrencyHolding.nation_id == created_nation.nation_id,
            )
        )
        assert founder is not None
        assert founder.role == "founder"
        assert created_nation is not None
        assert holding is not None
        assert holding.amount == Decimal("1000.0000")
        assert await get_draft(session, FOUNDER_ID) is None
        print(
            f"E2E|PASS|founder-created|nation={created_nation.nation_id}|currency={created_nation.currency_code}|holding={holding.amount}"
        )


async def get_draft_record(user_id: int):
    async with async_session() as session:
        return await get_draft(session, user_id)


@pytest.mark.asyncio
async def test_invalid_input_is_observed_before_handler():
    from app.services.intent_router import IntentType, classify_intent

    message = SimpleNamespace(text="چطور ادامه بدم؟")
    intent = await classify_intent(message, MarketStates.WAITING_BUY_AMOUNT.state)
    assert intent == IntentType.OFF_TOPIC
    print(f"E2E|PASS|invalid-off-topic|intent={intent.value}")
