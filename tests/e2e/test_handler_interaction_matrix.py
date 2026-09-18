from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import delete

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
from app.states.founder import FounderStates
from app.states.market import MarketStates
from app.states.onboarding import OnboardingStates


USER_ID = 940001
NATION_GROUP = -100940001
MATRIX_GROUP_A = -100940002
MATRIX_GROUP_B = -100940003


class Bot:
    def __init__(self) -> None:
        self.id = 949999
        self.username = "matrix_test_bot"
        self.sent = []
        self.edits = []
        self.next_message_id = 1000

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.next_message_id += 1
        self.sent.append((chat_id, text, reply_markup))
        return Message(self, chat_id, self.next_message_id, text)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edits.append(("markup", chat_id, message_id))
        return None

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, **kwargs):
        self.edits.append(("text", chat_id, message_id))
        return Message(self, chat_id, message_id, text)

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None, **kwargs):
        self.edits.append(("caption", chat_id, message_id))
        return Message(self, chat_id, message_id, caption)

    async def get_me(self):
        return SimpleNamespace(id=self.id, username=self.username)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")


class Message:
    def __init__(self, bot, chat_id, message_id=1, text=""):
        self.bot = bot
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id, type="private", title=None, username=None)
        self.from_user = SimpleNamespace(id=USER_ID, first_name="Matrix", username="matrix")
        self.text = text
        self.photo = None
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return await self.bot.send_message(self.chat.id, text, reply_markup=reply_markup, **kwargs)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        return await self.bot.edit_message_text(self.chat.id, self.message_id, text, reply_markup, **kwargs)

    async def edit_caption(self, caption, reply_markup=None, **kwargs):
        return await self.bot.edit_message_caption(self.chat.id, self.message_id, caption, reply_markup, **kwargs)


class Callback:
    def __init__(self, bot, data, user_id=USER_ID, message=None):
        self.bot = bot
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, first_name="Matrix", username="matrix")
        self.message = message or Message(bot, user_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


async def context(storage, bot, user_id=USER_ID):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


@pytest.mark.asyncio
async def test_every_registered_handler_executes_at_least_once():
    from app.handlers import founder, governance, market, nation, onboarding_fix, sections, start

    bot = Bot()
    storage = MemoryStorage()
    state = await context(storage, bot)

    async with async_session() as session:
        async with session.begin():
            nation_row = Nation(
                group_id=NATION_GROUP,
                name="Matrixland",
                currency_code="MTR",
                exchange_rate=Decimal("1.0000"),
                rate_prev=Decimal("1.0000"),
                rate_24h_open=Decimal("1.0000"),
                is_active=True,
                member_count=1,
            )
            session.add(nation_row)
            await session.flush()
            session.add(User(
                user_id=USER_ID,
                username="MatrixUser",
                home_nation_id=nation_row.nation_id,
                balance=Decimal("500.00"),
                xr_balance=Decimal("500.00"),
                role="founder",
            ))
            session.add(CurrencyHolding(
                user_id=USER_ID,
                nation_id=nation_row.nation_id,
                amount=Decimal("100.0000"),
            ))

    failures = []

    async def run(name, callback):
        try:
            await callback()
            print(f"HANDLER|PASS|{name}")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"HANDLER|FAIL|{name}|{exc!r}")

    def start_message(text="/start"):
        return Message(bot, USER_ID, text=text)

    await run("start.start", lambda: start.start(start_message(), state))
    await run("start.start_game_button", lambda: start.start_game_button(await start_message("🎮 شروع بازی"), state))
    await run("start.start_game_callback", lambda: start.start_game_callback(Callback(bot, "start_game"), state))
    await run("start.start_help", lambda: start.start_help(await start_message("❓ راهنما")))
    await run("start.start_help_callback", lambda: start.start_help_callback(Callback(bot, "show_help")))
    await state.set_state(OnboardingStates.SELECT_NATION)
    await run("start.join_nation", lambda: start.join_nation(Callback(bot, "join_nation:999999"), state))
    await run("start.first_trade_tutorial", lambda: start.first_trade_tutorial(Callback(bot, "first_trade_tutorial"), state))
    await run("start.confirm_first_trade", lambda: start.confirm_first_trade(Callback(bot, "confirm_first_trade"), state))
    await run("start.skip_first_trade", lambda: start.skip_first_trade(Callback(bot, "skip_first_trade"), state))
    await run("start.cancel_start", lambda: start.cancel_start(Callback(bot, "cancel_start"), state))

    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await run(
        "onboarding.restart_onboarding_with_command",
        lambda: onboarding_fix.restart_onboarding_with_command(await start_message(), state),
    )
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await run(
        "onboarding.reject_blocked_name",
        lambda: onboarding_fix.reject_blocked_name(await start_message("admin"), state),
    )
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await run(
        "onboarding.reject_non_english_name",
        lambda: onboarding_fix.reject_non_english_name(await start_message("سلام دنیا"), state),
    )
    await state.set_state(OnboardingStates.SET_USERNAME_PLAYER)
    await run(
        "onboarding.accept_valid_name",
        lambda: onboarding_fix.accept_valid_name(await start_message("MatrixValid1"), state),
    )
    await run("onboarding.cancel_start_fix", lambda: onboarding_fix.cancel_start_fix(Callback(bot, "cancel_start"), state))

    await state.clear()
    await run("founder.start_founder", lambda: founder.start_founder(Callback(bot, "found_nation"), state, bot))
    await state.clear()
    await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
    await state.update_data(founder_user_id=USER_ID)
    group_event = SimpleNamespace(
        chat=SimpleNamespace(type="group", id=MATRIX_GROUP_A, title="Matrix Capital", username="matrix_capital"),
        new_chat_member=SimpleNamespace(status="administrator"),
        from_user=SimpleNamespace(id=USER_ID, first_name="Matrix"),
    )
    async def get_context(**kwargs):
        return state
    dispatcher = SimpleNamespace(fsm=SimpleNamespace(get_context=get_context))
    await run("founder.bot_group_status_changed", lambda: founder.bot_group_status_changed(group_event, bot, dispatcher))
    await state.set_state(FounderStates.WAITING_GROUP_ADMIN)
    await state.update_data(founder_user_id=USER_ID)
    group_message = Message(bot, MATRIX_GROUP_B, text=f"/start founder_{USER_ID}")
    group_message.chat = SimpleNamespace(id=MATRIX_GROUP_B, type="group", title="Matrix Capital B", username="matrix_capital_b")
    await run("founder.group_founder_start", lambda: founder.group_founder_start(group_message, bot, dispatcher))
    await state.set_state(FounderStates.SET_NATION_NAME)
    await state.update_data(group_id=MATRIX_GROUP_B, group_title="Matrix Capital B", group_username="matrix_capital_b", group_type="group")
    await run("founder.receive_nation_name", lambda: founder.receive_nation_name(await start_message("Matrix Republic"), state))
    await state.clear()
    await run("founder.confirm_founder", lambda: founder.confirm_founder(Callback(bot, "confirm_found"), state, bot))
    await run("founder.cancel_founder", lambda: founder.cancel_founder(Callback(bot, "cancel_founder"), state))

    await run("nation.open_nations", lambda: nation.open_nations(await start_message("🌍 ملت‌ها")))
    await run("nation.back_to_dashboard", lambda: nation.back_to_dashboard(Callback(bot, "back_to_dashboard")))
    await run("nation.my_nations", lambda: nation.my_nations(Callback(bot, "my_nations")))
    await run("nation.explore_nations", lambda: nation.explore_nations(Callback(bot, "explore_nations")))
    await run("nation.unavailable_nation_panel", lambda: nation.unavailable_nation_panel(Callback(bot, "founder_panel")))
    await run("nation.unavailable_trade_confirmation", lambda: nation.unavailable_trade_confirmation(Callback(bot, "confirm_trade")))

    await run("sections.portfolio", lambda: sections.portfolio(await start_message("📊 پورتفولیو")))
    await run("sections.missions", lambda: sections.missions(await start_message("⚡ مأموریت")))
    await run("sections.ranking", lambda: sections.ranking(await start_message("🏆 رتبه‌بندی")))
    await run("sections.settings", lambda: sections.settings(await start_message("⚙️ تنظیمات")))

    await run("market.market_button", lambda: market.market_button(await start_message("💹 بازار")))
    await run("market.market_main", lambda: market.market_main(Callback(bot, "market_main")))
    await run("market.market_refresh", lambda: market.market_refresh(Callback(bot, "market_refresh")))
    await run("market.market_buy", lambda: market.market_buy(Callback(bot, "market_buy"), state))

    await state.clear()
    await run("market.buy_currency", lambda: market.buy_currency(Callback(bot, "buy_999999"), state))
    await state.set_state(MarketStates.WAITING_BUY_AMOUNT)
    await state.update_data(nation_id=999999)
    await run("market.buy_amount_message", lambda: market.buy_amount_message(await start_message("9"), state))
    await run("market.confirm_buy", lambda: market.confirm_buy(Callback(bot, "cbuy_999999_10"), state))
    await run("market.buy_quick", lambda: market.buy_quick(Callback(bot, "buyq_10_999999"), state))

    await state.clear()
    await run("market.market_sell", lambda: market.market_sell(Callback(bot, "market_sell"), state))
    await state.clear()
    await run("market.sell_currency", lambda: market.sell_currency(Callback(bot, "sell_NOPE"), state))
    await state.set_state(MarketStates.WAITING_SELL_AMOUNT)
    await state.update_data(nation_id=999999)
    await run("market.sell_amount_message", lambda: market.sell_amount_message(await start_message("0"), state))
    await run("market.confirm_sell", lambda: market.confirm_sell(Callback(bot, "csell_999999_10"), state))
    await run("market.sell_quick", lambda: market.sell_quick(Callback(bot, "sellq_10_NOPE"), state))
    await run("market.market_chart", lambda: market.market_chart(Callback(bot, "market_chart")))
    await run("market.market_history", lambda: market.market_history(Callback(bot, "market_history")))

    await run("governance.governance_main", lambda: governance.governance_main(Callback(bot, "governance_main")))
    await run("governance.governance_active", lambda: governance.governance_active(Callback(bot, "gov_active")))
    await run("governance.governance_new", lambda: governance.governance_new(Callback(bot, "gov_new"), state))
    await run("governance.governance_select_rule", lambda: governance.governance_select_rule(Callback(bot, "gov_rule:not_a_rule"), state))
    await state.clear()
    await run("governance.governance_receive_value", lambda: governance.governance_receive_value(await start_message("5"), state))
    await run("governance.governance_confirm", lambda: governance.governance_confirm(Callback(bot, "gov_confirm"), state))
    await run("governance.governance_cancel", lambda: governance.governance_cancel(Callback(bot, "gov_cancel"), state))
    await run("governance.governance_voting", lambda: governance.governance_voting(Callback(bot, "gov_voting")))
    await run("governance.governance_proposal", lambda: governance.governance_proposal(Callback(bot, "gov_proposal:999999")))
    await run("governance.governance_vote", lambda: governance.governance_vote(Callback(bot, "gov_vote:999999:for")))
    await run("governance.governance_history", lambda: governance.governance_history(Callback(bot, "gov_history:0")))
    await run("governance.governance_revoke_list", lambda: governance.governance_revoke_list(Callback(bot, "gov_revoke_list")))
    await run("governance.governance_revoke", lambda: governance.governance_revoke(Callback(bot, "gov_revoke:999999")))

    assert not failures, "\n".join(f"{name}: {error}" for name, error in failures)

    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(TradePreview).where(TradePreview.user_id == USER_ID))
            await session.execute(delete(Transaction).where(Transaction.user_id == USER_ID))
            await session.execute(delete(UserActivity).where(UserActivity.user_id == USER_ID))
            await session.execute(delete(CurrencyHolding).where(CurrencyHolding.user_id == USER_ID))
            await session.execute(delete(OnboardingDraft).where(OnboardingDraft.player_id == USER_ID))
            await session.execute(delete(KeyboardState).where(KeyboardState.chat_id == USER_ID))
            await session.execute(delete(BotGroup).where(BotGroup.group_id.in_([MATRIX_GROUP_A, MATRIX_GROUP_B])))
            user = await session.get(User, USER_ID)
            if user is not None:
                await session.delete(user)
            await session.execute(delete(Nation).where(Nation.group_id.in_([NATION_GROUP, MATRIX_GROUP_A, MATRIX_GROUP_B])))

    print("HANDLER-MATRIX|PASS|all active handler callbacks invoked without exception")
