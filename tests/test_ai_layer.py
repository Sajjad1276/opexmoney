from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import secrets
from types import SimpleNamespace

import pytest

from ai import AICompanion
from ai.context_builder import build_user_context
from ai.personality import AI_FALLBACK_MESSAGE
from ai.prompt_engine import build_cache_key, build_dynamic_prompt
from ai.response_parser import parse_ai_response
from app.database.models import ActivityType, Nation, Transaction, User, UserActivity
from app.database.session import async_session
from config import settings

TARGET_USER_ID = 982450701
TARGET_NATION_ID = 982450701


@pytest.mark.asyncio
async def test_context_builder_reads_real_database(monkeypatch) -> None:
    async def fake_last_message(user_id: int) -> str | None:
        assert user_id == TARGET_USER_ID
        return "قبلاً درباره نرخ ارز صحبت کردیم."

    monkeypatch.setattr("ai.context_builder.get_last_bot_message", fake_last_message)

    async with async_session() as session:
        async with session.begin():
            nation = Nation(
                nation_id=TARGET_NATION_ID,
                group_id=-100982450701,
                name="Test Nation",
                currency_code="TST",
                exchange_rate=Decimal("2.5000"),
                rate_prev=Decimal("2.3000"),
                rate_24h_open=Decimal("2.1000"),
                trade_volume_24h=Decimal("1000"),
                active_members_24h=4,
                member_count=7,
                invite_code=secrets.token_urlsafe(8),
                nation_rank=3,
                is_active=True,
            )
            user = User(
                user_id=TARGET_USER_ID,
                username="AI Tester",
                home_nation_id=TARGET_NATION_ID,
                balance=Decimal("850.00"),
                xr_balance=Decimal("120.00"),
                role="player",
            )
            session.add_all([nation, user])
            await session.flush()
            for index in range(2):
                session.add(
                    Transaction(
                        user_id=TARGET_USER_ID,
                        nation_id=TARGET_NATION_ID,
                        transaction_type="buy",
                        spend_xr=Decimal("50"),
                        amount=Decimal("20"),
                        fee_xr=Decimal("1"),
                        rate=Decimal("2.5"),
                        created_at=datetime.utcnow() - timedelta(minutes=index),
                    )
                )
            for day_offset in range(3):
                session.add(
                    UserActivity(
                        user_id=TARGET_USER_ID,
                        nation_id=TARGET_NATION_ID,
                        activity_type=ActivityType.LOGIN,
                        created_at=datetime.utcnow() - timedelta(days=day_offset),
                    )
                )

        context = await build_user_context(TARGET_USER_ID, session)

    assert context["user"]["name"] == "AI Tester"
    assert context["home_nation"]["rank"] == 3
    assert context["home_nation"]["exchange_rate"] == "2.5000"
    assert len(context["transactions"]) == 2
    assert context["active_days"] == 3
    assert context["enemies"] == []
    assert context["allies"] == []
    assert context["last_bot_message"] == "قبلاً درباره نرخ ارز صحبت کردیم."

    async with async_session() as cleanup_session:
        async with cleanup_session.begin():
            await cleanup_session.execute(
                Transaction.__table__.delete().where(Transaction.user_id == TARGET_USER_ID)
            )
            await cleanup_session.execute(
                UserActivity.__table__.delete().where(UserActivity.user_id == TARGET_USER_ID)
            )
            await cleanup_session.execute(
                User.__table__.delete().where(User.user_id == TARGET_USER_ID)
            )
            await cleanup_session.execute(
                Nation.__table__.delete().where(Nation.nation_id == TARGET_NATION_ID)
            )


def test_response_parser_enforces_contract() -> None:
    parsed = parse_ai_response(
        '{"reply":"<b>خوبی</b>؟\\nنرخ ارز امروز <u>مثبت</u> شده.\\nیک خط اضافه."}'
    )
    assert parsed.reply == "<b>خوبی</b>؟\nنرخ ارز امروز <u>مثبت</u> شده.\nیک خط اضافه."

    parsed_plain = parse_ai_response("سلام\nخط دوم\nخط سوم\nخط چهارم\nخط پنجم")
    assert len(parsed_plain.reply.splitlines()) == 4

    parsed_unsafe = parse_ai_response('{"reply":"<script>alert(1)</script><b>سلام</b>"}')
    assert "<script>" not in parsed_unsafe.reply
    assert "<b>سلام</b>" in parsed_unsafe.reply


def test_prompt_and_cache_key_are_deterministic() -> None:
    context = {"user": {"name": "سجاد"}, "active_days": 5}
    prompt = build_dynamic_prompt(context=context, user_message="وضعیتم چطوره؟")
    key_one = build_cache_key(
        model=settings.ai_model,
        context=context,
        user_message="وضعیتم چطوره؟",
    )
    key_two = build_cache_key(
        model=settings.ai_model,
        context=context,
        user_message="وضعیتم چطوره؟",
    )
    assert "سجاد" in prompt
    assert key_one == key_two
    assert key_one.startswith("opex:ai:response:")


@pytest.mark.asyncio
async def test_ai_fallback_when_provider_is_unavailable(monkeypatch) -> None:
    companion = AICompanion()

    async def fake_context(user_id, db):
        return {"user": {"id": user_id}, "transactions": []}

    async def fake_cache(_key):
        return None

    monkeypatch.setattr("ai.build_user_context", fake_context)
    monkeypatch.setattr("ai.get_cached_response", fake_cache)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "ai_enabled", True)

    result = await companion.reply(123, "سلام اوپکس", SimpleNamespace())
    assert result == AI_FALLBACK_MESSAGE


@pytest.mark.asyncio
async def test_ai_success_path_parses_and_caches(monkeypatch) -> None:
    companion = AICompanion()
    events: list[str] = []

    async def fake_context(user_id, db):
        return {"user": {"id": user_id}, "transactions": []}

    async def fake_cache(_key):
        return None

    async def fake_set_cache(_key, _value):
        events.append("cache")

    async def fake_remember(_user_id, _message):
        events.append("remember")

    class FakeModels:
        async def generate_content(self, **kwargs):
            assert kwargs["model"] == settings.ai_model
            assert kwargs["config"].system_instruction
            return SimpleNamespace(text='<b>سلام</b>، معامله‌گر.')

    class FakeAsyncClient:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            self.aio = FakeAsyncClient()

    monkeypatch.setattr("ai.build_user_context", fake_context)
    monkeypatch.setattr("ai.get_cached_response", fake_cache)
    monkeypatch.setattr("ai.set_cached_response", fake_set_cache)
    monkeypatch.setattr("ai.remember_bot_message", fake_remember)
    monkeypatch.setattr("ai.genai.Client", FakeClient)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_model", "gemini-2.5-flash-lite")

    result = await companion.reply(123, "سلام اوپکس", SimpleNamespace())

    assert result == "<b>سلام</b>، معامله‌گر."
    assert events == ["cache", "remember"]


@pytest.mark.asyncio
async def test_ai_health_check_uses_gemini_model(monkeypatch) -> None:
    companion = AICompanion()

    class FakeModels:
        async def get(self, **kwargs):
            assert kwargs["model"] == settings.ai_model
            return SimpleNamespace(name=f"models/{settings.ai_model}")

    class FakeAsyncClient:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            self.aio = FakeAsyncClient()

    monkeypatch.setattr("ai.genai.Client", FakeClient)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_enabled", True)

    assert await companion.health_check() is True


def test_main_wires_ai_router() -> None:
    from pathlib import Path

    source = Path("main.py").read_text(encoding="utf-8")
    assert "dp.include_router(ai_router)" in source
