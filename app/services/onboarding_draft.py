from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OnboardingDraft


async def save_draft(
    session: AsyncSession,
    *,
    player_id: int,
    step_key: str,
    payload: dict[str, Any] | None = None,
) -> OnboardingDraft:
    result = await session.execute(
        select(OnboardingDraft).where(OnboardingDraft.player_id == player_id)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        draft = OnboardingDraft(
            player_id=player_id,
            step_key=step_key,
            payload=payload or {},
            updated_at=datetime.utcnow(),
        )
        session.add(draft)
    else:
        draft.step_key = step_key
        draft.payload = payload or {}
        draft.updated_at = datetime.utcnow()
    await session.flush()
    return draft


async def get_draft(
    session: AsyncSession,
    player_id: int,
) -> OnboardingDraft | None:
    return await session.scalar(
        select(OnboardingDraft).where(OnboardingDraft.player_id == player_id)
    )


async def clear_draft(
    session: AsyncSession,
    player_id: int,
) -> None:
    draft = await get_draft(session, player_id)
    if draft is not None:
        await session.delete(draft)
        await session.flush()
