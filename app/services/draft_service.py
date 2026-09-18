from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OnboardingDraft
from app.database.session import async_session


async def _save(session: AsyncSession, player_id: int, step_key: str, payload: Mapping[str, Any]) -> OnboardingDraft:
    draft = await session.get(OnboardingDraft, player_id)
    if draft is None:
        draft = OnboardingDraft(player_id=player_id, step_key=step_key, payload=dict(payload))
        session.add(draft)
    else:
        draft.step_key = step_key
        draft.payload = dict(payload)
    await session.flush()
    return draft


async def save_draft(
    player_id: int,
    step_key: str,
    payload: Mapping[str, Any],
    *,
    session: AsyncSession | None = None,
) -> None:
    if session is not None:
        await _save(session, player_id, step_key, payload)
        return
    async with async_session() as owned_session:
        async with owned_session.begin():
            await _save(owned_session, player_id, step_key, payload)


async def get_draft(
    player_id: int,
    *,
    session: AsyncSession | None = None,
) -> OnboardingDraft | None:
    if session is not None:
        return await session.get(OnboardingDraft, player_id)
    async with async_session() as owned_session:
        return await owned_session.get(OnboardingDraft, player_id)


async def clear_draft(
    player_id: int,
    *,
    session: AsyncSession | None = None,
) -> None:
    if session is not None:
        draft = await session.get(OnboardingDraft, player_id)
        if draft is not None:
            await session.delete(draft)
            await session.flush()
        return
    async with async_session() as owned_session:
        async with owned_session.begin():
            draft = await owned_session.get(OnboardingDraft, player_id)
            if draft is not None:
                await owned_session.delete(draft)
