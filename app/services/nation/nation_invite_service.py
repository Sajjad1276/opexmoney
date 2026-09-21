from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, NationJoinRequest, NationMember, NationMemberRole
from app.services.nation.membership_service import (
    MembershipResult,
    _join_nation_internal,
)
from config import settings


INVITE_TTL_SECONDS = 86400
_INVITE_KEY_PREFIX = "invite:"



@dataclass(frozen=True)
class InviteLink:
    token: str
    url: str
    expires_at: datetime


def _invite_key(token: str) -> str:
    return f"{_INVITE_KEY_PREFIX}{token}"


async def create_invite_link(
    session: AsyncSession,
    redis,
    founder_id: int,
    nation_id: int,
    request_id: int | None = None,
) -> InviteLink:
    founder_member = await session.scalar(
        select(NationMember.id)
        .where(
            NationMember.nation_id == nation_id,
            NationMember.user_id == founder_id,
            NationMember.is_active.is_(True),
            NationMember.role == NationMemberRole.FOUNDER,
        )
        .limit(1)
    )
    if founder_member is None:
        raise ValueError("⛔ فقط بنیان‌گذار می‌تواند لینک دعوت بسازد.")

    nation = await session.get(Nation, nation_id)
    if nation is None or not nation.is_active:
        raise ValueError("⚠️ این ملت فعال نیست.")

    token = secrets.token_urlsafe(16)
    expires_at = datetime.utcnow() + timedelta(seconds=INVITE_TTL_SECONDS)
    payload = {
        "nation_id": nation_id,
        "request_id": request_id,
        "founder_id": founder_id,
    }
    await redis.set(
        _invite_key(token),
        json.dumps(payload, separators=(",", ":")),
        ex=INVITE_TTL_SECONDS,
    )
    return InviteLink(
        token=token,
        url=f"https://t.me/{settings.bot_username}?start=inv_{token}",
        expires_at=expires_at,
    )


async def consume_invite_link(
    session: AsyncSession,
    redis,
    token: str,
    user_id: int,
) -> MembershipResult:
    raw = await redis.getdel(_invite_key(token))
    if not raw:
        raise ValueError("⚠️ لینک دعوت نامعتبر یا منقضی شده است.")

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    try:
        payload = json.loads(raw)
        nation_id = int(payload["nation_id"])
        founder_id = int(payload["founder_id"])
        request_id = payload.get("request_id")
        request_id = int(request_id) if request_id is not None else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("⚠️ لینک دعوت خراب یا نامعتبر است.")

    nation = await session.get(Nation, nation_id)
    if nation is None or not nation.is_active:
        raise ValueError("⚠️ این ملت دیگر فعال نیست.")

    result = await _join_nation_internal(
        session,
        user_id,
        nation_id,
        allow_private=True,
        source="invite_link",
    )

    if request_id is not None:
        request = await session.get(NationJoinRequest, request_id, with_for_update=True)
        if request is not None and str(request.status) == "pending":
            request.status = "approved"
            request.resolved_by = founder_id
            request.resolved_at = datetime.utcnow()

    return result
