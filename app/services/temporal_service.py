from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PlayerTemporalProfile, UserActivity
from config import settings


def _local_hour(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(settings.temporal_timezone)).hour


async def _least_active_hour(
    session: AsyncSession,
    player_id: int,
    now: datetime,
) -> int:
    since = now - timedelta(days=7)
    rows = (
        await session.execute(
            select(UserActivity.created_at)
            .where(
                UserActivity.user_id == player_id,
                UserActivity.created_at >= since,
            )
        )
    ).scalars().all()

    counts = [0] * 24
    for created_at in rows:
        counts[_local_hour(created_at)] += 1

    return min(range(24), key=lambda hour: (counts[hour], hour))


async def ensure_temporal_profile(
    session: AsyncSession,
    player_id: int,
    *,
    now: datetime | None = None,
) -> PlayerTemporalProfile:
    now = now or datetime.utcnow()
    existing = await session.get(PlayerTemporalProfile, player_id)
    if existing is not None:
        return existing

    peak_hour = await _least_active_hour(session, player_id, now)
    profile = PlayerTemporalProfile(
        player_id=player_id,
        peak_hour_start=peak_hour,
        peak_window_hours=settings.temporal_peak_window_hours,
        peak_multiplier=settings.temporal_peak_multiplier,
        assigned_at=now,
        next_rotation_at=now + timedelta(days=settings.temporal_rotation_days),
    )
    session.add(profile)
    await session.flush()
    return profile


async def rotate_due_profiles(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.utcnow()
    profiles = (
        await session.execute(
            select(PlayerTemporalProfile)
            .where(PlayerTemporalProfile.next_rotation_at <= now)
            .with_for_update()
        )
    ).scalars().all()

    for profile in profiles:
        profile.peak_hour_start = await _least_active_hour(session, profile.player_id, now)
        profile.assigned_at = now
        profile.next_rotation_at = now + timedelta(days=settings.temporal_rotation_days)

    return len(profiles)


def is_peak_window(
    profile: PlayerTemporalProfile,
    now: datetime,
) -> bool:
    local_hour = _local_hour(now)
    width = max(1, min(24, int(profile.peak_window_hours)))
    distance = (local_hour - int(profile.peak_hour_start)) % 24
    return distance < width


async def get_peak_multiplier(
    session: AsyncSession,
    player_id: int,
    *,
    now: datetime | None = None,
) -> Decimal:
    now = now or datetime.utcnow()
    profile = await session.get(PlayerTemporalProfile, player_id)
    if profile is None:
        return Decimal("1")
    if not is_peak_window(profile, now):
        return Decimal("1")
    return Decimal(str(profile.peak_multiplier))
