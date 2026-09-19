from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Lesson, User, UserLessonProgress, UserXP

logger = logging.getLogger(__name__)

MODULE_META = {
    1: {"title": "مبانی بازی", "emoji": "📖", "min_level": "beginner"},
    2: {"title": "نرخ ارز", "emoji": "📈", "min_level": "beginner"},
    3: {"title": "استراتژی", "emoji": "⚔️", "min_level": "beginner"},
    4: {"title": "حکومت", "emoji": "🏛", "min_level": "mid"},
    5: {"title": "جنگ اقتصادی", "emoji": "💣", "min_level": "mid"},
    6: {"title": "تسلط", "emoji": "👑", "min_level": "pro"},
}

LEVEL_ORDER = ["beginner", "mid", "pro"]

XP_THRESHOLDS = {
    "beginner": 0,
    "mid": 100,
    "pro": 300,
}

LEVEL_EMOJI = {
    "beginner": "🌱",
    "mid": "🔥",
    "pro": "👑",
}

SESSION_TTL = 3600
SESSION_KEY = "academy_session:{user_id}"


def _level_index(level: str) -> int:
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return 0


def _level_allows(user_level: str, required_level: str) -> bool:
    return _level_index(user_level) >= _level_index(required_level)


async def get_user_xp(session: AsyncSession, user_id: int) -> UserXP:
    user_xp = await session.scalar(
        select(UserXP)
        .where(UserXP.user_id == user_id)
        .with_for_update()
    )
    if user_xp is None:
        user_xp = UserXP(
            user_id=user_id,
            total_xp=0,
            level="beginner",
            last_updated=datetime.utcnow(),
        )
        session.add(user_xp)
    return user_xp


async def recalculate_level(session: AsyncSession, user_xp: UserXP) -> bool:
    new_level = "beginner"
    for level in reversed(LEVEL_ORDER):
        if user_xp.total_xp >= XP_THRESHOLDS[level]:
            new_level = level
            break

    if new_level != user_xp.level:
        user_xp.level = new_level
        user_xp.last_updated = datetime.utcnow()
        return True

    return False


async def _done_count_for_module(
    session: AsyncSession,
    user_id: int,
    module_id: int,
) -> int:
    count = await session.scalar(
        select(func.count(UserLessonProgress.id))
        .join(Lesson, Lesson.id == UserLessonProgress.lesson_id)
        .where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.status == "done",
            Lesson.module_id == module_id,
            Lesson.is_active.is_(True),
        )
    )
    return int(count or 0)


async def _is_module_locked(
    session: AsyncSession,
    user_id: int,
    module_id: int,
    user_level: str,
) -> bool:
    meta = MODULE_META[module_id]

    if not _level_allows(user_level, meta["min_level"]):
        return True

    if module_id > 1:
        previous_done = await _done_count_for_module(
            session,
            user_id,
            module_id - 1,
        )
        if previous_done == 0:
            return True

    return False


async def get_module_status(
    session: AsyncSession,
    user_id: int,
    user_level: str,
) -> list[dict]:
    statuses: list[dict] = []

    for module_id in range(1, 7):
        meta = MODULE_META[module_id]
        lesson_ids = list(
            (
                await session.execute(
                    select(Lesson.id)
                    .where(
                        Lesson.module_id == module_id,
                        Lesson.is_active.is_(True),
                    )
                    .order_by(Lesson.order.asc())
                )
            ).scalars().all()
        )

        progress_map: dict[int, str] = {}
        if lesson_ids:
            progress_rows = (
                await session.execute(
                    select(
                        UserLessonProgress.lesson_id,
                        UserLessonProgress.status,
                    )
                    .where(
                        UserLessonProgress.user_id == user_id,
                        UserLessonProgress.lesson_id.in_(lesson_ids),
                    )
                )
            ).all()
            progress_map = {
                int(lesson_id): str(status)
                for lesson_id, status in progress_rows
            }

        done_count = sum(
            1 for lesson_id in lesson_ids if progress_map.get(lesson_id) == "done"
        )
        locked = await _is_module_locked(
            session,
            user_id,
            module_id,
            user_level,
        )

        total_count = len(lesson_ids)
        if locked:
            status = "locked"
        elif total_count > 0 and done_count == total_count:
            status = "complete"
        elif done_count > 0:
            status = "in_progress"
        else:
            status = "open"

        statuses.append(
            {
                "module_id": module_id,
                "title": meta["title"],
                "emoji": meta["emoji"],
                "locked": locked,
                "done_count": done_count,
                "total_count": total_count,
                "status": status,
            }
        )

    return statuses


async def get_module_lessons(
    session: AsyncSession,
    user_id: int,
    module_id: int,
) -> list[dict]:
    if module_id not in MODULE_META:
        return []

    user_xp = await get_user_xp(session, user_id)
    locked = await _is_module_locked(
        session,
        user_id,
        module_id,
        user_xp.level,
    )

    lessons = (
        await session.execute(
            select(Lesson)
            .where(
                Lesson.module_id == module_id,
                Lesson.is_active.is_(True),
            )
            .order_by(Lesson.order.asc())
        )
    ).scalars().all()

    if not lessons:
        return []

    lesson_ids = [lesson.id for lesson in lessons]
    progress_rows = (
        await session.execute(
            select(UserLessonProgress)
            .where(
                UserLessonProgress.user_id == user_id,
                UserLessonProgress.lesson_id.in_(lesson_ids),
            )
        )
    ).scalars().all()
    progress_map = {row.lesson_id: row for row in progress_rows}

    first_lesson = lessons[0]
    if not locked and first_lesson.id not in progress_map:
        first_progress = UserLessonProgress(
            user_id=user_id,
            lesson_id=first_lesson.id,
            status="open",
        )
        session.add(first_progress)
        progress_map[first_lesson.id] = first_progress

    result: list[dict] = []
    for lesson in lessons:
        progress = progress_map.get(lesson.id)
        status = progress.status if progress is not None else "locked"

        if locked and status != "done":
            status = "locked"

        result.append(
            {
                "lesson_id": lesson.id,
                "order": lesson.order,
                "title_fa": lesson.title_fa,
                "xp_reward": int(lesson.xp_reward),
                "xr_reward": Decimal(str(lesson.xr_reward)),
                "status": status,
                "quiz_score": (
                    int(progress.quiz_score)
                    if progress is not None and progress.quiz_score is not None
                    else None
                ),
            }
        )

    return result


async def get_lesson(
    session: AsyncSession,
    lesson_id: int,
) -> Lesson | None:
    return await session.scalar(
        select(Lesson).where(
            Lesson.id == lesson_id,
            Lesson.is_active.is_(True),
        )
    )


async def get_user_progress(
    session: AsyncSession,
    user_id: int,
    lesson_id: int,
) -> UserLessonProgress | None:
    return await session.scalar(
        select(UserLessonProgress)
        .where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.lesson_id == lesson_id,
        )
        .limit(1)
    )


async def open_lesson(
    session: AsyncSession,
    user_id: int,
    lesson_id: int,
) -> None:
    lesson = await get_lesson(session, lesson_id)
    if lesson is None:
        return

    progress = await session.scalar(
        select(UserLessonProgress)
        .where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.lesson_id == lesson_id,
        )
        .with_for_update()
    )

    if progress is None:
        session.add(
            UserLessonProgress(
                user_id=user_id,
                lesson_id=lesson_id,
                status="open",
            )
        )
    elif progress.status == "locked":
        progress.status = "open"


async def complete_lesson(
    session: AsyncSession,
    user_id: int,
    lesson_id: int,
    quiz_score: int,
) -> dict:
    lesson = await get_lesson(session, lesson_id)
    if lesson is None:
        return {"ok": False, "reason": "lesson_not_found"}

    progress = await session.scalar(
        select(UserLessonProgress)
        .where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.lesson_id == lesson_id,
        )
        .with_for_update()
    )
    if progress is None:
        return {"ok": False, "reason": "not_opened"}

    if progress.status == "done":
        return {"ok": False, "reason": "already_done"}

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        return {"ok": False, "reason": "user_not_found"}

    module_lessons = (
        await session.execute(
            select(Lesson)
            .where(
                Lesson.module_id == lesson.module_id,
                Lesson.is_active.is_(True),
            )
            .order_by(Lesson.order.asc())
        )
    ).scalars().all()
    module_lesson_ids = [item.id for item in module_lessons]

    done_before = 0
    if module_lesson_ids:
        done_before = int(
            await session.scalar(
                select(func.count(UserLessonProgress.id))
                .where(
                    UserLessonProgress.user_id == user_id,
                    UserLessonProgress.lesson_id.in_(module_lesson_ids),
                    UserLessonProgress.status == "done",
                )
            )
            or 0
        )

    next_lesson = await session.scalar(
        select(Lesson)
        .where(
            Lesson.module_id == lesson.module_id,
            Lesson.order == lesson.order + 1,
            Lesson.is_active.is_(True),
        )
        .limit(1)
    )
    next_progress = None
    if next_lesson is not None:
        next_progress = await session.scalar(
            select(UserLessonProgress)
            .where(
                UserLessonProgress.user_id == user_id,
                UserLessonProgress.lesson_id == next_lesson.id,
            )
            .with_for_update()
        )

    module_completed = bool(module_lessons) and (
        done_before + 1 >= len(module_lessons)
    )

    xp = await get_user_xp(session, user_id)
    xp.total_xp = int(xp.total_xp) + int(lesson.xp_reward)
    level_up = await recalculate_level(session, xp)

    candidate_first_lesson = None
    candidate_first_progress = None
    next_module_id = None
    if module_completed and lesson.module_id < 6:
        candidate_id = lesson.module_id + 1
        candidate_meta = MODULE_META[candidate_id]
        if _level_allows(xp.level, candidate_meta["min_level"]):
            with session.no_autoflush:
                candidate_first_lesson = await session.scalar(
                    select(Lesson)
                    .where(
                        Lesson.module_id == candidate_id,
                        Lesson.is_active.is_(True),
                    )
                    .order_by(Lesson.order.asc())
                    .limit(1)
                )
                if candidate_first_lesson is not None:
                    candidate_first_progress = await session.scalar(
                        select(UserLessonProgress)
                        .where(
                            UserLessonProgress.user_id == user_id,
                            UserLessonProgress.lesson_id == candidate_first_lesson.id,
                        )
                        .with_for_update()
                    )
                    next_module_id = candidate_id

    xr_gained = Decimal(str(lesson.xr_reward or Decimal("0")))
    if xr_gained > 0:
        user.xr_balance = Decimal(str(user.xr_balance or Decimal("0"))) + xr_gained

    now = datetime.utcnow()
    progress.status = "done"
    progress.quiz_score = max(0, min(100, int(quiz_score)))
    progress.completed_at = now

    if next_lesson is not None:
        if next_progress is None:
            session.add(
                UserLessonProgress(
                    user_id=user_id,
                    lesson_id=next_lesson.id,
                    status="open",
                )
            )
        elif next_progress.status == "locked":
            next_progress.status = "open"

    if candidate_first_lesson is not None:
        if candidate_first_progress is None:
            session.add(
                UserLessonProgress(
                    user_id=user_id,
                    lesson_id=candidate_first_lesson.id,
                    status="open",
                )
            )
        elif candidate_first_progress.status == "locked":
            candidate_first_progress.status = "open"

    await session.flush()

    return {
        "ok": True,
        "xp_gained": int(lesson.xp_reward),
        "xr_gained": xr_gained,
        "new_total_xp": int(xp.total_xp),
        "level_up": bool(level_up),
        "new_level": xp.level if level_up else None,
        "module_completed": module_completed,
        "next_module_id": next_module_id,
        "module_id": lesson.module_id,
    }


async def get_ai_session(
    redis: Redis | None,
    user_id: int,
) -> dict:
    default = {"lesson_id": None, "history": []}
    if redis is None:
        return default

    try:
        raw = await redis.get(SESSION_KEY.format(user_id=user_id))
    except Exception:
        logger.warning(
            "Academy Redis read failed for user_id=%s; using empty history",
            user_id,
            exc_info=True,
        )
        return default

    if not raw:
        return default

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "Invalid Academy Redis session for user_id=%s",
            user_id,
        )
        return default

    if not isinstance(data, dict):
        return default

    lesson_id = data.get("lesson_id")
    try:
        lesson_id = int(lesson_id) if lesson_id is not None else None
    except (TypeError, ValueError):
        lesson_id = None

    history = data.get("history")
    if not isinstance(history, list):
        history = []

    clean_history = [
        item
        for item in history
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]
    return {"lesson_id": lesson_id, "history": clean_history}


async def save_ai_session(
    redis: Redis | None,
    user_id: int,
    lesson_id: int | None,
    history: list[dict],
) -> None:
    if redis is None:
        return

    data: dict[str, object] = {
        "lesson_id": lesson_id,
        "history": history[-10:],
    }
    try:
        await redis.setex(
            SESSION_KEY.format(user_id=user_id),
            SESSION_TTL,
            json.dumps(data, ensure_ascii=False),
        )
    except Exception:
        logger.warning(
            "Academy Redis write failed for user_id=%s; continuing without persistence",
            user_id,
            exc_info=True,
        )


async def clear_ai_session(
    redis: Redis | None,
    user_id: int,
) -> None:
    if redis is None:
        return

    try:
        await redis.delete(SESSION_KEY.format(user_id=user_id))
    except Exception:
        logger.warning(
            "Academy Redis delete failed for user_id=%s",
            user_id,
            exc_info=True,
        )
