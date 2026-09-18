from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from decimal import Decimal

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import delete, select

from app.database.models import (
    BotGroup,
    CurrencyHolding,
    KeyboardState,
    Nation,
    OnboardingDraft,
    TradePreview,
    Transaction,
    User,
    UserActivity,
)
from app.database.session import async_session
from app.handlers.founder import (
    _continue_group_onboarding,
    confirm_founder,
    receive_nation_name,
    start_founder,
)
from app.handlers.market import (
    confirm_sell,
    open_market,
    sell_amount_message,
    sell_currency,
    market_sell,
)
from app.handlers.onboarding_fix import accept_valid_name, reject_non_english_name
from app.handlers.start import join_nation, start, start_game_callback
from app.services.intent_router import handle_system_command, classify_intent, IntentType
from app.states.founder import FounderStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates


TEST_USER_ID = 982450101
SEED_USER_ID = 982450099
BOT_ID = 982450999
SEED_GROUP_ID = -100982450099
FOUNDER_GROUP_ID = -100982450101
SEED_CURRENCY = "SDN"


@dataclass
class FakeUser:
    id: int
    first_name: str = "Audit"
    username: str = "audit"


@dataclass
class FakeChat:
    id: int
    type: str = "private"
    title: str | None = None
    username: str | None = None


@dataclass
class FakeMessage:
    bot: object
    from_user: FakeUser
    chat: FakeChat
    text: str | None = None
    message_id: int = 0
    replies: list["FakeMessage"] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    deleted: bool = False

    async def answer(self, text, reply_markup=None, parse_mode=None):
        return await self.bot.send_message(
            self.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            from_user=self.from_user,
        )

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.text = text
        self.edits.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode})
        return self

    async def edit_caption(self, caption, reply_markup=None, parse_mode=None):
        self.text = caption
        self.edits.append({"caption": caption, "reply_markup": reply_markup, "parse_mode": parse_mode})
        return self

    async def delete(self):
        self.deleted = True
        return True


@dataclass
class FakeCallback:
    bot: object
    from_user: FakeUser
    message: FakeMessage
    data: str
    answers: list[dict] = field(default_factory=list)

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})
        return True


class FakeBot:
    def __init__(self):
        self.id = BOT_ID
        self.username = "opex_audit_bot"
        self.counter = 1000
        self.sent: list[FakeMessage] = []
        self.calls: list[tuple] = []

    async def get_me(self):
        return SimpleNamespace(id=self.id, username=self.username)

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append(("get_chat_member", chat_id, user_id))
        return SimpleNamespace(status="administrator")

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, from_user=None):
        self.counter += 1
        if from_user is None:
            from_user = FakeUser(self.id, "OPEX", "opex_audit_bot")
        msg = FakeMessage(
            bot=self,
            from_user=from_user,
            chat=FakeChat(chat_id, "private" if chat_id > 0 else "supergroup"),
            text=text,
            message_id=self.counter,
        )
        self.sent.append(msg)
        self.calls.append(("send_message", chat_id, text, type(reply_markup).__name__ if reply_markup else None))
        return msg

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.calls.append(("edit_message_reply_markup", chat_id, message_id, reply_markup))
        return True

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete_message", chat_id, message_id))
        return True


class FakeFSMDispatcher:
    def __init__(self, context: FSMContext):
        self.fsm = self
        self.context = context

    async def get_context(self, *, bot, chat_id, user_id):
        return self.context


def make_context(storage: MemoryStorage, user_id: int) -> FSMContext:
    key = StorageKey(bot_id=BOT_ID, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


async def cleanup():
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(TradePreview).where(TradePreview.user_id.in_([TEST_USER_ID, SEED_USER_ID])))
            await session.execute(delete(Transaction).where(Transaction.user_id.in_([TEST_USER_ID, SEED_USER_ID])))
            await session.execute(delete(UserActivity).where(UserActivity.user_id.in_([TEST_USER_ID, SEED_USER_ID])))
            await session.execute(delete(CurrencyHolding).where(CurrencyHolding.user_id.in_([TEST_USER_ID, SEED_USER_ID])))
            await session.execute(delete(OnboardingDraft).where(OnboardingDraft.player_id == TEST_USER_ID))
            await session.execute(delete(KeyboardState).where(KeyboardState.player_id == TEST_USER_ID))
            await session.execute(
                User.__table__.update().where(User.user_id.in_([TEST_USER_ID, SEED_USER_ID])).values(home_nation_id=None)
            )
            await session.execute(
                Nation.__table__.update().where(Nation.group_id.in_([SEED_GROUP_ID, FOUNDER_GROUP_ID])).values(founder_user_id=None)
            )
            await session.execute(delete(User).where(User.user_id.in_([TEST_USER_ID, SEED_USER_ID])))
            await session.execute(delete(Nation).where(Nation.group_id.in_([SEED_GROUP_ID, FOUNDER_GROUP_ID])))
            await session.execute(delete(BotGroup).where(BotGroup.group_id == FOUNDER_GROUP_ID))


@pytest.fixture
async def seeded_db():
    await cleanup()
    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                group_id=SEED_GROUP_ID,
                name="Seed Nation",
                currency_code=SEED_CURRENCY,
                founder_user_id=None,
                exchange_rate=Decimal("1.0000"),
                rate_prev=Decimal("1.0000"),
                rate_24h_open=Decimal("1.0000"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
            )
            user = User(
                user_id=SEED_USER_ID,
                username="SeedUser",
                balance=Decimal("500.00"),
                xr_balance=Decimal("0.00"),
                role="player",
            )
            session.add_all([nation, user])
            await session.flush()
            user.home_nation_id = nation.nation_id
            session.add(
                CurrencyHolding(
                    user_id=SEED_USER_ID,
                    nation_id=nation.nation_id,
                    amount=Decimal("500.0000"),
                )
            )
    yield
    await cleanup()


@pytest.mark.asyncio
async def test_full_user_journey_with_restart_and_keyboard_transitions(seeded_db, capsys):
    bot = FakeBot()
    user = FakeUser(TEST_USER_ID, "Audit", "audituser")
    chat = FakeChat(TEST_USER_ID)

    storage = MemoryStorage()
    state = make_context(storage, TEST_USER_ID)

    await start(FakeMessage(bot, user, chat, "/start"), state)
    welcome = bot.sent[-1]
    assert welcome.text and "OPEX MONEY" in welcome.text
    print("E2E|PASS|01|/start|welcome sent")

    await start_game_callback(
        FakeCallback(bot, user, welcome, "start_game"),
        state,
    )
    assert await state.get_state() == OnboardingStates.SET_USERNAME_PLAYER.state

    async with async_session() as session:
        keyboard = await session.get(KeyboardState, TEST_USER_ID)
        assert keyboard is not None
        assert keyboard.kind == "inline:onboarding"
    print("E2E|PASS|02|start_game|FSM=SET_USERNAME_PLAYER|keyboard=inline:onboarding")

    invalid = FakeMessage(bot, user, chat, "اسم فارسی!")
    await reject_non_english_name(invalid, state)
    assert await state.get_state() == OnboardingStates.SET_USERNAME_PLAYER.state
    print("E2E|PASS|03|invalid_username|state_preserved")

    await accept_valid_name(FakeMessage(bot, user, chat, "Audit123"), state)
    assert await state.get_state() == OnboardingStates.SELECT_NATION.state

    async with async_session() as session:
        db_user = await session.get(User, TEST_USER_ID)
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert db_user is not None and db_user.username == "Audit123"
        assert draft is not None and draft.step_key == "onboarding.select_nation"
    print("E2E|PASS|04|username|DB_saved|draft_saved")

    async with async_session() as session:
        nation = await session.scalar(select(Nation).where(Nation.group_id == SEED_GROUP_ID))

    nation_message = bot.sent[-1]
    await join_nation(
        FakeCallback(
            bot,
            user,
            nation_message,
            f"join_nation:{nation.nation_id}",
        ),
        state,
    )
    assert await state.get_state() is None

    async with async_session() as session:
        db_user = await session.get(User, TEST_USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == TEST_USER_ID,
                CurrencyHolding.nation_id == nation.nation_id,
            )
        )
        assert db_user is not None and db_user.home_nation_id == nation.nation_id
        assert holding is not None and holding.amount == Decimal("500.0000")
    print("E2E|PASS|05|join_nation|User+Holding persisted")

    dashboard = bot.sent[-1]
    await start_founder(
        FakeCallback(bot, user, dashboard, "found_nation"),
        state,
        bot,
    )
    assert await state.get_state() == FounderStates.WAITING_GROUP_ADMIN.state

    async with async_session() as session:
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert draft is not None and draft.step_key == "founder.waiting_group_admin"
    print("E2E|PASS|06|founder_start|draft=waiting_group_admin")

    dispatcher = FakeFSMDispatcher(state)
    await _continue_group_onboarding(
        bot=bot,
        dispatcher=dispatcher,
        founder_user_id=TEST_USER_ID,
        group_id=FOUNDER_GROUP_ID,
        group_title="Audit Capital",
        group_username="audit_capital",
        group_type="supergroup",
    )
    assert await state.get_state() == FounderStates.SET_NATION_NAME.state

    async with async_session() as session:
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert draft is not None
        assert draft.step_key == "founder.set_nation_name"
        assert draft.payload["group_id"] == FOUNDER_GROUP_ID
    print("E2E|PASS|07|group_connect|draft=persistent")

    bad_nation_name = FakeMessage(bot, user, FakeChat(TEST_USER_ID), "12345")
    await receive_nation_name(bad_nation_name, state)
    assert await state.get_state() == FounderStates.SET_NATION_NAME.state
    print("E2E|PASS|08|invalid_nation_name|state_preserved")

    await receive_nation_name(
        FakeMessage(bot, user, chat, "Nova Realm"),
        state,
    )
    assert await state.get_state() == FounderStates.CONFIRM.state

    async with async_session() as session:
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert draft is not None and draft.step_key == "founder.confirm"
        assert draft.payload["nation_name"] == "Nova Realm"
    print("E2E|PASS|09|nation_name|confirm_draft_saved")

    restart_storage = MemoryStorage()
    restarted_state = make_context(restart_storage, TEST_USER_ID)
    restart_message = FakeMessage(bot, user, chat, "/start")
    await start(restart_message, restarted_state)
    assert await restarted_state.get_state() is None
    assert restart_message is not None

    resume_message = bot.sent[-1]
    assert resume_message.text and "ادامه" in resume_message.text
    print("E2E|PASS|10|restart|FSM_reset_but_draft_detected")

    from app.services.intent_router import resume_draft
    await resume_draft(
        FakeCallback(bot, user, resume_message, "resume_draft"),
        restarted_state,
    )
    assert await restarted_state.get_state() == FounderStates.CONFIRM.state
    recovered = await restarted_state.get_data()
    assert recovered["group_id"] == FOUNDER_GROUP_ID
    assert recovered["nation_name"] == "Nova Realm"
    print("E2E|PASS|11|resume_draft|state+payload_recovered")

    confirm_message = bot.sent[-1]
    await confirm_founder(
        FakeCallback(bot, user, confirm_message, "confirm_found"),
        restarted_state,
        bot,
    )
    assert await restarted_state.get_state() is None

    async with async_session() as session:
        db_user = await session.get(User, TEST_USER_ID)
        new_nation = await session.scalar(select(Nation).where(Nation.group_id == FOUNDER_GROUP_ID))
        assert db_user is not None and db_user.role == "founder"
        assert db_user.home_nation_id == new_nation.nation_id
        assert db_user.balance == Decimal("1000.00")
        assert db_user.xr_balance == Decimal("1000.00")
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == TEST_USER_ID,
                CurrencyHolding.nation_id == new_nation.nation_id,
            )
        )
        assert holding.amount == Decimal("1000.0000")
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert draft is None
    print("E2E|PASS|12|confirm_founder|Nation+User+Holding persisted|draft_cleared")

    await open_market(FakeMessage(bot, user, chat, "💹 بازار"))
    async with async_session() as session:
        keyboard = await session.get(KeyboardState, TEST_USER_ID)
        assert keyboard.kind == "inline:market"
    print("E2E|PASS|13|market_open|reply_to_inline_transition")

    market_message = bot.sent[-1]
    await market_sell(FakeCallback(bot, user, market_message, "market_sell"), restarted_state)
    sell_list_message = bot.sent[-1]
    await sell_currency(
        FakeCallback(
            bot,
            user,
            sell_list_message,
            f"sell_{new_nation.currency_code}",
        ),
        restarted_state,
    )
    assert await restarted_state.get_state() == MarketStates.WAITING_SELL_AMOUNT.state
    print("E2E|PASS|14|sell_wizard|state=WAITING_SELL_AMOUNT")

    invalid_amount = FakeMessage(bot, user, chat, "نه عدد است")
    await sell_amount_message(invalid_amount, restarted_state)
    assert await restarted_state.get_state() == MarketStates.WAITING_SELL_AMOUNT.state
    print("E2E|PASS|15|invalid_amount|state_preserved")

    await handle_system_command(
        FakeMessage(bot, user, chat, "/cancel"),
        restarted_state,
    )
    assert await restarted_state.get_state() is None

    async with async_session() as session:
        keyboard = await session.get(KeyboardState, TEST_USER_ID)
        draft = await session.get(OnboardingDraft, TEST_USER_ID)
        assert keyboard.kind == "reply:main"
        assert draft is None
    print("E2E|PASS|16|cancel|inline_to_reply_restored|draft_cleared")

    await market_sell(FakeCallback(bot, user, bot.sent[-1], "market_sell"), restarted_state)
    await sell_currency(
        FakeCallback(
            bot,
            user,
            bot.sent[-1],
            f"sell_{new_nation.currency_code}",
        ),
        restarted_state,
    )
    await sell_amount_message(FakeMessage(bot, user, chat, "100"), restarted_state)
    async with async_session() as session:
        preview = await session.scalar(
            select(TradePreview)
            .where(
                TradePreview.user_id == TEST_USER_ID,
                TradePreview.nation_id == new_nation.nation_id,
                TradePreview.side == "sell",
            )
            .order_by(TradePreview.id.desc())
        )
    assert preview is not None
    await confirm_sell(
        FakeCallback(
            bot,
            user,
            bot.sent[-1],
            f"csell_{new_nation.nation_id}_{preview.spend}",
        ),
        restarted_state,
    )

    async with async_session() as session:
        tx = await session.scalar(
            select(Transaction)
            .where(Transaction.user_id == TEST_USER_ID)
            .order_by(Transaction.id.desc())
        )
        assert tx is not None and tx.transaction_type == "sell"
        assert tx.amount == Decimal("100")
        db_user = await session.get(User, TEST_USER_ID)
        holding = await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == TEST_USER_ID,
                CurrencyHolding.nation_id == new_nation.nation_id,
            )
        )
        assert db_user.xr_balance > Decimal("1000.00")
        assert holding.amount == Decimal("900.0000")
    print("E2E|PASS|17|trade|Transaction+balances persisted")

    captured = capsys.readouterr().out
    assert "E2E|PASS|17|trade" in captured
