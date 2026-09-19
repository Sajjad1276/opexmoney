from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

import app.handlers.nation_management as nm
from app.database.models import (
    Nation,
    NationJoinRequest,
    NationLog,
    NationMember,
    NationMemberRole,
    User,
)
from app.database.session import async_session


FOUNDER_ID = 931001
PLAYER_A_ID = 931002
PLAYER_B_ID = 931003
PLAYER_C_ID = 931004
GROUP_ID = -100931001


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, user_id, text, **kwargs):
        self.messages.append((user_id, text))
        return SimpleNamespace()


class FakeCall:
    def __init__(self, user_id: int, data: str):
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Health",
            username=f"u{user_id}",
        )
        self.data = data
        self.message = None
        self.answers: list[tuple[object, dict]] = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


async def _seed_nation():
    async with async_session() as session:
        async with session.begin():
            founder = User(
                user_id=FOUNDER_ID,
                username="founder_health",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="founder",
            )
            a = User(
                user_id=PLAYER_A_ID,
                username="player_a",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            b = User(
                user_id=PLAYER_B_ID,
                username="player_b",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            c = User(
                user_id=PLAYER_C_ID,
                username="player_c",
                balance=Decimal("500"),
                xr_balance=Decimal("0"),
                role="player",
            )
            nation = Nation(
                name="Health Republic",
                currency_code="HLT",
                group_id=GROUP_ID,
                founder_user_id=FOUNDER_ID,
                exchange_rate=Decimal("1"),
                rate_prev=Decimal("1"),
                rate_24h_open=Decimal("1"),
                trade_volume_24h=Decimal("0"),
                active_members_24h=1,
                member_count=1,
                is_active=True,
                join_policy="OPEN",
                personality="neutral",
                invite_code="OPX-HEALTH-INVITE",
                treasury=Decimal("0"),
            )
            session.add_all([founder, a, b, c, nation])
            await session.flush()
            founder.home_nation_id = nation.nation_id
            session.add(
                NationMember(
                    nation_id=nation.nation_id,
                    user_id=FOUNDER_ID,
                    role=NationMemberRole.FOUNDER,
                    is_active=True,
                )
            )
            return nation.nation_id


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    select(Nation.nation_id).where(Nation.group_id == GROUP_ID)
                )
            ).scalars().all()
            if nation_ids:
                await session.execute(
                    delete(NationJoinRequest).where(
                        NationJoinRequest.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationLog).where(NationLog.nation_id.in_(nation_ids))
                )
                await session.execute(
                    delete(NationMember).where(NationMember.nation_id.in_(nation_ids))
                )
            await session.execute(
                delete(User).where(
                    User.user_id.in_(
                        [FOUNDER_ID, PLAYER_A_ID, PLAYER_B_ID, PLAYER_C_ID]
                    )
                )
            )
            if nation_ids:
                await session.execute(
                    delete(Nation).where(Nation.nation_id.in_(nation_ids))
                )


@pytest.mark.asyncio
async def test_open_join_then_policy_to_approval_then_admin_approval(monkeypatch):
    nation_id = await _seed_nation()
    bot = FakeBot()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(nm, "_welcome_member", noop)
    monkeypatch.setattr(nm, "_notify_founder", noop)
    monkeypatch.setattr(nm, "publish_nation_event_analysis", noop)

    message, _ = await nm._join_user(
        bot=bot,
        user_id=PLAYER_A_ID,
        nation_id=nation_id,
    )
    assert "پیوستی" in message

    async with async_session() as session:
        user = await session.get(User, PLAYER_A_ID)
        member = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == PLAYER_A_ID,
            )
        )
        nation = await session.get(Nation, nation_id)
        assert user.home_nation_id == nation_id
        assert member.role == NationMemberRole.CITIZEN
        assert nation.member_count == 2

        nation.join_policy = "APPROVAL"
        await session.flush()

    request_text, _ = await nm._join_user(
        bot=bot,
        user_id=PLAYER_B_ID,
        nation_id=nation_id,
    )
    assert "درخواست عضویت ارسال شد" in request_text

    async with async_session() as session:
        request = await session.scalar(
            select(NationJoinRequest).where(
                NationJoinRequest.nation_id == nation_id,
                NationJoinRequest.user_id == PLAYER_B_ID,
            )
        )
        assert request.status == "pending"
        player_b = await session.get(User, PLAYER_B_ID)
        assert player_b.home_nation_id is None

    call = FakeCall(FOUNDER_ID, f"nm:approve:{nation_id}:{PLAYER_B_ID}")
    await nm.approve_join_request(call, bot)

    async with async_session() as session:
        request = await session.scalar(
            select(NationJoinRequest).where(
                NationJoinRequest.nation_id == nation_id,
                NationJoinRequest.user_id == PLAYER_B_ID,
            )
        )
        player_b = await session.get(User, PLAYER_B_ID)
        member_b = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == PLAYER_B_ID,
            )
        )
        assert request.status == "approved"
        assert player_b.home_nation_id == nation_id
        assert member_b.role == NationMemberRole.CITIZEN


@pytest.mark.asyncio
async def test_invite_only_rejects_wrong_code_and_accepts_right_code(monkeypatch):
    nation_id = await _seed_nation()
    bot = FakeBot()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(nm, "_welcome_member", noop)
    monkeypatch.setattr(nm, "_notify_founder", noop)
    monkeypatch.setattr(nm, "publish_nation_event_analysis", noop)

    async with async_session() as session:
        async with session.begin():
            nation = await session.get(Nation, nation_id)
            nation.join_policy = "INVITE_ONLY"

    with pytest.raises(ValueError):
        await nm._join_user(
            bot=bot,
            user_id=PLAYER_C_ID,
            nation_id=nation_id,
            invite_code="WRONG",
        )

    text, _ = await nm._join_user(
        bot=bot,
        user_id=PLAYER_C_ID,
        nation_id=nation_id,
        invite_code="OPX-HEALTH-INVITE",
    )
    assert "پیوستی" in text

    async with async_session() as session:
        user = await session.get(User, PLAYER_C_ID)
        assert user.home_nation_id == nation_id


@pytest.mark.asyncio
async def test_founder_can_promote_and_kick_member(monkeypatch):
    nation_id = await _seed_nation()
    bot = FakeBot()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(nm, "publish_nation_event_analysis", noop)
    monkeypatch.setattr(nm, "_notify_founder", noop)

    await nm._join_user(bot=bot, user_id=PLAYER_A_ID, nation_id=nation_id)

    await nm._set_member_role(
        bot=bot,
        actor_id=FOUNDER_ID,
        nation_id=nation_id,
        target_id=PLAYER_A_ID,
        new_role=NationMemberRole.MINISTER,
    )

    async with async_session() as session:
        member = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == PLAYER_A_ID,
            )
        )
        user = await session.get(User, PLAYER_A_ID)
        assert member.role == NationMemberRole.MINISTER
        assert user.role == "minister"

    call = FakeCall(FOUNDER_ID, f"nm:kick:{nation_id}:{PLAYER_A_ID}")
    await nm.kick_member(call, bot)

    async with async_session() as session:
        member = await session.scalar(
            select(NationMember).where(
                NationMember.nation_id == nation_id,
                NationMember.user_id == PLAYER_A_ID,
            )
        )
        user = await session.get(User, PLAYER_A_ID)
        assert member.is_active is False
        assert user.home_nation_id is None
        assert user.role == "player"
