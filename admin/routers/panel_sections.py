from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.dependencies import get_db_session, get_redis
from ai import companion
from app.database.models import (
    ActivityType,
    AIUsageLog,
    CurrencyHolding,
    DecisionSnapshot,
    GovernanceLedger,
    Lesson,
    Mission,
    Nation,
    NationFoundingDraft,
    NationMembership,
    NationTreasury,
    TreasuryLog,
    Transaction,
    User,
    UserActivity,
    UserLessonProgress,
    UserMissionProgress,
    UserXP,
)
from app.schedulers.admin_control import SCHEDULER_JOB_META
from app.services.nation.founder_service import finalize_draft

router = APIRouter(tags=["admin-panel-sections"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LessonBody(StrictBody):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=4000)
    level: Literal["beginner", "intermediate", "advanced"]
    xp_reward: int = Field(ge=0, le=100000)
    xr_reward: float = Field(ge=0, le=100000)
    quiz_json: str = "[]"


class MissionBody(StrictBody):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=255)
    mission_type: Literal["daily", "weekly", "one_time", "story", "permanent"]
    target_value: int = Field(ge=0, le=1000000)
    target_type: Literal["trades", "logins", "referrals", "lessons", "custom"]
    reward_xp: int = Field(ge=0, le=100000)
    reward_xr: float = Field(ge=0, le=1000000)
    duration_days: int = Field(ge=0, le=3650)


class ConfirmBody(StrictBody):
    confirm: bool = False
    reason: str = Field(default="", max_length=255)


class AIStrategyBody(StrictBody):
    strategy: Literal["aggressive", "conservative", "balanced", "random"]


class AITierBody(StrictBody):
    tier: Literal[1, 2, 3]


class ToggleBody(StrictBody):
    active: bool | None = None
    is_active: bool | None = None


class HomeNationBody(StrictBody):
    nation_id: int | None = None
    confirm: bool = False


class ProfileBanBody(StrictBody):
    reason: str = Field(default="", max_length=255)


class TreasuryAdjustBody(StrictBody):
    nation_id: int
    currency_type: Literal["usd", "local"]
    adjustment_type: Literal["add", "subtract", "set"]
    amount: float = Field(ge=0, le=1000000000)
    reason: str = Field(min_length=1, max_length=255)
    affect_total_deposited: bool = False
    confirm: bool = False


def _iso(v):
    return v.isoformat() if v else None


def _enum(v):
    return getattr(v, "value", v)


def _page(page: int, limit: int) -> tuple[int, int, int]:
    page = max(1, min(page, 100000))
    limit = max(1, min(limit, 100))
    return page, limit, (page - 1) * limit


def _validate_quiz(value: str) -> str:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="فرمت JSON نامعتبر است") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail="quiz_json باید آرایه باشد")
    for item in data:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="ساختار quiz_json نامعتبر است")
        if not isinstance(item.get("question"), str):
            raise HTTPException(status_code=422, detail="question نامعتبر است")
        if not isinstance(item.get("options"), list) or not item["options"]:
            raise HTTPException(status_code=422, detail="options نامعتبر است")
        try:
            answer = int(item.get("answer"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="answer نامعتبر است") from exc
        if answer < 0 or answer >= len(item["options"]):
            raise HTTPException(status_code=422, detail="answer خارج از محدوده است")
    return json.dumps(data, ensure_ascii=False)


async def _audit(
    db: AsyncSession,
    admin_user: AdminUser,
    action: str,
    rule_key: str,
    *,
    old_value=None,
    new_value=None,
    reason: str | None = None,
) -> None:
    db.add(
        GovernanceLedger(
            actor_player_id=admin_user.user_id,
            action=action,
            rule_key=rule_key,
            old_value=str(old_value)[:64] if old_value is not None else None,
            new_value=str(new_value)[:64] if new_value is not None else None,
            reason=(reason or "")[:255] or None,
        )
    )


# ---------------- Academy ----------------

@router.get("/api/academy/stats")
async def academy_stats(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    total_lessons = int(await db.scalar(select(func.count(Lesson.id))) or 0)
    active_lessons = int(await db.scalar(select(func.count(Lesson.id)).where(Lesson.is_active.is_(True))) or 0)
    xp_distributed = await db.scalar(
        select(func.coalesce(func.sum(Lesson.xp_reward), 0))
        .join(UserLessonProgress, UserLessonProgress.lesson_id == Lesson.id)
        .where(UserLessonProgress.status == "done")
    )
    unique_learners = int(await db.scalar(select(func.count(func.distinct(UserLessonProgress.user_id)))) or 0)
    return {
        "total_lessons": total_lessons,
        "active_lessons": active_lessons,
        "total_xp_distributed": int(xp_distributed or 0),
        "unique_learners": unique_learners,
    }


@router.get("/api/academy/lessons")
async def academy_lessons(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Literal["all", "active", "inactive"] = "all",
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    stmt = select(Lesson).order_by(Lesson.module_id, Lesson.order, Lesson.id)
    if status == "active":
        stmt = stmt.where(Lesson.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Lesson.is_active.is_(False))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(await db.scalar(count_stmt) or 0)
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {
        "items": [
            {
                "lesson_id": r.id,
                "title": r.title_fa,
                "content": r.content_fa,
                "level": r.level,
                "xp_reward": r.xp_reward,
                "xr_reward": float(r.xr_reward or 0),
                "is_active": r.is_active,
                "quiz_json": r.quiz_json,
                "module_id": r.module_id,
                "order": r.order,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.get("/api/academy/lessons/{lesson_id}")
async def academy_lesson(
    lesson_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return {
        "lesson_id": lesson.id,
        "title": lesson.title_fa,
        "content": lesson.content_fa,
        "level": lesson.level,
        "xp_reward": lesson.xp_reward,
        "xr_reward": float(lesson.xr_reward or 0),
        "is_active": lesson.is_active,
        "quiz_json": lesson.quiz_json,
        "module_id": lesson.module_id,
        "order": lesson.order,
    }


@router.post("/api/academy/lessons")
async def create_academy_lesson(
    body: LessonBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    quiz = _validate_quiz(body.quiz_json)
    async with db.begin():
        next_order = int(
            await db.scalar(
                select(func.coalesce(func.max(Lesson.order), 0)).where(Lesson.module_id == 1)
            )
            or 0
        ) + 1
        lesson = Lesson(
            module_id=1,
            order=next_order,
            level=body.level,
            title_fa=body.title,
            content_fa=body.content,
            xp_reward=body.xp_reward,
            xr_reward=Decimal(str(body.xr_reward)),
            quiz_json=quiz,
            is_active=True,
        )
        db.add(lesson)
        await db.flush()
        await _audit(db, admin_user, "admin_academy_create", f"academy.lesson.{lesson.id}", new_value=body.title)
    return {"success": True, "lesson_id": lesson.id}


@router.patch("/api/academy/lessons/{lesson_id}")
async def update_academy_lesson_v2(
    lesson_id: int,
    body: LessonBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    quiz = _validate_quiz(body.quiz_json)
    async with db.begin():
        lesson = await db.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
        if lesson is None:
            raise HTTPException(status_code=404, detail="Lesson not found")
        old = lesson.title_fa
        lesson.title_fa = body.title
        lesson.content_fa = body.content
        lesson.level = body.level
        lesson.xp_reward = body.xp_reward
        lesson.xr_reward = Decimal(str(body.xr_reward))
        lesson.quiz_json = quiz
        await _audit(db, admin_user, "admin_academy_update", f"academy.lesson.{lesson_id}", old_value=old, new_value=body.title)
    return {"success": True}


@router.patch("/api/academy/lessons/{lesson_id}/toggle")
async def toggle_academy_lesson(
    lesson_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        lesson = await db.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
        if lesson is None:
            raise HTTPException(status_code=404, detail="Lesson not found")
        lesson.is_active = not lesson.is_active
        await _audit(db, admin_user, "admin_academy_toggle", f"academy.lesson.{lesson_id}", new_value=lesson.is_active)
    return {"success": True, "is_active": lesson.is_active}


@router.get("/api/academy/progress")
async def academy_progress_v2(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    grouped = (
        select(
            UserLessonProgress.user_id.label("user_id"),
            func.sum(case((UserLessonProgress.status == "done", 1), else_=0)).label("completed_lessons"),
            func.max(UserLessonProgress.completed_at).label("last_activity"),
        )
        .group_by(UserLessonProgress.user_id)
        .subquery()
    )
    stmt = (
        select(User.user_id, User.username, UserXP.total_xp, grouped.c.completed_lessons, grouped.c.last_activity)
        .join(grouped, grouped.c.user_id == User.user_id)
        .outerjoin(UserXP, UserXP.user_id == User.user_id)
        .order_by(desc(grouped.c.last_activity), User.user_id)
    )
    total = int(await db.scalar(select(func.count()).select_from(grouped)) or 0)
    rows = (await db.execute(stmt.offset(offset).limit(limit))).all()
    return {
        "items": [
            {
                "user_id": r.user_id,
                "username": r.username,
                "completed_lessons": int(r.completed_lessons or 0),
                "total_xp": int(r.total_xp or 0),
                "last_activity": _iso(r.last_activity),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


# ---------------- Missions ----------------

@router.get("/api/missions/stats")
async def mission_stats(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    return {
        "total_missions": int(await db.scalar(select(func.count(Mission.id))) or 0),
        "active_missions": int(await db.scalar(select(func.count(Mission.id)).where(Mission.is_active.is_(True))) or 0),
        "total_completions": int(await db.scalar(select(func.count(UserMissionProgress.id)).where(UserMissionProgress.completed.is_(True))) or 0),
        "total_claims": int(await db.scalar(select(func.count(UserMissionProgress.id)).where(UserMissionProgress.claimed.is_(True))) or 0),
    }


@router.get("/api/missions")
async def missions_v2(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Literal["all", "active", "inactive"] = "all",
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    stmt = select(Mission).order_by(Mission.id.desc())
    if status == "active":
        stmt = stmt.where(Mission.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Mission.is_active.is_(False))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(await db.scalar(count_stmt) or 0)
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {
        "items": [
            {
                "mission_id": m.id,
                "title": m.title_fa,
                "description": m.description_fa,
                "type": m.mission_type,
                "target_value": m.target_count,
                "target_type": m.target_type,
                "reward_xp": int(m.reward_xp or 0),
                "reward_xr": float(m.reward_xr or 0),
                "reward_currency": float(m.reward_currency or 0),
                "duration_days": m.duration_days,
                "is_active": m.is_active,
            }
            for m in rows
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.get("/api/missions/{mission_id}")
async def mission_detail(
    mission_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    m = await db.get(Mission, mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {
        "mission_id": m.id,
        "title": m.title_fa,
        "description": m.description_fa,
        "type": m.mission_type,
        "target_value": m.target_count,
        "target_type": m.target_type,
        "reward_xp": int(m.reward_xp or 0),
        "reward_xr": float(m.reward_xr or 0),
        "reward_currency": float(m.reward_currency or 0),
        "duration_days": m.duration_days,
        "is_active": m.is_active,
    }


@router.post("/api/missions")
async def create_mission_v2(
    body: MissionBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        key = f"admin_{int(datetime.now(UTC).timestamp())}_{body.title[:18].lower().replace(' ', '_')}"
        while await db.scalar(select(Mission.id).where(Mission.key == key)):
            key += "x"
        m = Mission(
            key=key,
            title_fa=body.title,
            description_fa=body.description,
            mission_type=body.mission_type,
            target_count=body.target_value,
            target_type=body.target_type,
            duration_days=body.duration_days,
            reward_xr=Decimal(str(body.reward_xr)),
            reward_currency=Decimal(str(body.reward_xr)),
            is_active=True,
        )
        db.add(m)
        await db.flush()
        await _audit(db, admin_user, "admin_mission_create", f"mission.{m.id}", new_value=body.title)
    return {"success": True, "mission_id": m.id}


@router.patch("/api/missions/{mission_id}")
async def update_mission_v2(
    mission_id: int,
    body: MissionBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        m = await db.scalar(select(Mission).where(Mission.id == mission_id).with_for_update())
        if not m:
            raise HTTPException(status_code=404, detail="Mission not found")
        old = m.title_fa
        m.title_fa = body.title
        m.description_fa = body.description
        m.mission_type = body.mission_type
        m.target_count = body.target_value
        m.target_type = body.target_type
        m.duration_days = body.duration_days
        m.reward_xp = body.reward_xp
        m.reward_xr = Decimal(str(body.reward_xr))
        m.reward_currency = Decimal("0")
        await _audit(db, admin_user, "admin_mission_update", f"mission.{mission_id}", old_value=old, new_value=body.title)
    return {"success": True}


@router.patch("/api/missions/{mission_id}/toggle")
async def toggle_mission(
    mission_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        m = await db.scalar(select(Mission).where(Mission.id == mission_id).with_for_update())
        if not m:
            raise HTTPException(status_code=404, detail="Mission not found")
        m.is_active = not m.is_active
        await _audit(db, admin_user, "admin_mission_toggle", f"mission.{mission_id}", new_value=m.is_active)
    return {"success": True, "is_active": m.is_active}


@router.get("/api/missions/{mission_id}/stats")
async def mission_instance_stats(
    mission_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    started = int(await db.scalar(select(func.count(UserMissionProgress.id)).where(UserMissionProgress.mission_id == mission_id)) or 0)
    completed = int(await db.scalar(select(func.count(UserMissionProgress.id)).where(UserMissionProgress.mission_id == mission_id, UserMissionProgress.completed.is_(True))) or 0)
    claimed = int(await db.scalar(select(func.count(UserMissionProgress.id)).where(UserMissionProgress.mission_id == mission_id, UserMissionProgress.claimed.is_(True))) or 0)
    return {
        "total_started": started,
        "total_completed": completed,
        "total_claimed": claimed,
        "completion_rate": round(completed / started * 100, 1) if started else 0,
    }


# ---------------- Founding ----------------

def _founding_step(draft: NationFoundingDraft) -> int:
    if not draft.group_id:
        return 1
    if not draft.nation_name:
        return 2
    if not draft.flag_emoji or draft.flag_emoji == "🏴":
        return 3
    if not draft.currency_code:
        return 4
    return 5


@router.get("/api/founding/stats")
async def founding_stats(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = await db.execute(select(NationFoundingDraft.status, func.count()).group_by(NationFoundingDraft.status))
    counts = {str(k): int(v) for k, v in rows.all()}
    return {
        "active_drafts": sum(counts.get(k, 0) for k in ("WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG", "REVIEW", "FINALIZING", "DRAFT")),
        "completed_drafts": counts.get("COMPLETED", 0),
        "expired_drafts": counts.get("EXPIRED", 0),
        "cancelled_drafts": counts.get("CANCELLED", 0),
    }


@router.get("/api/founding/drafts")
async def founding_drafts_v2(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str = "all",
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    stmt = select(NationFoundingDraft, User.username).join(User, User.user_id == NationFoundingDraft.founder_user_id)
    if status != "all":
        if status == "DRAFT":
            stmt = stmt.where(NationFoundingDraft.status.in_(("WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG")))
        else:
            stmt = stmt.where(NationFoundingDraft.status == status)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.execute(stmt.order_by(NationFoundingDraft.updated_at.desc()).offset(offset).limit(limit))).all()
    return {
        "items": [
            {
                "draft_id": d.id,
                "founder_user_id": d.founder_user_id,
                "founder_username": username,
                "nation_name": d.nation_name,
                "currency_name": d.currency_code,
                "flag_emoji": d.flag_emoji,
                "current_step": _founding_step(d),
                "status": d.status if d.status not in {"WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG"} else "DRAFT",
                "connected_group_id": d.group_id,
                "created_at": _iso(d.created_at),
                "updated_at": _iso(d.updated_at),
            }
            for d, username in rows
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.get("/api/founding/drafts/{draft_id}")
async def founding_detail(
    draft_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    row = await db.execute(
        select(NationFoundingDraft, User.username)
        .join(User, User.user_id == NationFoundingDraft.founder_user_id)
        .where(NationFoundingDraft.id == draft_id)
    )
    r = row.one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Draft not found")
    d, username = r
    return {
        "draft_id": d.id,
        "founder_user_id": d.founder_user_id,
        "founder_username": username,
        "nation_name": d.nation_name,
        "currency_name": d.currency_code,
        "flag_emoji": d.flag_emoji,
        "current_step": _founding_step(d),
        "status": d.status if d.status not in {"WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG"} else "DRAFT",
        "connected_group_id": d.group_id,
        "created_at": _iso(d.created_at),
        "updated_at": _iso(d.updated_at),
    }


@router.post("/api/founding/drafts/{draft_id}/cancel")
async def founding_cancel(
    draft_id: int,
    body: ConfirmBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        d = await db.scalar(select(NationFoundingDraft).where(NationFoundingDraft.id == draft_id).with_for_update())
        if not d:
            raise HTTPException(status_code=404, detail="Draft not found")
        if d.status not in {"WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG", "REVIEW"}:
            raise HTTPException(status_code=409, detail="Draft is not cancellable")
        preview = {
            "draft_id": d.id,
            "nation_name": d.nation_name,
            "status": d.status,
            "reason": body.reason,
        }
        if not body.confirm:
            return {"preview": preview}
        d.status = "CANCELLED"
        await _audit(db, admin_user, "admin_founding_cancel", f"founding.{draft_id}", old_value=preview["status"], new_value="CANCELLED", reason=body.reason)
    return {"success": True, "message": "Draft لغو شد"}


@router.post("/api/founding/drafts/{draft_id}/finalize")
async def founding_finalize(
    draft_id: int,
    body: ConfirmBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    founder_id = await db.scalar(select(NationFoundingDraft.founder_user_id).where(NationFoundingDraft.id == draft_id))
    if founder_id is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    d = await db.get(NationFoundingDraft, draft_id)
    if not d or d.status != "REVIEW":
        raise HTTPException(status_code=409, detail="Draft باید در وضعیت REVIEW باشد")
    preview = {
        "draft_id": d.id,
        "nation_name": d.nation_name,
        "founder_user_id": d.founder_user_id,
        "founder_username": None,
        "creates": "nation + founder membership + initial holdings",
    }
    if not body.confirm:
        username = await db.scalar(select(User.username).where(User.user_id == founder_id))
        preview["founder_username"] = username
        return {"preview": preview}
    await db.rollback()
    try:
        nation, group_id = await finalize_draft(db, founder_user_id=int(founder_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with db.begin():
        await _audit(
            db,
            admin_user,
            "admin_founding_finalize",
            f"founding.{draft_id}",
            new_value=f"COMPLETED:nation={nation.nation_id}",
            reason="Finalized from admin panel",
        )
    return {"success": True, "nation_id": nation.nation_id, "group_id": group_id}


# ---------------- AI ----------------

async def _ai_usage_today(db: AsyncSession) -> tuple[int, int]:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(AIUsageLog.advisor_questions_used), 0),
                func.coalesce(func.sum(AIUsageLog.portfolio_scans_used), 0),
                func.coalesce(func.sum(AIUsageLog.war_analysis_used), 0),
            ).where(AIUsageLog.date == date.today())
        )
    ).one()
    return int(row[0]), int(row[1]) + int(row[2])


@router.get("/api/ai/stats")
async def ai_stats(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    requests, extra = await _ai_usage_today(db)
    ai_players = int(await db.scalar(select(func.count(User.user_id)).where(User.is_ai.is_(True))) or 0)
    ai_nations = int(await db.scalar(select(func.count(Nation.nation_id)).where(Nation.is_ai.is_(True), Nation.is_active.is_(True))) or 0)
    trades_today = int(
        await db.scalar(
            select(func.count(UserActivity.id))
            .join(User, User.user_id == UserActivity.user_id)
            .where(User.is_ai.is_(True), UserActivity.activity_type == ActivityType.TRADE, UserActivity.created_at >= today)
        )
        or 0
    )
    active_ai = int(
        await db.scalar(
            select(func.count(func.distinct(UserActivity.user_id)))
            .join(User, User.user_id == UserActivity.user_id)
            .where(User.is_ai.is_(True), User.deleted_at.is_(None), UserActivity.created_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24))
        )
        or 0
    )
    health = await companion.health_check()
    return {
        "ai_players": ai_players,
        "ai_nations": ai_nations,
        "ai_trades_today": trades_today,
        "gemini_requests_today": requests + extra,
        "gemini_tokens_today": None,
        "active_ai_players": active_ai,
        "gemini_model": __import__("config").settings.ai_model,
        "gemini_status": "online" if health else "offline",
    }


@router.get("/api/ai/gemini-health")
async def ai_gemini_health(admin_user: AdminUser = Depends(get_admin_user)):
    checked = datetime.now(UTC)
    ok = await companion.health_check()
    try:
        from config import settings
        model = settings.ai_model
    except Exception:
        model = None
    return {
        "status": "online" if ok else "offline",
        "model": model,
        "last_checked_at": checked.isoformat(),
    }


@router.get("/api/ai/players")
async def ai_players(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    trade_max = (
        select(UserActivity.user_id, func.max(UserActivity.created_at).label("last_trade_at"))
        .where(UserActivity.activity_type == ActivityType.TRADE)
        .group_by(UserActivity.user_id)
        .subquery()
    )
    stmt = (
        select(User, Nation.name.label("nation_name"), trade_max.c.last_trade_at)
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .outerjoin(trade_max, trade_max.c.user_id == User.user_id)
        .where(User.is_ai.is_(True))
        .order_by(User.user_id)
    )
    total = int(await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    rows = (await db.execute(stmt.offset(offset).limit(limit))).all()
    return {
        "players": [
            {
                "user_id": u.user_id,
                "username": u.username,
                "nation_name": nation_name,
                "strategy": u.ai_strategy,
                "tier": { "bronze": 1, "silver": 2, "gold": 3, "diamond": 3, "elite": 3 }.get(_enum(u.ai_tier), 3),
                "is_active": u.deleted_at is None,
                "last_trade_at": _iso(last_trade_at),
            }
            for u, nation_name, last_trade_at in rows
        ],
        "total": total,
        "pages": (total + limit - 1) // limit if total else 1,
    }


@router.patch("/api/ai/players/{user_id}/strategy")
async def update_ai_strategy(user_id: int, body: AIStrategyBody, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id, User.is_ai.is_(True)).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="AI player not found")
        old = u.ai_strategy
        u.ai_strategy = body.strategy
        await _audit(db, admin_user, "admin_ai_strategy", f"ai.player.{user_id}", old_value=old, new_value=body.strategy)
    return {"success": True, "strategy": body.strategy}


@router.patch("/api/ai/players/{user_id}/tier")
async def update_ai_tier(user_id: int, body: AITierBody, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    from app.database.models import AITier
    mapping = {1: AITier.BRONZE, 2: AITier.SILVER, 3: AITier.GOLD}
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id, User.is_ai.is_(True)).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="AI player not found")
        u.ai_tier = mapping[body.tier]
        await _audit(db, admin_user, "admin_ai_tier", f"ai.player.{user_id}", new_value=body.tier)
    return {"success": True, "tier": body.tier}


@router.patch("/api/ai/players/{user_id}/toggle")
async def toggle_ai_player(user_id: int, body: ToggleBody, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id, User.is_ai.is_(True)).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="AI player not found")
        active = body.active if body.active is not None else body.is_active
        if active is None:
            active = u.deleted_at is not None
        u.deleted_at = None if active else datetime.now(UTC)
        await _audit(db, admin_user, "admin_ai_toggle", f"ai.player.{user_id}", new_value=active)
    return {"success": True, "is_active": active}


@router.get("/api/ai/scheduler-status")
async def ai_scheduler_status(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    job_id = "ai_world_5m"
    state = await redis.hgetall(f"opex:scheduler:job:{job_id}") if redis else {}
    return {
        "is_running": not bool(state.get("paused")) and state.get("status") not in {"error", "missed"},
        "current_cycle": job_id,
        "last_cycle_at": state.get("last_run"),
        "next_cycle_at": state.get("next_run"),
        "cycle_duration_seconds": 300,
    }


async def _queue_scheduler_command(redis, job_id: str, command: str):
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await redis.rpush("opex:admin:scheduler:commands", json.dumps({"job_id": job_id, "command": command}))


@router.post("/api/ai/scheduler/pause")
async def ai_scheduler_pause(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    await _queue_scheduler_command(redis, "ai_world_5m", "pause")
    return {"success": True}


@router.post("/api/ai/scheduler/resume")
async def ai_scheduler_resume(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    await _queue_scheduler_command(redis, "ai_world_5m", "resume")
    return {"success": True}


@router.post("/api/ai/scheduler/run-now")
async def ai_scheduler_run_now(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    await _queue_scheduler_command(redis, "ai_world_5m", "run_now")
    return {"success": True}


# ---------------- Leaderboard ----------------

async def _wealth_value_subquery():
    return (
        select(
            CurrencyHolding.user_id.label("user_id"),
            func.coalesce(func.sum(CurrencyHolding.amount * Nation.exchange_rate), 0).label("local_value"),
        )
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(Nation.is_active.is_(True))
        .group_by(CurrencyHolding.user_id)
        .subquery()
    )


@router.get("/api/leaderboard/nations")
async def leaderboard_nations(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    rows = (
        await db.execute(
            select(Nation.nation_id, Nation.name, Nation.flag_emoji, Nation.member_count, Nation.treasury, Nation.exchange_rate)
            .where(Nation.is_active.is_(True), Nation.deleted_at.is_(None))
            .order_by(Nation.member_count.desc(), Nation.treasury.desc(), Nation.nation_id)
            .limit(50)
        )
    ).all()
    out = []
    for idx, r in enumerate(rows, 1):
        score = float(Decimal(str(r.member_count or 0)) * Decimal("1") + Decimal(str(r.treasury or 0)) * Decimal("0.01") + Decimal(str(r.exchange_rate or 0)) * Decimal("10"))
        out.append({
            "rank": idx,
            "nation_id": r.nation_id,
            "name": r.name,
            "flag_emoji": r.flag_emoji,
            "score": round(score, 2),
            "member_count": r.member_count,
            "treasury": float(r.treasury or 0),
        })
    return {"nations": out, "updated_at": datetime.now(UTC).isoformat()}


@router.get("/api/leaderboard/wealth")
async def leaderboard_wealth(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    holdings = await _wealth_value_subquery()
    rows = (
        await db.execute(
            select(User.user_id, User.username, User.balance, User.xr_balance, User.is_ai, Nation.name.label("nation_name"), holdings.c.local_value)
            .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
            .outerjoin(holdings, holdings.c.user_id == User.user_id)
            .where(User.deleted_at.is_(None))
            .order_by((User.xr_balance + func.coalesce(holdings.c.local_value, 0)).desc(), User.user_id)
            .limit(50)
        )
    ).all()
    return {
        "players": [
            {
                "rank": i + 1,
                "user_id": r.user_id,
                "username": r.username,
                "nation_name": r.nation_name,
                "balance": float((r.xr_balance or 0) + (r.local_value or 0)),
                "is_ai": r.is_ai,
            }
            for i, r in enumerate(rows)
        ],
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/api/leaderboard/traders")
async def leaderboard_traders(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    stats = (
        select(
            Transaction.user_id.label("user_id"),
            func.count(Transaction.id).label("trade_count"),
            func.coalesce(func.sum(Transaction.spend_xr), 0).label("trade_volume"),
        )
        .where(Transaction.created_at >= since)
        .group_by(Transaction.user_id)
        .subquery()
    )
    profits = (
        select(
            DecisionSnapshot.user_id.label("user_id"),
            func.coalesce(func.sum(DecisionSnapshot.actual_outcome), 0).label("profit"),
        )
        .where(DecisionSnapshot.is_evaluated.is_(True))
        .group_by(DecisionSnapshot.user_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(User.user_id, User.username, Nation.name.label("nation_name"), stats.c.trade_count, stats.c.trade_volume, profits.c.profit)
            .join(stats, stats.c.user_id == User.user_id)
            .outerjoin(profits, profits.c.user_id == User.user_id)
            .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
            .where(User.deleted_at.is_(None))
            .order_by(stats.c.trade_count.desc(), stats.c.trade_volume.desc())
            .limit(50)
        )
    ).all()
    return {
        "players": [
            {
                "rank": i + 1,
                "user_id": r.user_id,
                "username": r.username,
                "nation_name": r.nation_name,
                "trade_count": int(r.trade_count or 0),
                "trade_volume": float(r.trade_volume or 0),
                "profit": float(r.profit or 0),
            }
            for i, r in enumerate(rows)
        ],
        "updated_at": datetime.now(UTC).isoformat(),
    }


# ---------------- Profiles ----------------

async def _profile_row(db: AsyncSession, user_id: int):
    row = await db.execute(
        select(
            User,
            Nation.name.label("home_nation_name"),
            NationMembership.nation_id.label("active_membership_nation_id"),
            select(Nation.name)
            .where(Nation.nation_id == NationMembership.nation_id)
            .scalar_subquery()
            .label("active_membership_nation_name"),
        )
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .outerjoin(
            NationMembership,
            (NationMembership.user_id == User.user_id) & NationMembership.is_active.is_(True),
        )
        .where(User.user_id == user_id)
    )
    return row.first()


@router.get("/api/profiles/search")
async def profile_search(q: str = "", db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    q = q.strip()
    stmt = select(User).order_by(User.user_id)
    if q:
        conditions = [User.username.ilike(f"%{q}%")]
        if q.isdigit():
            conditions.append(User.user_id == int(q))
        stmt = stmt.where(or_(*conditions))
    rows = (await db.execute(stmt.limit(100))).scalars().all()
    out = []
    for u in rows:
        active = await db.scalar(
            select(NationMembership.nation_id).where(
                NationMembership.user_id == u.user_id,
                NationMembership.is_active.is_(True),
            ).limit(1)
        )
        out.append({
            "user_id": u.user_id,
            "username": u.username,
            "role": u.role,
            "home_nation_id": u.home_nation_id,
            "active_membership_nation_id": active,
            "is_banned": u.deleted_at is not None,
            "context_mismatch": u.home_nation_id != active,
        })
    return {"users": out}


@router.get("/api/profiles/{user_id}")
async def profile_detail(user_id: int, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    active = await db.scalar(
        select(NationMembership).where(NationMembership.user_id == user_id, NationMembership.is_active.is_(True)).limit(1)
    )
    home_name = await db.scalar(select(Nation.name).where(Nation.nation_id == u.home_nation_id)) if u.home_nation_id else None
    active_name = await db.scalar(select(Nation.name).where(Nation.nation_id == active.nation_id)) if active else None
    last_active = await db.scalar(select(func.max(UserActivity.created_at)).where(UserActivity.user_id == user_id))
    mismatch = u.home_nation_id != (active.nation_id if active else None)
    details = None
    if mismatch:
        details = f"home_nation_id={u.home_nation_id}, active_membership_nation_id={active.nation_id if active else None}"
    joined_at = active.joined_at if active else u.created_at
    return {
        "user_id": u.user_id,
        "username": u.username,
        "role": u.role,
        "home_nation_id": u.home_nation_id,
        "home_nation_name": home_name,
        "active_membership_nation_id": active.nation_id if active else None,
        "active_membership_nation_name": active_name,
        "is_banned": u.deleted_at is not None,
        "ban_reason": u.ban_reason,
        "joined_at": _iso(joined_at),
        "last_active": _iso(last_active),
        "context_mismatch": mismatch,
        "mismatch_details": details,
    }


@router.post("/api/profiles/{user_id}/ban")
async def profile_ban(user_id: int, body: ProfileBanBody, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.deleted_at = datetime.now(UTC)
        u.ban_reason = body.reason or "مسدودسازی توسط مدیر"
        await _audit(db, admin_user, "admin_profile_ban", f"profile.{user_id}", reason=u.ban_reason)
    return {"success": True}


@router.post("/api/profiles/{user_id}/unban")
async def profile_unban(user_id: int, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.deleted_at = None
        u.ban_reason = None
        await _audit(db, admin_user, "admin_profile_unban", f"profile.{user_id}")
    return {"success": True}


@router.patch("/api/profiles/{user_id}/home-nation")
async def profile_home_nation(user_id: int, body: HomeNationBody, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    if not body.confirm:
        return {
            "preview": True,
            "user_id": user_id,
            "new_home_nation_id": body.nation_id,
            "message": "برای اجرا confirm=true ارسال شود",
        }
    async with db.begin():
        u = await db.scalar(select(User).where(User.user_id == user_id).with_for_update())
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        if body.nation_id is not None:
            nation = await db.scalar(select(Nation).where(Nation.nation_id == body.nation_id, Nation.is_active.is_(True)))
            if not nation:
                raise HTTPException(status_code=404, detail="Nation not found")
        old = u.home_nation_id
        u.home_nation_id = body.nation_id
        await _audit(db, admin_user, "admin_profile_home_nation", f"profile.{user_id}", old_value=old, new_value=body.nation_id)
    return {"success": True, "home_nation_id": body.nation_id}


# ---------------- Onboarding ----------------

@router.get("/api/onboarding/funnel")
async def onboarding_funnel(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    total_started = int(await db.scalar(select(func.count(User.user_id))) or 0)
    registered = int(await db.scalar(select(func.count(User.user_id)).where(User.deleted_at.is_(None), User.username != "")) or 0)
    assets = int(await db.scalar(select(func.count(func.distinct(CurrencyHolding.user_id))).join(User, User.user_id == CurrencyHolding.user_id).where(User.deleted_at.is_(None))) or 0)
    first_trade = int(await db.scalar(select(func.count(func.distinct(Transaction.user_id))).join(User, User.user_id == Transaction.user_id).where(User.deleted_at.is_(None))) or 0)
    ai_count = int(await db.scalar(select(func.count(User.user_id)).where(User.is_ai.is_(True), User.deleted_at.is_(None))) or 0)
    real_count = int(await db.scalar(select(func.count(User.user_id)).where(User.is_ai.is_(False), User.deleted_at.is_(None))) or 0)
    return {
        "total_started": total_started,
        "registered_username": registered,
        "assets_created": assets,
        "first_trade": first_trade,
        "conversion_rates": {
            "to_username": round(registered / total_started * 100, 1) if total_started else 0,
            "to_assets": round(assets / registered * 100, 1) if registered else 0,
            "to_trade": round(first_trade / assets * 100, 1) if assets else 0,
        },
        "ai_count": ai_count,
        "real_player_count": real_count,
    }


@router.get("/api/onboarding/active-accounts")
async def onboarding_active_accounts(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    now = datetime.now(UTC).replace(tzinfo=None)
    values = {}
    for key, delta in (("active_today", timedelta(days=1)), ("active_week", timedelta(days=7)), ("active_month", timedelta(days=30))):
        values[key] = int(await db.scalar(select(func.count(func.distinct(UserActivity.user_id))).where(UserActivity.created_at >= now - delta)) or 0)
    return values


# ---------------- Scheduler ----------------

async def _scheduler_rows(redis):
    result = []
    for job_id, (schedule, description) in SCHEDULER_JOB_META.items():
        state = await redis.hgetall(f"opex:scheduler:job:{job_id}") if redis else {}
        result.append({
            "job_id": job_id,
            "name": job_id,
            "description": description,
            "last_run_at": state.get("last_run"),
            "next_run_at": state.get("next_run"),
            "status": "paused" if state.get("paused") == "1" else ("error" if state.get("status") == "error" else ("running" if state.get("status") == "queued" else "idle")),
            "last_error": state.get("last_error"),
            "is_paused": state.get("paused") == "1",
        })
    return result


@router.get("/api/scheduler/jobs")
async def scheduler_jobs(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    return {"jobs": await _scheduler_rows(redis)}


@router.post("/api/scheduler/jobs/{job_id}/{action}")
async def scheduler_job_action(job_id: str, action: Literal["run", "pause", "resume"], redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    if job_id not in SCHEDULER_JOB_META:
        raise HTTPException(status_code=404, detail="Unknown scheduler job")
    command = {"run": "run_now", "pause": "pause", "resume": "resume"}[action]
    await _queue_scheduler_command(redis, job_id, command)
    return {"success": True, "action": action}


# ---------------- Support ----------------

async def _redis_list(redis, prefix: str):
    values = []
    if not redis:
        return values
    async for key in redis.scan_iter(match=prefix + "*", count=100):
        values.append(key)
    return values


@router.get("/api/support/stats")
async def support_stats(redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    error_keys = await _redis_list(redis, "opex:support:errors:")
    event_keys = await _redis_list(redis, "opex:support:events:")
    recent_errors = sum(int(await redis.llen(k) or 0) for k in error_keys) if redis else 0
    recent_events = sum(int(await redis.llen(k) or 0) for k in event_keys) if redis else 0
    return {
        "error_keys_count": len(error_keys),
        "event_keys_count": len(event_keys),
        "recent_errors_count": recent_errors,
        "recent_events_count": recent_events,
    }


@router.get("/api/support/telemetry/{user_id}")
async def support_user_telemetry(user_id: int, db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    from app.diagnostics.support_telemetry import get_recent_telemetry
    data = await get_recent_telemetry(user_id)
    username = await db.scalar(select(User.username).where(User.user_id == user_id))
    return {"user_id": user_id, "username": username, "errors": data["errors"], "events": data["events"]}


@router.get("/api/support/recent-errors")
async def support_recent_errors(limit: int = Query(20, ge=1, le=100), redis=Depends(get_redis), admin_user: AdminUser = Depends(get_admin_user)):
    rows = []
    if redis:
        keys = await _redis_list(redis, "opex:support:errors:")
        for key in keys:
            raw = await redis.lrange(key, -20, -1)
            for item in raw:
                try:
                    rows.append(json.loads(item))
                except Exception:
                    continue
    rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"errors": rows[:limit]}


# ---------------- Treasury ----------------

@router.get("/api/treasury/overview")
async def treasury_overview(db: AsyncSession = Depends(get_db_session), admin_user: AdminUser = Depends(get_admin_user)):
    rows = (
        await db.execute(
            select(Nation, NationTreasury)
            .outerjoin(NationTreasury, NationTreasury.nation_id == Nation.nation_id)
            .where(Nation.is_active.is_(True), Nation.deleted_at.is_(None))
            .order_by(Nation.name)
        )
    ).all()
    return {
        "nations": [
            {
                "nation_id": n.nation_id,
                "name": n.name,
                "flag_emoji": n.flag_emoji,
                "usd_balance": float((t.balance_xr if t else n.treasury) or 0),
                "local_balance": float((t.balance_local if t else 0) or 0),
                "total_deposited": float((t.total_deposited if t else 0) or 0),
                "last_deposit_at": _iso(t.last_deposit_at if t else None),
            }
            for n, t in rows
        ]
    }


@router.post("/api/treasury/adjust")
async def treasury_adjust_v2(
    body: TreasuryAdjustBody,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        nation = await db.scalar(select(Nation).where(Nation.nation_id == body.nation_id, Nation.is_active.is_(True)).with_for_update())
        if not nation:
            raise HTTPException(status_code=404, detail="Nation not found")
        treasury = await db.scalar(select(NationTreasury).where(NationTreasury.nation_id == body.nation_id).with_for_update())
        if treasury is None:
            treasury = NationTreasury(nation_id=body.nation_id, balance_xr=0, balance_local=0, total_deposited=0)
            db.add(treasury)
            await db.flush()
        current = Decimal(str(treasury.balance_xr if body.currency_type == "usd" else treasury.balance_local))
        amount = Decimal(str(body.amount))
        if body.adjustment_type == "add":
            new_balance = current + amount
            change = amount
        elif body.adjustment_type == "subtract":
            new_balance = current - amount
            change = -amount
        else:
            new_balance = amount
            change = amount - current
        if new_balance < 0:
            raise HTTPException(status_code=422, detail="موجودی نمی‌تواند منفی شود")
        preview = {
            "current_balance": float(current),
            "new_balance": float(new_balance),
            "change": float(change),
            "affect_total_deposited": body.affect_total_deposited,
        }
        if not body.confirm:
            return {"preview": preview}
        if body.currency_type == "usd":
            treasury.balance_xr = new_balance
            nation.treasury = new_balance
            if body.affect_total_deposited:
                treasury.total_deposited = Decimal(str(treasury.total_deposited or 0)) + change
        else:
            treasury.balance_local = new_balance
        db.add(
            TreasuryLog(
                nation_id=body.nation_id,
                actor_id=admin_user.user_id,
                action=f"ADMIN_{body.currency_type.upper()}_{body.adjustment_type.upper()}",
                amount_xr=abs(change),
                note=body.reason,
            )
        )
        await _audit(db, admin_user, "admin_treasury_adjust", f"treasury.{body.nation_id}", old_value=current, new_value=new_balance, reason=body.reason)
    return {"success": True, "message": "خزانه اصلاح شد", "preview": preview}


@router.get("/api/treasury/logs")
async def treasury_logs_v2(
    nation_id: int | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    from_date: str | None = None,
    to_date: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    page, limit, offset = _page(page, limit)
    stmt = select(TreasuryLog, Nation.name).join(Nation, Nation.nation_id == TreasuryLog.nation_id)
    if nation_id:
        stmt = stmt.where(TreasuryLog.nation_id == nation_id)
    if from_date:
        stmt = stmt.where(TreasuryLog.created_at >= datetime.fromisoformat(from_date))
    if to_date:
        stmt = stmt.where(TreasuryLog.created_at <= datetime.fromisoformat(to_date))
    total = int(await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    rows = (await db.execute(stmt.order_by(TreasuryLog.created_at.desc()).offset(offset).limit(limit))).all()
    return {
        "items": [
            {
                "created_at": _iso(l.created_at),
                "nation_name": nation,
                "action": l.action,
                "amount": float(l.amount_xr or 0),
                "reason": l.note,
                "actor_username": l.actor.username if l.actor else None,
            }
            for l, nation in rows
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit if total else 1,
    }
