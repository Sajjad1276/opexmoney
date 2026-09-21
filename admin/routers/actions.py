from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TelegramError

from admin.auth import get_admin_user
from admin.cache import invalidate_prefix
from admin.dependencies import get_db_session, get_redis
from admin.schemas.responses import BroadcastActionResponse, SuccessActionResponse, UpdatedActionResponse
from app.database.models import GovernanceLedger, Nation, NationWar, User
from redis.asyncio import Redis

logger = logging.getLogger("opex.admin.actions")
router = APIRouter(prefix="/api/admin/actions", tags=["admin-actions"])


class BroadcastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML", "Markdown"]


class GiveBonusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] | Literal["all"]
    amount_xr: Decimal = Field(gt=0)
    note: str = Field(min_length=1, max_length=255)


class BanPlayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    reason: str = Field(min_length=1, max_length=255)


class ResetMarketRatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nation_id: int | Literal["all"]
    new_rate: Decimal = Field(gt=0)


class EndWarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    war_id: int
    result: Literal["nation_wins", "opponent_wins", "draw"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    db: AsyncSession,
    actor_player_id: int | None,
    *,
    action: str,
    rule_key: str,
    old_value: str | None,
    new_value: str | None,
    reason: str | None,
) -> None:
    db.add(
        GovernanceLedger(
            at=_now(),
            actor_player_id=actor_player_id,
            action=action,
            rule_key=rule_key,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )
    )


async def _audit_actor(db: AsyncSession, admin_user: int) -> int | None:
    return await db.scalar(
        select(User.user_id).where(User.user_id == admin_user)
    )


def _confirm_or_raise(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")


async def _send_broadcast(user_ids: list[int], message: str, parse_mode: str, bot_token: str) -> dict[str, int]:
    sent = 0
    failed = 0
    bot = Bot(token=bot_token)
    try:
        await bot.initialize()
        for user_id in user_ids:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=parse_mode,
                )
                sent += 1
                await asyncio.sleep(0.04)
            except RetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after))
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode=parse_mode,
                    )
                    sent += 1
                except TelegramError:
                    failed += 1
            except (Forbidden, TelegramError):
                failed += 1
            except Exception:
                failed += 1
                logger.exception("Broadcast failed for user %s", user_id)
    finally:
        await bot.shutdown()
    return {"sent": sent, "failed": failed}


@router.post("/broadcast", response_model=BroadcastActionResponse)
async def broadcast(
    payload: BroadcastRequest,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> BroadcastActionResponse:
    _confirm_or_raise(confirm)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    allowed = await redis.set(
        "admin:rate-limit:broadcast",
        str(admin_user),
        nx=True,
        ex=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Broadcast rate limit exceeded")

    from os import getenv

    bot_token = getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(status_code=503, detail="BOT_TOKEN unavailable")

    user_ids = list(
        (
            await db.execute(
                select(User.user_id).where(
                    User.deleted_at.is_(None),
                    User.is_ai.is_(False),
                )
            )
        ).scalars().all()
    )

    result = await run_in_threadpool(
        _broadcast_sync,
        user_ids,
        payload.message,
        payload.parse_mode,
        bot_token,
    )

    audit_actor = await _audit_actor(db, admin_user)
    _audit(
        db,
        audit_actor,
        action="admin_broadcast",
        rule_key="broadcast",
        old_value=None,
        new_value=f"sent={result['sent']};failed={result['failed']}",
        reason=payload.message[:255],
    )
    await db.commit()
    return BroadcastActionResponse(**result)


def _broadcast_sync(user_ids: list[int], message: str, parse_mode: str, bot_token: str) -> dict[str, int]:
    return asyncio.run(_send_broadcast(user_ids, message, parse_mode, bot_token))


@router.post("/give-bonus", response_model=UpdatedActionResponse)
async def give_bonus(
    payload: GiveBonusRequest,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> UpdatedActionResponse:
    _confirm_or_raise(confirm)

    from decimal import Decimal

    amount_xr = Decimal(str(payload.amount_xr))

    if payload.user_ids == "all":
        result = await db.execute(
            update(User)
            .where(User.deleted_at.is_(None), User.is_ai.is_(False))
            .values(xr_balance=User.xr_balance + amount_xr)
        )
    else:
        unique_ids = sorted(set(payload.user_ids))
        if not unique_ids:
            raise HTTPException(status_code=400, detail="user_ids cannot be empty")
        result = await db.execute(
            update(User)
            .where(User.user_id.in_(unique_ids), User.deleted_at.is_(None))
            .values(xr_balance=User.xr_balance + amount_xr)
        )

    updated = int(result.rowcount or 0)
    _audit(
        db,
        admin_user,
        action="admin_give_bonus",
        rule_key="give_bonus",
        old_value=None,
        new_value=str(amount_xr),
        reason=payload.note,
    )
    await db.commit()
    await invalidate_prefix(redis, "players")
    await invalidate_prefix(redis, "player-detail")
    return UpdatedActionResponse(updated=updated)


@router.post("/ban-player", response_model=SuccessActionResponse)
async def ban_player(
    payload: BanPlayerRequest,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> SuccessActionResponse:
    _confirm_or_raise(confirm)
    user = await db.scalar(select(User).where(User.user_id == payload.user_id))
    if user is None:
        return SuccessActionResponse(success=False)

    old_value = user.deleted_at.isoformat() if user.deleted_at else None
    if user.deleted_at is None:
        user.deleted_at = _now()
    _audit(
        db,
        admin_user,
        action="admin_ban_player",
        rule_key="ban_player",
        old_value=old_value,
        new_value=user.deleted_at.isoformat() if user.deleted_at else None,
        reason=payload.reason,
    )
    await db.commit()
    await invalidate_prefix(redis, "players")
    await invalidate_prefix(redis, "player-detail")
    await invalidate_prefix(redis, "stats-overview")
    await invalidate_prefix(redis, "stats-economy-health")
    return SuccessActionResponse(success=True)


@router.post("/reset-market-rates", response_model=UpdatedActionResponse)
async def reset_market_rates(
    payload: ResetMarketRatesRequest,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> UpdatedActionResponse:
    _confirm_or_raise(confirm)

    if payload.nation_id == "all":
        nations = list(
            (
                await db.execute(
                    select(Nation).where(Nation.is_active.is_(True), Nation.deleted_at.is_(None))
                )
            ).scalars().all()
        )
    else:
        nation = await db.scalar(select(Nation).where(Nation.nation_id == payload.nation_id))
        if nation is None:
            return UpdatedActionResponse(updated=0)
        nations = [nation]

    old_rates = {nation.nation_id: nation.exchange_rate for nation in nations}
    for nation in nations:
        nation.rate_prev = old_rates[nation.nation_id]
        nation.exchange_rate = payload.new_rate

    _audit(
        db,
        admin_user,
        action="admin_reset_market_rates",
        rule_key="reset_market_rates",
        old_value=(
            "mixed"
            if len(nations) != 1
            else str(old_rates[nations[0].nation_id])
        ),
        new_value=str(payload.new_rate),
        reason=str(payload.nation_id),
    )
    await db.commit()
    for prefix in (
        "nations", "nation-detail", "stats-overview", "stats-economy-health",
        "economy-market-states", "economy-rate-history", "economy-behavior",
    ):
        await invalidate_prefix(redis, prefix)
    return UpdatedActionResponse(updated=len(nations))


@router.post("/end-war", response_model=SuccessActionResponse)
async def end_war(
    payload: EndWarRequest,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis | None = Depends(get_redis),
    admin_user: int = Depends(get_admin_user),
) -> SuccessActionResponse:
    _confirm_or_raise(confirm)
    war = await db.scalar(select(NationWar).where(NationWar.id == payload.war_id))
    if war is None or war.status != "active":
        return SuccessActionResponse(success=False)

    from app.database.models import WarStatus

    old_status = str(war.status)
    war.status = WarStatus.DRAW if payload.result == "draw" else WarStatus.ENDED
    war.ended_at = _now()
    _audit(
        db,
        admin_user,
        action="admin_end_war",
        rule_key="end_war",
        old_value=old_status,
        new_value=payload.result,
        reason=f"war_id={payload.war_id}",
    )
    await db.commit()
    for prefix in ("wars", "nations", "nation-detail", "player-detail", "stats-overview"):
        await invalidate_prefix(redis, prefix)
    return SuccessActionResponse(success=True)
