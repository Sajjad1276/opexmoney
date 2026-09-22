from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import AdminUser, get_admin_user, settings
from admin.cache import invalidate_prefix
from admin.dependencies import get_db_session, get_redis, page_count
from admin.schemas.responses import ActionResult, GovernanceLedgerItem, PaginatedResponse
from app.database.models import (
    GovernanceLedger,
    Mission,
    Nation,
    NationWar,
    User,
    UserMissionProgress,
    WarStatus,
    WorldEvent,
    WorldEventEffectType,
    WorldEventScope,
    WorldEventSource,
    WorldEventType,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/actions",
    tags=["admin-actions"],
)

_CONFIRM_MESSAGE = "Send confirm=true to execute this action"

_ACTION_RATE_LIMITS: dict[str, int] = {
    "broadcast": 1,
    "give-bonus": 5,
    "ban-player": 10,
    "unban-player": 10,
    "reset-rates": 2,
    "end-war": 10,
    "create-event": 5,
    "send-mission-reward": 10,
}


class _StrictBody(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        allow_inf_nan=False,
    )


class BroadcastBody(_StrictBody):
    message: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML", "Markdown"] = "HTML"
    target: Literal["all", "active_only"] = "active_only"


class BonusBody(_StrictBody):
    user_ids: list[int] | Literal["all"]
    amount_xr: float = Field(gt=0, le=100000)
    note: str = Field(min_length=1, max_length=255)

    @field_validator("user_ids")
    @classmethod
    def validate_user_ids(cls, value: list[int] | Literal["all"]) -> list[int] | Literal["all"]:
        if value == "all":
            return value
        if len(value) == 0:
            raise ValueError("user_ids cannot be empty")
        if len(value) > 500:
            raise ValueError("user_ids cannot contain more than 500 items")
        if len(set(value)) != len(value):
            raise ValueError("user_ids must be unique")
        for user_id in value:
            if user_id <= 0:
                raise ValueError("user_ids must contain only positive integers")
        return value


class BanPlayerBody(_StrictBody):
    user_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=255)


class UnbanPlayerBody(_StrictBody):
    user_id: int = Field(gt=0)


class ResetRatesBody(_StrictBody):
    nation_id: int | Literal["all"]
    new_rate: float = Field(gt=0.0001, le=1000.0)
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("nation_id")
    @classmethod
    def validate_nation_id(cls, value: int | Literal["all"]) -> int | Literal["all"]:
        if value == "all":
            return value
        if value <= 0:
            raise ValueError("nation_id must be a positive integer")
        return value


class EndWarBody(_StrictBody):
    war_id: int = Field(gt=0)
    result: Literal["nation_wins", "opponent_wins", "draw"]
    reason: str = Field(default="", max_length=255)


class CreateEventBody(_StrictBody):
    event_type: str = Field(min_length=1)
    scope: Literal["local", "national", "global"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    effect_type: str = Field(min_length=1)
    effect_magnitude: float = Field(gt=0, le=10.0)
    duration_minutes: int = Field(gt=0, le=10080)
    affected_nation_id: int | None = Field(default=None, gt=0)
    affected_currency: str | None = Field(default=None, max_length=4)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        try:
            WorldEventType(value)
        except ValueError as exc:
            raise ValueError(f"Invalid event_type: {value}") from exc
        return value

    @field_validator("effect_type")
    @classmethod
    def validate_effect_type(cls, value: str) -> str:
        try:
            WorldEventEffectType(value)
        except ValueError as exc:
            raise ValueError(f"Invalid effect_type: {value}") from exc
        return value


class MissionRewardBody(_StrictBody):
    user_ids: list[int] = Field(max_length=200)
    mission_key: str = Field(min_length=1)
    override_xr: float | None = Field(default=None, gt=0, le=100000)

    @field_validator("user_ids")
    @classmethod
    def validate_user_ids(cls, value: list[int]) -> list[int]:
        if len(value) == 0:
            raise ValueError("user_ids cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("user_ids must be unique")
        for user_id in value:
            if user_id <= 0:
                raise ValueError("user_ids must contain only positive integers")
        return value


async def check_action_rate_limit(action: str, redis: Redis) -> None:
    limit = _ACTION_RATE_LIMITS.get(action)
    if limit is None:
        return

    try:
        key = f"admin:action_rl:{action}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit for {action}: try again later",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Action rate-limit check failed for %s: %s",
            action,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Action rate limiter unavailable",
        ) from None


async def _audit(
    session: AsyncSession,
    admin_user: AdminUser,
    action: str,
    rule_key: str,
    old_value: str | None = None,
    new_value: str | None = None,
    reason: str | None = None,
) -> None:
    entry = GovernanceLedger(
        at=datetime.now(timezone.utc),
        actor_player_id=admin_user.user_id,
        action=action,
        rule_key=rule_key,
        old_value=str(old_value)[:64] if old_value is not None else None,
        new_value=str(new_value)[:64] if new_value is not None else None,
        reason=str(reason)[:255] if reason is not None else None,
    )
    session.add(entry)


async def _invalidate(*prefixes: str, redis: Redis | None) -> None:
    if redis is None:
        return

    for prefix in prefixes:
        try:
            await invalidate_prefix(redis, prefix)
        except Exception as exc:
            logger.warning(
                "Cache invalidation failed for %s: %s",
                prefix,
                exc,
                exc_info=True,
            )


def _dry_run() -> ActionResult:
    return ActionResult(
        success=False,
        affected=0,
        message=_CONFIRM_MESSAGE,
    )


def _require_redis(redis: Redis | None) -> Redis:
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return redis


async def _rollback_read_transaction(session: AsyncSession) -> None:
    if session.in_transaction():
        await session.rollback()


def _error(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


@router.post("/broadcast", response_model=ActionResult)
async def broadcast(
    body: BroadcastBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("broadcast", redis_client)

    try:
        target_conditions = [User.is_ai.is_(False)]
        if body.target == "active_only":
            target_conditions.append(User.deleted_at.is_(None))

        rows = await db.execute(
            select(User.user_id).where(*target_conditions)
        )
        user_ids = [int(user_id) for user_id in rows.scalars().all()]
        await _rollback_read_transaction(db)

        sent = 0
        failed = 0
        telegram_url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage"

        timeout = httpx.Timeout(5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for user_id in user_ids:
                try:
                    response = await client.post(
                        telegram_url,
                        json={
                            "chat_id": user_id,
                            "text": body.message,
                            "parse_mode": body.parse_mode,
                        },
                    )
                    payload = response.json()
                    if response.is_success and payload.get("ok") is True:
                        sent += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                finally:
                    await asyncio.sleep(0.05)

        async with db.begin():
            await _audit(
                db,
                admin_user,
                action="admin_broadcast",
                rule_key="broadcast",
                new_value=f"sent:{sent},failed:{failed}",
                reason=body.message[:100],
            )
            await db.flush()

        await _invalidate("stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=sent,
            message=f"Sent: {sent}, Failed: {failed}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/give-bonus", response_model=ActionResult)
async def give_bonus(
    body: BonusBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("give-bonus", redis_client)

    try:
        if body.user_ids == "all":
            count = int(
                await db.scalar(
                    select(func.count(User.user_id)).where(
                        User.deleted_at.is_(None),
                        User.is_ai.is_(False),
                    )
                )
                or 0
            )
        else:
            requested_ids = list(body.user_ids)
            existing_rows = await db.execute(
                select(User.user_id).where(User.user_id.in_(requested_ids))
            )
            existing_ids = {int(user_id) for user_id in existing_rows.scalars().all()}
            missing = sorted(set(requested_ids) - existing_ids)
            if missing:
                await _rollback_read_transaction(db)
                raise _error(f"Unknown user_ids: {missing}")
            count = len(requested_ids)

        await _rollback_read_transaction(db)

        amount = Decimal(str(body.amount_xr))

        async with db.begin():
            if body.user_ids == "all":
                result = await db.execute(
                    update(User)
                    .where(
                        User.deleted_at.is_(None),
                        User.is_ai.is_(False),
                    )
                    .values(xr_balance=User.xr_balance + amount)
                )
            else:
                result = await db.execute(
                    update(User)
                    .where(User.user_id.in_(list(body.user_ids)))
                    .values(xr_balance=User.xr_balance + amount)
                )

            affected = int(result.rowcount or 0)
            await _audit(
                db,
                admin_user,
                action="admin_bonus",
                rule_key="xr_balance",
                new_value=str(body.amount_xr),
                reason=body.note,
            )
            await db.flush()

        if body.user_ids == "all" and affected != count:
            logger.warning(
                "Bonus target count changed during transaction: expected=%s affected=%s",
                count,
                affected,
            )

        await _invalidate("players", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=affected,
            message=f"Added {body.amount_xr} XR to {affected} players",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/ban-player", response_model=ActionResult)
async def ban_player(
    body: BanPlayerBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("ban-player", redis_client)

    try:
        row = await db.execute(
            select(User.user_id, User.username, User.deleted_at).where(
                User.user_id == body.user_id
            )
        )
        user = row.first()
        await _rollback_read_transaction(db)

        if user is None:
            raise HTTPException(status_code=404, detail="Player not found")
        if user.deleted_at is not None:
            raise HTTPException(status_code=409, detail="Player already banned")

        now = datetime.now(timezone.utc)
        async with db.begin():
            result = await db.execute(
                update(User)
                .where(
                    User.user_id == body.user_id,
                    User.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
            affected = int(result.rowcount or 0)
            if affected != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Player already banned or not found",
                )

            await _audit(
                db,
                admin_user,
                action="admin_ban",
                rule_key="user.deleted_at",
                old_value="active",
                new_value="banned",
                reason=body.reason,
            )
            await db.flush()

        await _invalidate("players", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=1,
            message=f"Player {user.username} banned",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/unban-player", response_model=ActionResult)
async def unban_player(
    body: UnbanPlayerBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("unban-player", redis_client)

    try:
        row = await db.execute(
            select(User.user_id, User.username, User.deleted_at).where(
                User.user_id == body.user_id
            )
        )
        user = row.first()
        await _rollback_read_transaction(db)

        if user is None:
            raise HTTPException(status_code=404, detail="Player not found")
        if user.deleted_at is None:
            raise HTTPException(status_code=409, detail="Player is not banned")

        async with db.begin():
            result = await db.execute(
                update(User)
                .where(
                    User.user_id == body.user_id,
                    User.deleted_at.is_not(None),
                )
                .values(deleted_at=None)
            )
            affected = int(result.rowcount or 0)
            if affected != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Player already active or not found",
                )

            await _audit(
                db,
                admin_user,
                action="admin_unban",
                rule_key="user.deleted_at",
                old_value="banned",
                new_value="active",
                reason=None,
            )
            await db.flush()

        await _invalidate("players", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=1,
            message=f"Player {user.username} unbanned",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/reset-rates", response_model=ActionResult)
async def reset_rates(
    body: ResetRatesBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("reset-rates", redis_client)

    try:
        old_rate: str = "multiple"
        if body.nation_id != "all":
            row = await db.execute(
                select(Nation.nation_id, Nation.exchange_rate).where(
                    Nation.nation_id == body.nation_id,
                    Nation.is_active.is_(True),
                    Nation.deleted_at.is_(None),
                )
            )
            nation = row.first()
            await _rollback_read_transaction(db)
            if nation is None:
                raise HTTPException(status_code=404, detail="Nation not found")
            old_rate = str(nation.exchange_rate)

        new_rate = Decimal(str(body.new_rate))
        async with db.begin():
            if body.nation_id == "all":
                result = await db.execute(
                    update(Nation)
                    .where(
                        Nation.is_active.is_(True),
                        Nation.deleted_at.is_(None),
                        Nation.is_ai.is_(False),
                    )
                    .values(
                        rate_prev=Nation.exchange_rate,
                        rate_24h_open=Nation.exchange_rate,
                        exchange_rate=new_rate,
                    )
                )
            else:
                result = await db.execute(
                    update(Nation)
                    .where(
                        Nation.nation_id == body.nation_id,
                        Nation.is_active.is_(True),
                        Nation.deleted_at.is_(None),
                        Nation.is_ai.is_(False),
                    )
                    .values(
                        rate_prev=Nation.exchange_rate,
                        rate_24h_open=Nation.exchange_rate,
                        exchange_rate=new_rate,
                    )
                )

            affected = int(result.rowcount or 0)
            if body.nation_id != "all" and affected != 1:
                raise HTTPException(status_code=409, detail="Nation changed or is no longer active")

            await _audit(
                db,
                admin_user,
                action="admin_reset_rate",
                rule_key="nation.exchange_rate",
                old_value=old_rate,
                new_value=str(body.new_rate),
                reason=body.reason,
            )
            await db.flush()

        await _invalidate("nations", "economy", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=affected,
            message=f"Rate reset to {body.new_rate} for {affected} nation(s)",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/end-war", response_model=ActionResult)
async def end_war(
    body: EndWarBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("end-war", redis_client)

    try:
        row = await db.execute(
            select(
                NationWar.id,
                NationWar.status,
                NationWar.declared_at,
                NationWar.ends_at,
            ).where(
                NationWar.id == body.war_id,
                NationWar.status == WarStatus.ACTIVE,
            )
        )
        war = row.first()
        await _rollback_read_transaction(db)

        if war is None:
            raise HTTPException(status_code=404, detail="Active war not found")

        mapped_status = (
            WarStatus.DRAW
            if body.result == "draw"
            else WarStatus.ENDED
        )
        now = datetime.now(timezone.utc)

        async with db.begin():
            result = await db.execute(
                update(NationWar)
                .where(
                    NationWar.id == body.war_id,
                    NationWar.status == WarStatus.ACTIVE,
                )
                .values(
                    status=mapped_status,
                    ended_at=now,
                )
            )
            affected = int(result.rowcount or 0)
            if affected != 1:
                raise HTTPException(
                    status_code=409,
                    detail="War already ended or not found",
                )

            await _audit(
                db,
                admin_user,
                action="admin_end_war",
                rule_key="nation_war.status",
                old_value="active",
                new_value=body.result,
                reason=body.reason or None,
            )
            await db.flush()

        await _invalidate("wars", "nations", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=1,
            message=f"War #{body.war_id} ended: {body.result}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/create-event", response_model=ActionResult)
async def create_event(
    body: CreateEventBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("create-event", redis_client)

    try:
        affected_nation = None
        if body.affected_nation_id is not None:
            row = await db.execute(
                select(Nation.nation_id).where(
                    Nation.nation_id == body.affected_nation_id,
                    Nation.is_active.is_(True),
                    Nation.deleted_at.is_(None),
                )
            )
            affected_nation = row.scalar_one_or_none()
            await _rollback_read_transaction(db)
            if affected_nation is None:
                raise HTTPException(
                    status_code=422,
                    detail="affected_nation_id must reference an active nation",
                )

        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(minutes=body.duration_minutes)

        async with db.begin():
            event = WorldEvent(
                event_type=WorldEventType(body.event_type),
                scope=WorldEventScope(body.scope),
                title=body.title,
                description=body.description,
                effect_type=WorldEventEffectType(body.effect_type),
                effect_magnitude=body.effect_magnitude,
                duration_minutes=body.duration_minutes,
                started_at=now,
                ends_at=ends_at,
                is_active=True,
                source=WorldEventSource.USER_ACTION,
                announced_in_group=False,
                affected_nation_id=body.affected_nation_id,
                affected_currency=body.affected_currency,
            )
            db.add(event)
            await db.flush()

            await _audit(
                db,
                admin_user,
                action="admin_create_event",
                rule_key="world_events",
                new_value=f"{body.event_type}:{body.effect_magnitude}",
                reason=body.title,
            )
            await db.flush()

        await _invalidate("economy", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=1,
            message=(
                f"Event '{body.title}' created, active for "
                f"{body.duration_minutes} minutes"
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.post("/send-mission-reward", response_model=ActionResult)
async def send_mission_reward(
    body: MissionRewardBody,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult:
    redis_client = _require_redis(redis)

    if not confirm:
        return _dry_run()

    await check_action_rate_limit("send-mission-reward", redis_client)

    try:
        mission = await db.scalar(
            select(Mission).where(
                Mission.key == body.mission_key,
                Mission.is_active.is_(True),
            )
        )
        if mission is None:
            await _rollback_read_transaction(db)
            raise HTTPException(status_code=404, detail="Mission not found")

        if not body.user_ids:
            await _rollback_read_transaction(db)
            raise _error("user_ids cannot be empty")

        existing_rows = await db.execute(
            select(User.user_id).where(User.user_id.in_(body.user_ids))
        )
        existing_ids = {int(user_id) for user_id in existing_rows.scalars().all()}
        missing = sorted(set(body.user_ids) - existing_ids)
        if missing:
            await _rollback_read_transaction(db)
            raise _error(f"Unknown user_ids: {missing}")

        reward_xr = (
            Decimal(str(body.override_xr))
            if body.override_xr is not None
            else Decimal(str(mission.reward_xr))
        )
        mission_id = int(mission.id)
        target_count = int(mission.target_count)
        await _rollback_read_transaction(db)

        now = datetime.now(timezone.utc)
        user_ids = list(body.user_ids)

        async with db.begin():
            balance_result = await db.execute(
                update(User)
                .where(User.user_id.in_(user_ids))
                .values(xr_balance=User.xr_balance + reward_xr)
            )
            affected = int(balance_result.rowcount or 0)
            if affected != len(user_ids):
                raise HTTPException(
                    status_code=409,
                    detail="One or more players changed before reward execution",
                )

            progress_values = [
                {
                    "user_id": user_id,
                    "mission_id": mission_id,
                    "progress": target_count,
                    "completed": True,
                    "completed_at": now,
                    "claimed": True,
                }
                for user_id in user_ids
            ]
            progress_stmt = pg_insert(UserMissionProgress).values(progress_values)
            progress_stmt = progress_stmt.on_conflict_do_update(
                constraint="uq_user_mission",
                set_={
                    "progress": target_count,
                    "completed": True,
                    "completed_at": now,
                    "claimed": True,
                },
            )
            await db.execute(progress_stmt)

            await _audit(
                db,
                admin_user,
                action="admin_mission_reward",
                rule_key=f"mission.{body.mission_key}",
                new_value=f"granted_to:{affected}",
                reason=f"xr:{reward_xr}",
            )
            await db.flush()

        await _invalidate("players", "stats", redis=redis_client)
        return ActionResult(
            success=True,
            affected=affected,
            message=f"Mission reward granted to {affected} players",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


@router.get("/audit-log", response_model=ActionResult | PaginatedResponse[GovernanceLedgerItem])
async def audit_log(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    action_filter: str | None = Query(default=None, max_length=100),
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: AdminUser = Depends(get_admin_user),
) -> ActionResult | PaginatedResponse[GovernanceLedgerItem]:
    del redis

    if not confirm:
        return _dry_run()

    try:
        conditions: list[Any] = [
            GovernanceLedger.action.like("admin\\_%", escape="\\")
        ]
        if action_filter is not None:
            conditions.append(GovernanceLedger.action == action_filter)

        count_stmt = select(func.count(GovernanceLedger.id)).where(*conditions)
        rows_stmt = (
            select(
                GovernanceLedger.id,
                GovernanceLedger.at,
                User.username,
                GovernanceLedger.action,
                GovernanceLedger.rule_key,
                GovernanceLedger.old_value,
                GovernanceLedger.new_value,
                GovernanceLedger.reason,
            )
            .outerjoin(
                User,
                User.user_id == GovernanceLedger.actor_player_id,
            )
            .where(*conditions)
            .order_by(GovernanceLedger.at.desc(), GovernanceLedger.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )

        total = await db.scalar(count_stmt)
        rows = await db.execute(rows_stmt)

        total_int = int(total or 0)
        items = [
            GovernanceLedgerItem(
                id=int(row.id),
                at=row.at.isoformat(),
                actor_username=row.username,
                action=row.action,
                rule_key=row.rule_key,
                old_value=row.old_value,
                new_value=row.new_value,
                reason=row.reason,
            )
            for row in rows.all()
        ]

        await _rollback_read_transaction(db)

        return PaginatedResponse(
            items=items,
            total=total_int,
            page=page,
            pages=page_count(total_int, limit),
        )
    except Exception as exc:
        logger.error(f"Action failed: {exc}", exc_info=True)
        await _rollback_read_transaction(db)
        raise HTTPException(
            status_code=500,
            detail="Action failed — database rolled back",
        ) from None


# ── END OF actions.py ──
