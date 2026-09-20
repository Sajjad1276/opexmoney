from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Mission, User, UserMissionProgress
from app.services.user_service import sync_user_balance


@dataclass(frozen=True)
class MissionStatus:
    mission_id: int
    key: str
    title_fa: str
    progress: int
    target_count: int
    completed: bool
    claimed: bool
    reward_xr: Decimal
    pct: int


def _reset_window(mission_type: str) -> timedelta | None:
    if mission_type == "daily":
        return timedelta(days=1)
    if mission_type == "weekly":
        return timedelta(days=7)
    return None


def _next_reset_at(mission_type: str, now: datetime) -> datetime | None:
    window = _reset_window(mission_type)
    return now + window if window is not None else None


def _reset_expired_progress(
    progress: UserMissionProgress,
    mission_type: str,
    now: datetime,
) -> bool:
    if mission_type not in {"daily", "weekly"}:
        return False

    if progress.reset_at is not None and progress.reset_at > now:
        return False

    progress.progress = 0
    progress.completed = False
    progress.completed_at = None
    progress.claimed = False
    progress.reset_at = _next_reset_at(mission_type, now)
    return True


async def get_user_missions(
    session: AsyncSession,
    user_id: int,
) -> dict[str, list[MissionStatus]]:
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = (
        await session.execute(
            select(Mission, UserMissionProgress)
            .outerjoin(
                UserMissionProgress,
                and_(
                    UserMissionProgress.mission_id == Mission.id,
                    UserMissionProgress.user_id == user_id,
                ),
            )
            .where(Mission.is_active.is_(True))
            .order_by(Mission.id.asc())
        )
    ).all()

    grouped: dict[str, list[MissionStatus]] = {
        "daily": [],
        "weekly": [],
        "permanent": [],
    }

    for mission, progress in rows:
        if progress is not None:
            _reset_expired_progress(progress, mission.mission_type, now)

        current = progress.progress if progress is not None else 0
        completed = progress.completed if progress is not None else False
        claimed = progress.claimed if progress is not None else False
        target = max(int(mission.target_count), 0)
        pct = 100 if target == 0 else min(100, int(round(current / target * 100)))

        status = MissionStatus(
            mission_id=mission.id,
            key=mission.key,
            title_fa=mission.title_fa,
            progress=current,
            target_count=target,
            completed=completed,
            claimed=claimed,
            reward_xr=Decimal(str(mission.reward_xr)),
            pct=pct,
        )
        grouped.setdefault(mission.mission_type, []).append(status)

    return grouped


async def increment_mission(
    session: AsyncSession,
    user_id: int,
    mission_key: str,
    amount: int = 1,
) -> None:
    amount = int(amount)
    if amount <= 0:
        return

    mission = await session.scalar(
        select(Mission)
        .where(
            Mission.key == mission_key,
            Mission.is_active.is_(True),
        )
        .limit(1)
    )
    if mission is None:
        return

    now = datetime.utcnow()
    progress = await session.scalar(
        select(UserMissionProgress)
        .where(
            UserMissionProgress.user_id == user_id,
            UserMissionProgress.mission_id == mission.id,
        )
        .with_for_update()
    )

    if progress is not None:
        _reset_expired_progress(progress, mission.mission_type, now)

        if progress.completed:
            return
    else:
        progress = UserMissionProgress(
            user_id=user_id,
            mission_id=mission.id,
            reset_at=_next_reset_at(mission.mission_type, now),
        )
        session.add(progress)
        await session.flush()

    target = max(int(mission.target_count), 0)
    if target == 0:
        progress.completed = True
        progress.completed_at = now
        return

    progress.progress = min(
        target,
        max(0, int(progress.progress)) + amount,
    )
    if progress.progress >= target:
        progress.completed = True
        progress.completed_at = now


async def claim_reward(
    session: AsyncSession,
    user_id: int,
    mission_id: int,
) -> dict:
    progress = await session.scalar(
        select(UserMissionProgress)
        .where(
            UserMissionProgress.user_id == user_id,
            UserMissionProgress.mission_id == mission_id,
        )
        .with_for_update()
    )
    if progress is None:
        return {"ok": False, "reason": "not_completed"}

    if not progress.completed:
        return {"ok": False, "reason": "not_completed"}

    if progress.claimed:
        return {"ok": False, "reason": "already_claimed"}

    mission = await session.scalar(
        select(Mission)
        .where(Mission.id == mission_id)
    )
    if mission is None or not mission.is_active:
        return {"ok": False, "reason": "mission_not_found"}

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return {"ok": False, "reason": "user_not_found"}

    reward_xr = Decimal(str(mission.reward_xr))
    progress.claimed = True
    user.xr_balance = Decimal(str(user.xr_balance or Decimal("0"))) + reward_xr

    await sync_user_balance(session, user_id)

    return {
        "ok": True,
        "reward_xr": reward_xr,
        "mission_title": mission.title_fa,
    }


async def check_permanent_missions(
    session: AsyncSession,
    user_id: int,
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        return

    if Decimal(str(user.xr_balance or Decimal("0"))) >= Decimal("5000"):
        await increment_mission(
            session,
            user_id,
            "RICH_PLAYER",
            amount=5000,
        )

    if user.home_nation_id is not None:
        await increment_mission(
            session,
            user_id,
            "JOIN_NATION",
            amount=1,
        )
