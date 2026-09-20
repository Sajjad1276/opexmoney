from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.database.models import Nation, NationMember, NationMemberRole, NationTelegramMember, User
from app.database.session import async_session
from app.services.nation_control import require_nation_control


HUMAN_USER = 946001
AI_USER = 946002
HUMAN_GROUP = -100946001


async def _cleanup() -> None:
    async with async_session() as session:
        async with session.begin():
            nation_ids = (
                await session.execute(
                    Nation.__table__.select().with_only_columns(Nation.nation_id).where(
                        Nation.currency_code.in_(["CTL", "AIC"])
                    )
                )
            ).scalars().all()
            if nation_ids:
                await session.execute(
                    delete(NationTelegramMember).where(
                        NationTelegramMember.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    delete(NationMember).where(
                        NationMember.nation_id.in_(nation_ids)
                    )
                )
                await session.execute(
                    User.__table__.update()
                    .where(
                        User.user_id.in_([HUMAN_USER, AI_USER]),
                        User.home_nation_id.in_(nation_ids),
                    )
                    .values(home_nation_id=None)
                )
                await session.execute(
                    delete(Nation).where(Nation.nation_id.in_(nation_ids))
                )
            await session.execute(
                delete(User).where(User.user_id.in_([HUMAN_USER, AI_USER]))
            )


@pytest.mark.asyncio
async def test_human_nation_control_requires_telegram_membership_but_ai_control_stays_virtual():
    await _cleanup()
    async with async_session() as session:
        async with session.begin():
            human = Nation(
                group_id=HUMAN_GROUP,
                name="Control Republic",
                currency_code="CTL",
                invite_code="OPX-CTL-946001",
                is_active=True,
                is_ai=False,
            )
            ai = Nation(
                group_id=None,
                name="Control AI",
                currency_code="AIC",
                invite_code="OPX-AIC-946002",
                is_active=True,
                is_ai=True,
            )
            human_user = User(user_id=HUMAN_USER, username="control_human")
            ai_user = User(user_id=AI_USER, username="control_ai", is_ai=True)
            session.add_all([human, ai, human_user, ai_user])
            await session.flush()
            human_user.home_nation_id = human.nation_id
            ai_user.home_nation_id = ai.nation_id
            session.add_all(
                [
                    NationMember(
                        nation_id=human.nation_id,
                        user_id=HUMAN_USER,
                        role=NationMemberRole.FOUNDER,
                        is_active=True,
                    ),
                    NationMember(
                        nation_id=ai.nation_id,
                        user_id=AI_USER,
                        role=NationMemberRole.FOUNDER,
                        is_active=True,
                    ),
                ]
            )

    async with async_session() as session:
        with pytest.raises(ValueError, match="عضویت تلگرامی"):
            await require_nation_control(
                session,
                user_id=HUMAN_USER,
                nation_id=(await session.scalar(
                    Nation.__table__.select().with_only_columns(Nation.nation_id).where(
                        Nation.currency_code == "CTL"
                    )
                )),
                allowed_roles={NationMemberRole.FOUNDER},
            )

    async with async_session() as session:
        async with session.begin():
            human_id = await session.scalar(
                Nation.__table__.select().with_only_columns(Nation.nation_id).where(
                    Nation.currency_code == "CTL"
                )
            )
            session.add(
                NationTelegramMember(
                    nation_id=human_id,
                    telegram_user_id=HUMAN_USER,
                    telegram_status="administrator",
                    is_member=True,
                    is_active=True,
                )
            )

    async with async_session() as session:
        human_id = await session.scalar(
            Nation.__table__.select().with_only_columns(Nation.nation_id).where(
                Nation.currency_code == "CTL"
            )
        )
        ai_id = await session.scalar(
            Nation.__table__.select().with_only_columns(Nation.nation_id).where(
                Nation.currency_code == "AIC"
            )
        )
        _, _, human_member = await require_nation_control(
            session,
            user_id=HUMAN_USER,
            nation_id=human_id,
            allowed_roles={NationMemberRole.FOUNDER},
        )
        _, _, ai_member = await require_nation_control(
            session,
            user_id=AI_USER,
            nation_id=ai_id,
            allowed_roles={NationMemberRole.FOUNDER},
        )
        assert human_member is not None
        assert ai_member is not None

    await _cleanup()
