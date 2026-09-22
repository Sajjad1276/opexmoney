from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user
from admin.dependencies import get_db_session, get_redis
from config import settings
from ai import companion
from app.database.models import (
    AITier,
    ActivityType,
    AIUsageLog,
    BehaviorSnapshot,
    CurrencyHolding,
    GovernanceLedger,
    Lesson,
    Mission,
    Nation,
    NationFoundingDraft,
    NationMembership,
    NationTreasury,
    PriceAlert,
    TreasuryLog,
    User,
    UserActivity,
    UserLessonProgress,
    UserMissionProgress,
    UserXP,
)
from app.schedulers.admin_control import SCHEDULER_JOB_META
from app.diagnostics.support_telemetry import get_recent_telemetry
from app.services.nation.founder_service import finalize_draft

router = APIRouter(prefix="/api/control", tags=["admin-control"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LessonCreate(StrictBody):
    module_id: int = Field(ge=1, le=1000)
    order: int = Field(ge=1, le=10000)
    level: str = Field(min_length=1, max_length=10)
    title_fa: str = Field(min_length=1, max_length=100)
    content_fa: str = Field(min_length=1, max_length=4000)
    quiz_json: str = "[]"
    xp_reward: int = Field(default=10, ge=0, le=100000)
    xr_reward: float = Field(default=0, ge=0, le=100000)
    is_active: bool = True


class LessonUpdate(StrictBody):
    title_fa: str | None = Field(default=None, min_length=1, max_length=100)
    content_fa: str | None = Field(default=None, min_length=1, max_length=4000)
    xp_reward: int | None = Field(default=None, ge=0, le=100000)
    xr_reward: float | None = Field(default=None, ge=0, le=100000)
    level: str | None = Field(default=None, min_length=1, max_length=10)
    quiz_json: str | None = Field(default=None, max_length=20000)
    is_active: bool | None = None


class MissionUpsert(StrictBody):
    key: str = Field(min_length=1, max_length=50)
    title_fa: str = Field(min_length=1, max_length=100)
    description_fa: str = Field(min_length=1, max_length=255)
    mission_type: Literal["daily", "weekly", "permanent"]
    target_count: int = Field(ge=0, le=1000000)
    reward_xr: float = Field(ge=0, le=1000000)
    reward_currency: float = Field(ge=0, le=1000000)
    is_active: bool = True


class ToggleBody(StrictBody):
    is_active: bool


class AIUserUpdate(StrictBody):
    is_ai: bool
    ai_strategy: str = Field(min_length=1, max_length=20)
    ai_tier: Literal["bronze", "silver", "gold", "diamond", "elite"] = "bronze"


class TreasuryAdjust(StrictBody):
    amount_xr: float = Field(gt=-1000000000, lt=1000000000)
    note: str = Field(min_length=1, max_length=255)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _validate_quiz_json(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="quiz_json must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=422, detail="quiz_json must be a JSON array")
    for index, question in enumerate(parsed):
        if not isinstance(question, dict):
            raise HTTPException(status_code=422, detail=f"quiz_json[{index}] must be an object")
        options = question.get("options")
        answer = question.get("answer")
        if not isinstance(question.get("q"), str) or not isinstance(options, list) or not options:
            raise HTTPException(status_code=422, detail=f"quiz_json[{index}] has invalid question/options")
        try:
            answer_index = int(answer)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"quiz_json[{index}] has invalid answer") from exc
        if answer_index < 0 or answer_index >= len(options):
            raise HTTPException(status_code=422, detail=f"quiz_json[{index}] answer is out of range")
        if not all(isinstance(option, str) for option in options):
            raise HTTPException(status_code=422, detail=f"quiz_json[{index}] options must be strings")
    return value



async def _audit(
    session: AsyncSession,
    admin_user: AdminUser,
    *,
    action: str,
    rule_key: str,
    old_value: object | None = None,
    new_value: object | None = None,
    reason: str | None = None,
) -> None:
    session.add(
        GovernanceLedger(
            at=datetime.now(UTC),
            actor_player_id=admin_user.user_id,
            action=action,
            rule_key=rule_key,
            old_value=str(old_value)[:64] if old_value is not None else None,
            new_value=str(new_value)[:64] if new_value is not None else None,
            reason=str(reason)[:255] if reason is not None else None,
        )
    )


@router.get("/academy/summary")
async def academy_summary(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    total_lessons = int(await db.scalar(select(func.count(Lesson.id))) or 0)
    active_lessons = int(await db.scalar(select(func.count(Lesson.id)).where(Lesson.is_active.is_(True))) or 0)
    total_progress = int(await db.scalar(select(func.count(UserLessonProgress.id))) or 0)
    completed = int(
        await db.scalar(
            select(func.count(UserLessonProgress.id)).where(UserLessonProgress.status == "done")
        )
        or 0
    )
    xp_users = int(await db.scalar(select(func.count(UserXP.user_id))) or 0)
    ai_questions = int(await db.scalar(select(func.coalesce(func.sum(UserLessonProgress.ai_questions_count), 0))) or 0)
    return {
        "total_lessons": total_lessons,
        "active_lessons": active_lessons,
        "total_progress": total_progress,
        "completed_lessons": completed,
        "xp_users": xp_users,
        "ai_questions": ai_questions,
    }


@router.get("/academy/lessons")
async def academy_lessons(
    module_id: int | None = Query(default=None, ge=1),
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    stmt = select(Lesson).order_by(Lesson.module_id.asc(), Lesson.order.asc(), Lesson.id.asc())
    if module_id is not None:
        stmt = stmt.where(Lesson.module_id == module_id)
    if active_only:
        stmt = stmt.where(Lesson.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    progress = await db.execute(
        select(UserLessonProgress.lesson_id, func.count(UserLessonProgress.id))
        .where(UserLessonProgress.status == "done")
        .group_by(UserLessonProgress.lesson_id)
    )
    done_by_lesson = {int(k): int(v) for k, v in progress.all()}
    return [
        {
            "id": int(row.id),
            "module_id": int(row.module_id),
            "order": int(row.order),
            "level": row.level,
            "title_fa": row.title_fa,
            "content_fa": row.content_fa,
            "quiz_json": row.quiz_json,
            "xp_reward": int(row.xp_reward),
            "xr_reward": float(row.xr_reward or 0),
            "is_active": bool(row.is_active),
            "completed_count": done_by_lesson.get(int(row.id), 0),
        }
        for row in rows
    ]


@router.post("/academy/lessons", status_code=201)
async def create_academy_lesson(
    body: LessonCreate,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    quiz_json = _validate_quiz_json(body.quiz_json)
    async with db.begin():
        duplicate = await db.scalar(
            select(Lesson.id).where(
                Lesson.module_id == body.module_id,
                Lesson.order == body.order,
            ).limit(1)
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Lesson order already exists in this module")
        lesson = Lesson(
            module_id=body.module_id,
            order=body.order,
            level=body.level,
            title_fa=body.title_fa,
            content_fa=body.content_fa,
            quiz_json=quiz_json,
            xp_reward=body.xp_reward,
            xr_reward=Decimal(str(body.xr_reward)),
            is_active=body.is_active,
        )
        db.add(lesson)
        await db.flush()
        await _audit(
            db,
            admin_user,
            action="admin_academy_lesson_create",
            rule_key=f"academy.lesson.{lesson.id}",
            new_value=body.title_fa,
            reason="Admin lesson create",
        )
        lesson_id = lesson.id
    return {"success": True, "lesson_id": lesson_id}


@router.patch("/academy/lessons/{lesson_id}")
async def update_academy_lesson(
    lesson_id: int,
    body: LessonUpdate,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        lesson = await db.scalar(select(Lesson).where(Lesson.id == lesson_id).with_for_update())
        if lesson is None:
            raise HTTPException(status_code=404, detail="Lesson not found")
        old = {
            "title": lesson.title_fa,
            "active": lesson.is_active,
            "xp": lesson.xp_reward,
            "xr": lesson.xr_reward,
        }
        values = body.model_dump(exclude_none=True)
        if "quiz_json" in values:
            values["quiz_json"] = _validate_quiz_json(values["quiz_json"])
        for key, value in values.items():
            setattr(lesson, key, value)
        await _audit(
            db,
            admin_user,
            action="admin_academy_lesson_update",
            rule_key=f"academy.lesson.{lesson_id}",
            old_value=json.dumps(old, ensure_ascii=False, default=str),
            new_value=json.dumps(
                {"title": lesson.title_fa, "active": lesson.is_active, "xp": lesson.xp_reward, "xr": lesson.xr_reward},
                ensure_ascii=False,
                default=str,
            ),
            reason="Admin lesson update",
        )
    return {"success": True, "message": "Lesson updated", "lesson_id": lesson_id}


@router.get("/academy/progress")
async def academy_progress(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = (
        await db.execute(
            select(
                User.user_id,
                User.username,
                UserXP.total_xp,
                UserXP.level,
                func.count(UserLessonProgress.id).label("progress_count"),
                func.sum(case((UserLessonProgress.status == "done", 1), else_=0)).label("done_count"),
            )
            .outerjoin(UserXP, UserXP.user_id == User.user_id)
            .outerjoin(UserLessonProgress, UserLessonProgress.user_id == User.user_id)
            .where(User.deleted_at.is_(None))
            .group_by(User.user_id, User.username, UserXP.total_xp, UserXP.level)
            .order_by(func.coalesce(UserXP.total_xp, 0).desc(), User.user_id.asc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_id": int(row.user_id),
            "username": row.username,
            "total_xp": int(row.total_xp or 0),
            "level": row.level or "beginner",
            "progress_count": int(row.progress_count or 0),
            "done_count": int(row.done_count or 0),
        }
        for row in rows
    ]


@router.get("/missions")
async def missions_list(
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    progress = (
        select(
            UserMissionProgress.mission_id,
            func.count(UserMissionProgress.id).label("players"),
            func.sum(case((UserMissionProgress.completed.is_(True), 1), else_=0)).label("completed"),
            func.sum(case((UserMissionProgress.claimed.is_(True), 1), else_=0)).label("claimed"),
        )
        .group_by(UserMissionProgress.mission_id)
        .subquery()
    )
    stmt = (
        select(Mission, progress.c.players, progress.c.completed, progress.c.claimed)
        .outerjoin(progress, progress.c.mission_id == Mission.id)
        .order_by(Mission.mission_type.asc(), Mission.id.asc())
    )
    if active_only:
        stmt = stmt.where(Mission.is_active.is_(True))
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": int(m.id),
            "key": m.key,
            "title_fa": m.title_fa,
            "description_fa": m.description_fa,
            "mission_type": m.mission_type,
            "target_count": int(m.target_count),
            "reward_xr": float(m.reward_xr or 0),
            "reward_currency": float(m.reward_currency or 0),
            "is_active": bool(m.is_active),
            "players": int(players or 0),
            "completed": int(completed or 0),
            "claimed": int(claimed or 0),
        }
        for m, players, completed, claimed in rows
    ]


@router.post("/missions", status_code=201)
async def create_mission(
    body: MissionUpsert,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        exists = await db.scalar(select(Mission.id).where(Mission.key == body.key))
        if exists is not None:
            raise HTTPException(status_code=409, detail="Mission key already exists")
        mission = Mission(
            key=body.key,
            title_fa=body.title_fa,
            description_fa=body.description_fa,
            mission_type=body.mission_type,
            target_count=body.target_count,
            reward_xr=Decimal(str(body.reward_xr)),
            reward_currency=Decimal(str(body.reward_currency)),
            is_active=body.is_active,
        )
        db.add(mission)
        await db.flush()
        await _audit(db, admin_user, action="admin_mission_create", rule_key=f"mission.{mission.id}", new_value=body.key)
        mission_id = mission.id
    return {"success": True, "mission_id": mission_id}


@router.patch("/missions/{mission_id}")
async def update_mission(
    mission_id: int,
    body: MissionUpsert,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        mission = await db.scalar(select(Mission).where(Mission.id == mission_id).with_for_update())
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        duplicate = await db.scalar(
            select(Mission.id).where(
                Mission.key == body.key,
                Mission.id != mission_id,
            ).limit(1)
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Mission key already exists")
        for key, value in body.model_dump().items():
            setattr(mission, key, Decimal(str(value)) if key in {"reward_xr", "reward_currency"} else value)
        await _audit(db, admin_user, action="admin_mission_update", rule_key=f"mission.{mission_id}", new_value=body.key)
    return {"success": True, "message": "Mission updated"}


@router.get("/ranking")
async def ranking_overview(
    tab: Literal["nations", "rich", "traders"] = Query(default="nations"),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    if tab == "nations":
        rows = (
            await db.execute(
                select(Nation.nation_id, Nation.name, Nation.currency_code, Nation.exchange_rate, Nation.member_count, Nation.nation_rank)
                .where(Nation.is_active.is_(True), Nation.deleted_at.is_(None))
                .order_by(
                    case((Nation.nation_rank.is_(None), 1), else_=0),
                    Nation.nation_rank.asc(),
                    Nation.member_count.desc(),
                    Nation.nation_id.asc(),
                )
                .limit(limit)
            )
        ).all()
        return [{"rank": int(r.nation_rank) if r.nation_rank is not None else i + 1, "nation_id": int(r.nation_id), "name": r.name, "currency_code": r.currency_code, "exchange_rate": float(r.exchange_rate or 0), "member_count": int(r.member_count or 0)} for i, r in enumerate(rows)]
    if tab == "rich":
        holding = (
            select(
                CurrencyHolding.user_id.label("user_id"),
                func.coalesce(func.sum(CurrencyHolding.amount * Nation.exchange_rate), 0).label("holding_value"),
            )
            .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
            .where(Nation.is_active.is_(True))
            .group_by(CurrencyHolding.user_id)
            .subquery()
        )
        rows = (
            await db.execute(
                select(User.user_id, User.username, User.xr_balance, func.coalesce(holding.c.holding_value, 0).label("holding_value"))
                .outerjoin(holding, holding.c.user_id == User.user_id)
                .where(User.deleted_at.is_(None))
                .order_by((User.xr_balance + func.coalesce(holding.c.holding_value, 0)).desc(), User.user_id.asc())
                .limit(limit)
            )
        ).all()
        return [{"rank": i + 1, "user_id": int(r.user_id), "username": r.username, "total_xr": float((r.xr_balance or 0) + (r.holding_value or 0))} for i, r in enumerate(rows)]
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    trade_count = func.count(UserActivity.id)
    rows = (
        await db.execute(
            select(User.user_id, User.username, trade_count.label("trade_count"))
            .outerjoin(
                UserActivity,
                (UserActivity.user_id == User.user_id)
                & (UserActivity.created_at >= since)
                & (UserActivity.activity_type == ActivityType.TRADE),
            )
            .where(User.deleted_at.is_(None))
            .group_by(User.user_id, User.username)
            .order_by(trade_count.desc(), User.user_id.asc())
            .limit(limit)
        )
    ).all()
    return [{"rank": i + 1, "user_id": int(r.user_id), "username": r.username, "trade_count": int(r.trade_count or 0)} for i, r in enumerate(rows)]


@router.get("/ai/summary")
async def ai_summary(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    ai_users = int(await db.scalar(select(func.count(User.user_id)).where(User.is_ai.is_(True), User.deleted_at.is_(None))) or 0)
    ai_nations = int(await db.scalar(select(func.count(Nation.nation_id)).where(Nation.is_ai.is_(True), Nation.is_active.is_(True))) or 0)
    ai_trades_24h = int(
        await db.scalar(
            select(func.count(UserActivity.id))
            .join(User, User.user_id == UserActivity.user_id)
            .where(
                User.is_ai.is_(True),
                UserActivity.activity_type == ActivityType.TRADE,
                UserActivity.created_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24),
            )
        )
        or 0
    )
    usage_today = await db.execute(
        select(
            func.coalesce(func.sum(AIUsageLog.advisor_questions_used), 0),
            func.coalesce(func.sum(AIUsageLog.portfolio_scans_used), 0),
            func.coalesce(func.sum(AIUsageLog.war_analysis_used), 0),
        ).where(AIUsageLog.date == date.today())
    )
    advisor, portfolio, war = usage_today.one()
    return {
        "ai_users": ai_users,
        "ai_nations": ai_nations,
        "ai_trades_24h": ai_trades_24h,
        "advisor_questions_today": int(advisor or 0),
        "portfolio_scans_today": int(portfolio or 0),
        "war_analysis_today": int(war or 0),
    }


@router.get("/ai/users")
async def ai_users(
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = (
        await db.execute(
            select(
                User.user_id,
                User.username,
                User.ai_strategy,
                User.ai_tier,
                User.balance,
                User.xr_balance,
                User.home_nation_id,
                User.created_at,
            )
            .where(User.is_ai.is_(True), User.deleted_at.is_(None))
            .order_by(User.user_id.asc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_id": int(r.user_id),
            "username": r.username,
            "ai_strategy": r.ai_strategy,
            "ai_tier": getattr(r.ai_tier, "value", str(r.ai_tier)),
            "balance": float(r.balance or 0),
            "xr_balance": float(r.xr_balance or 0),
            "home_nation_id": int(r.home_nation_id) if r.home_nation_id is not None else None,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


@router.patch("/ai/users/{user_id}")
async def update_ai_user(
    user_id: int,
    body: AIUserUpdate,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        user = await db.scalar(select(User).where(User.user_id == user_id).with_for_update())
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        old = {"is_ai": user.is_ai, "strategy": user.ai_strategy, "tier": getattr(user.ai_tier, "value", str(user.ai_tier))}
        user.is_ai = body.is_ai
        user.ai_strategy = body.ai_strategy
        user.ai_tier = AITier(body.ai_tier)
        await _audit(
            db,
            admin_user,
            action="admin_ai_user_update",
            rule_key=f"ai.user.{user_id}",
            old_value=json.dumps(old, ensure_ascii=False),
            new_value=json.dumps(body.model_dump(), ensure_ascii=False),
            reason="AI control panel",
        )
    return {"success": True, "message": "AI profile updated"}


@router.get("/ai/gemini-health")
async def gemini_health(
    admin_user: AdminUser = Depends(get_admin_user),
):
    started = datetime.now(UTC)
    ok = await companion.health_check()
    return {
        "ok": bool(ok),
        "checked_at": started.isoformat(),
        "provider": "gemini",
        "model": settings.ai_model,
        "enabled": bool(settings.ai_enabled),
    }


@router.get("/founder/summary")
async def founder_summary(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = await db.execute(select(NationFoundingDraft.status, func.count(NationFoundingDraft.id)).group_by(NationFoundingDraft.status))
    status_counts = {str(s): int(c) for s, c in rows.all()}
    return {
        "statuses": status_counts,
        "active": sum(v for k, v in status_counts.items() if k in {"WAITING_GROUP", "GROUP_READY", "NAMING", "FLAG", "REVIEW", "FINALIZING"}),
        "expired": status_counts.get("EXPIRED", 0),
        "completed": status_counts.get("COMPLETED", 0),
        "cancelled": status_counts.get("CANCELLED", 0),
    }


@router.get("/founder/drafts")
async def founder_drafts(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    stmt = (
        select(
            NationFoundingDraft.id,
            NationFoundingDraft.founder_user_id,
            User.username,
            NationFoundingDraft.group_id,
            NationFoundingDraft.group_title,
            NationFoundingDraft.group_username,
            NationFoundingDraft.group_type,
            NationFoundingDraft.nation_name,
            NationFoundingDraft.currency_code,
            NationFoundingDraft.flag_emoji,
            NationFoundingDraft.status,
            NationFoundingDraft.expires_at,
            NationFoundingDraft.created_at,
            NationFoundingDraft.updated_at,
        )
        .join(User, User.user_id == NationFoundingDraft.founder_user_id)
        .order_by(NationFoundingDraft.updated_at.desc(), NationFoundingDraft.id.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(NationFoundingDraft.status == status)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": int(r.id),
            "founder_user_id": int(r.founder_user_id),
            "username": r.username,
            "group_id": int(r.group_id) if r.group_id is not None else None,
            "group_title": r.group_title,
            "group_username": r.group_username,
            "group_type": r.group_type,
            "nation_name": r.nation_name,
            "currency_code": r.currency_code,
            "flag_emoji": r.flag_emoji,
            "status": r.status,
            "expires_at": _iso(r.expires_at),
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


@router.post("/founder/drafts/{draft_id}/finalize")
async def finalize_founder_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    founder_id = await db.scalar(
        select(NationFoundingDraft.founder_user_id)
        .where(NationFoundingDraft.id == draft_id)
        .limit(1)
    )
    if founder_id is None:
        raise HTTPException(status_code=404, detail="Founder draft not found")
    await db.rollback()
    try:
        nation, group_id = await finalize_draft(
            db,
            founder_user_id=int(founder_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with db.begin():
        await _audit(
            db,
            admin_user,
            action="admin_founder_draft_finalize",
            rule_key=f"founder.draft.{draft_id}",
            new_value=f"COMPLETED:nation={nation.nation_id}",
            reason=f"Admin finalized founder draft for group {group_id}",
        )
    return {
        "success": True,
        "message": "Founder draft finalized",
        "nation_id": int(nation.nation_id),
        "group_id": int(group_id),
    }


@router.post("/founder/drafts/{draft_id}/cancel")
async def cancel_founder_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        draft = await db.scalar(select(NationFoundingDraft).where(NationFoundingDraft.id == draft_id).with_for_update())
        if draft is None:
            raise HTTPException(status_code=404, detail="Founder draft not found")
        old = draft.status
        draft.status = "CANCELLED"
        await _audit(db, admin_user, action="admin_founder_draft_cancel", rule_key=f"founder.draft.{draft_id}", old_value=old, new_value="CANCELLED")
    return {"success": True, "message": "Founder draft cancelled"}


@router.get("/profiles")
async def profiles(
    search: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    stmt = (
        select(
            User.user_id,
            User.username,
            User.role,
            User.home_nation_id,
            Nation.name.label("nation_name"),
            User.created_at,
            User.deleted_at,
        )
        .outerjoin(Nation, Nation.nation_id == User.home_nation_id)
        .order_by(User.created_at.desc(), User.user_id.desc())
        .limit(limit)
    )
    if search.strip():
        stmt = stmt.where(or_(User.username.ilike(f"%{search.strip()}%"), func.cast(User.user_id, str).like(f"%{search.strip()}%")))
    rows = (await db.execute(stmt)).all()
    result = []
    for r in rows:
        membership_nation = await db.scalar(
            select(NationMembership.nation_id)
            .where(
                NationMembership.user_id == r.user_id,
                NationMembership.is_active.is_(True),
            )
            .order_by(NationMembership.joined_at.desc())
            .limit(1)
        )
        result.append(
            {
                "user_id": int(r.user_id),
                "username": r.username,
                "role": r.role,
                "home_nation_id": int(r.home_nation_id) if r.home_nation_id is not None else None,
                "nation_name": r.nation_name,
                "created_at": _iso(r.created_at),
                "is_banned": r.deleted_at is not None,
                "membership_nation_id": int(membership_nation) if membership_nation is not None else None,
                "context_consistent": r.home_nation_id == membership_nation,
            }
        )
    return result


@router.get("/onboarding/summary")
async def onboarding_summary(
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    total = int(await db.scalar(select(func.count(User.user_id)).where(User.deleted_at.is_(None))) or 0)
    with_username = int(await db.scalar(select(func.count(User.user_id)).where(User.deleted_at.is_(None), User.username.is_not(None), User.username != "")) or 0)
    with_holding = int(
        await db.scalar(
            select(func.count(func.distinct(CurrencyHolding.user_id))).join(User, User.user_id == CurrencyHolding.user_id).where(User.deleted_at.is_(None))
        )
        or 0
    )
    first_trade = int(
        await db.scalar(
            select(func.count(func.distinct(UserActivity.user_id)))
            .join(User, User.user_id == UserActivity.user_id)
            .where(
                UserActivity.activity_type == ActivityType.TRADE,
                User.is_ai.is_(False),
                User.deleted_at.is_(None),
            )
        )
        or 0
    )
    return {
        "total_users": total,
        "with_username": with_username,
        "with_holding": with_holding,
        "with_trade": first_trade,
        "username_rate": round(with_username / total * 100, 1) if total else 0,
        "holding_rate": round(with_holding / total * 100, 1) if total else 0,
        "trade_rate": round(first_trade / total * 100, 1) if total else 0,
    }


@router.get("/behavior-snapshots")
async def behavior_snapshots(
    limit: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = (
        await db.execute(
            select(
                BehaviorSnapshot.at,
                BehaviorSnapshot.active_players_count,
                BehaviorSnapshot.buy_tx_count,
                BehaviorSnapshot.sell_tx_count,
                BehaviorSnapshot.export_tx_count,
                BehaviorSnapshot.import_tx_count,
                BehaviorSnapshot.total_volume,
                BehaviorSnapshot.avg_net_worth,
                BehaviorSnapshot.median_net_worth,
                BehaviorSnapshot.gini_coefficient,
                BehaviorSnapshot.top10_wealth_share,
            )
            .order_by(BehaviorSnapshot.at.desc(), BehaviorSnapshot.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "at": _iso(r.at),
            "active_players_count": int(r.active_players_count),
            "buy_tx_count": int(r.buy_tx_count),
            "sell_tx_count": int(r.sell_tx_count),
            "export_tx_count": int(r.export_tx_count),
            "import_tx_count": int(r.import_tx_count),
            "total_volume": float(r.total_volume or 0),
            "avg_net_worth": float(r.avg_net_worth or 0),
            "median_net_worth": float(r.median_net_worth or 0),
            "gini_coefficient": float(r.gini_coefficient or 0),
            "top10_wealth_share": float(r.top10_wealth_share or 0),
        }
        for r in rows
    ]


@router.get("/price-alerts")
async def price_alerts(
    triggered: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    stmt = (
        select(PriceAlert.id, PriceAlert.user_id, User.username, PriceAlert.currency_code, PriceAlert.target_price, PriceAlert.direction, PriceAlert.is_triggered, PriceAlert.created_at, PriceAlert.triggered_at)
        .join(User, User.user_id == PriceAlert.user_id)
        .order_by(PriceAlert.created_at.desc())
        .limit(limit)
    )
    if triggered is not None:
        stmt = stmt.where(PriceAlert.is_triggered.is_(triggered))
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": int(r.id),
            "user_id": int(r.user_id),
            "username": r.username,
            "currency_code": r.currency_code,
            "target_price": float(r.target_price or 0),
            "direction": getattr(r.direction, "value", str(r.direction)),
            "is_triggered": bool(r.is_triggered),
            "created_at": _iso(r.created_at),
            "triggered_at": _iso(r.triggered_at),
        }
        for r in rows
    ]


@router.get("/support/overview")
async def support_overview(
    redis=Depends(get_redis),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    error_keys = []
    event_keys = []
    if redis is not None:
        async for key in redis.scan_iter(match="opex:support:errors:*", count=100):
            error_keys.append(key)
        async for key in redis.scan_iter(match="opex:support:events:*", count=100):
            event_keys.append(key)
    errors = 0
    events = 0
    for key in error_keys:
        errors += int(await redis.llen(key) or 0)
    for key in event_keys:
        events += int(await redis.llen(key) or 0)
    return {"error_keys": len(error_keys), "event_keys": len(event_keys), "recent_errors": errors, "recent_events": events}


@router.get("/support/users/{user_id}")
async def support_user_telemetry(
    user_id: int,
    admin_user: AdminUser = Depends(get_admin_user),
):
    return await get_recent_telemetry(user_id)


@router.get("/treasury")
async def treasury_list(
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = (
        await db.execute(
            select(
                Nation.nation_id,
                Nation.name,
                Nation.currency_code,
                Nation.treasury,
                NationTreasury.balance_xr,
                NationTreasury.balance_local,
                NationTreasury.total_deposited,
                NationTreasury.last_deposit_at,
            )
            .outerjoin(NationTreasury, NationTreasury.nation_id == Nation.nation_id)
            .where(Nation.is_active.is_(True), Nation.deleted_at.is_(None))
            .order_by(Nation.treasury.desc(), Nation.nation_id.asc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "nation_id": int(r.nation_id),
            "name": r.name,
            "currency_code": r.currency_code,
            "nation_treasury": float(r.treasury or 0),
            "balance_xr": float(r.balance_xr or 0),
            "balance_local": float(r.balance_local or 0),
            "total_deposited": float(r.total_deposited or 0),
            "last_deposit_at": _iso(r.last_deposit_at),
        }
        for r in rows
    ]


@router.get("/treasury/{nation_id}/logs")
async def treasury_logs(
    nation_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    rows = (
        await db.execute(
            select(
                TreasuryLog.id,
                TreasuryLog.action,
                TreasuryLog.amount_xr,
                TreasuryLog.note,
                TreasuryLog.created_at,
                User.username,
            )
            .outerjoin(User, User.user_id == TreasuryLog.actor_id)
            .where(TreasuryLog.nation_id == nation_id)
            .order_by(TreasuryLog.created_at.desc(), TreasuryLog.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": int(r.id),
            "action": r.action,
            "amount_xr": float(r.amount_xr or 0),
            "note": r.note,
            "created_at": _iso(r.created_at),
            "actor_username": r.username,
        }
        for r in rows
    ]


@router.post("/treasury/{nation_id}/adjust")
async def treasury_adjust(
    nation_id: int,
    body: TreasuryAdjust,
    db: AsyncSession = Depends(get_db_session),
    admin_user: AdminUser = Depends(get_admin_user),
):
    async with db.begin():
        nation = await db.scalar(select(Nation).where(Nation.nation_id == nation_id).with_for_update())
        if nation is None or not nation.is_active:
            raise HTTPException(status_code=404, detail="Active nation not found")
        treasury = await db.scalar(select(NationTreasury).where(NationTreasury.nation_id == nation_id).with_for_update())
        if treasury is None:
            treasury = NationTreasury(nation_id=nation_id)
            db.add(treasury)
            await db.flush()
        amount = Decimal(str(body.amount_xr))
        before = Decimal(str(treasury.balance_xr or 0))
        after = before + amount
        if after < 0:
            raise HTTPException(status_code=422, detail="Treasury balance cannot become negative")
        treasury.balance_xr = after
        nation.treasury = after
        db.add(
            TreasuryLog(
                nation_id=nation_id,
                actor_id=admin_user.user_id,
                action="ADMIN_ADJUST",
                amount_xr=amount,
                note=body.note,
            )
        )
        await _audit(
            db,
            admin_user,
            action="admin_treasury_adjust",
            rule_key=f"treasury.{nation_id}",
            old_value=before,
            new_value=after,
            reason=body.note,
        )
    return {"success": True, "message": "Treasury adjusted", "balance_xr": float(after)}


@router.get("/scheduler")
async def scheduler_status(
    redis=Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
):
    result = []
    for job_id, (schedule, label) in SCHEDULER_JOB_META.items():
        item = {"job_id": job_id, "schedule": schedule, "label": label, "status": "unknown"}
        if redis is not None:
            raw = await redis.hgetall(f"opex:scheduler:job:{job_id}")
            if raw:
                item.update({
                    "status": raw.get("status", "unknown"),
                    "last_run": raw.get("last_run"),
                    "last_error": raw.get("last_error"),
                    "next_run": raw.get("next_run"),
                    "paused": raw.get("paused") == "1",
                })
        result.append(item)
    return result


@router.post("/scheduler/{job_id}/{command}")
async def scheduler_command(
    job_id: str,
    command: Literal["pause", "resume", "run_now"],
    redis=Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
):
    if job_id not in SCHEDULER_JOB_META:
        raise HTTPException(status_code=404, detail="Unknown scheduler job")
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await redis.rpush(
        "opex:admin:scheduler:commands",
        json.dumps({"job_id": job_id, "command": command, "admin_user_id": admin_user.user_id}),
    )
    return {"success": True, "message": f"Scheduler command queued: {command}"}
