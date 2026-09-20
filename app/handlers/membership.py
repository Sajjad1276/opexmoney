from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUpdated

from app.database.session import async_session
from app.services.membership_service import (
    sync_telegram_membership,
    telegram_chat_member_state,
)

logger = logging.getLogger(__name__)

membership_router = Router(name="membership")


@membership_router.chat_member()
async def telegram_chat_member_updated(
    event: ChatMemberUpdated,
    bot: Bot,
) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return

    member = event.new_chat_member
    telegram_user_id = member.user.id
    status, is_member, active = telegram_chat_member_state(member)

    async with async_session() as session:
        async with session.begin():
            result = await sync_telegram_membership(
                session,
                group_id=event.chat.id,
                telegram_user_id=telegram_user_id,
                telegram_status=status,
                is_member=is_member,
                observed_at=getattr(event, "date", None),
                source="telegram:chat_member",
            )

    if result is None:
        return

    logger.info(
        "MEMBERSHIP|telegram_sync|nation=%s|user=%s|status=%s|active=%s|changed=%s|action=%s",
        result.nation_id,
        result.telegram_user_id,
        status,
        active,
        result.changed,
        result.action_type,
    )
