from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.database.models import NationFoundingDraft, User
from app.database.session import async_session
from app.services.founder_service import expire_founder_drafts


USER_IDS = [941001, 941002, 941003]


async def _cleanup() -> None:
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                delete(NationFoundingDraft).where(
                    NationFoundingDraft.founder_user_id.in_(USER_IDS)
                )
            )
            await session.execute(delete(User).where(User.user_id.in_(USER_IDS)))


@pytest.mark.asyncio
async def test_expire_founder_drafts_marks_only_expired_active_rows():
    await _cleanup()

    async with async_session() as session:
        async with session.begin():
            session.add_all(
                [
                    User(user_id=USER_IDS[0], username="phase4expired"),
                    User(user_id=USER_IDS[1], username="phase4live"),
                    User(user_id=USER_IDS[2], username="phase4done"),
                ]
            )
            await session.flush()
            now = datetime.utcnow()
            session.add_all(
                [
                    NationFoundingDraft(
                        founder_user_id=USER_IDS[0],
                        launch_token="phase4-expired",
                        status="WAITING_GROUP",
                        expires_at=now - timedelta(seconds=1),
                    ),
                    NationFoundingDraft(
                        founder_user_id=USER_IDS[1],
                        launch_token="phase4-live",
                        status="WAITING_GROUP",
                        expires_at=now + timedelta(minutes=10),
                    ),
                    NationFoundingDraft(
                        founder_user_id=USER_IDS[2],
                        launch_token="phase4-done",
                        status="COMPLETED",
                        expires_at=now - timedelta(days=1),
                    ),
                ]
            )

    async with async_session() as session:
        async with session.begin():
            expired = await expire_founder_drafts(session)

    assert expired == 1

    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    NationFoundingDraft.founder_user_id,
                    NationFoundingDraft.status,
                ).where(
                    NationFoundingDraft.founder_user_id.in_(USER_IDS)
                )
            )
        ).all()

    statuses = dict(rows)
    assert statuses[USER_IDS[0]] == "EXPIRED"
    assert statuses[USER_IDS[1]] == "WAITING_GROUP"
    assert statuses[USER_IDS[2]] == "COMPLETED"

    await _cleanup()
