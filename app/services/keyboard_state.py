from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select

from app.database.models import KeyboardState
from app.database.session import async_session


@dataclass(frozen=True)
class KeyboardSnapshot:
    player_id: int
    chat_id: int
    kind: str
    message_id: int | None


class KeyboardStateManager:
    """Single gateway for user keyboard transitions."""

    async def get(self, chat_id: int) -> KeyboardSnapshot:
        async with async_session() as session:
            row = await session.get(KeyboardState, chat_id)
            if row is None:
                return KeyboardSnapshot(chat_id, chat_id, "none", None)
            return KeyboardSnapshot(row.player_id, row.chat_id, row.kind, row.message_id)

    async def set_keyboard(
        self,
        bot: Bot,
        chat_id: int,
        kind: str,
        markup: Any | None,
    ) -> KeyboardSnapshot:
        """Remove incompatible old keyboard state and persist an empty transition."""
        async with async_session() as session:
            async with session.begin():
                row = await session.get(KeyboardState, chat_id)
                old_kind = row.kind if row else "none"
                old_message_id = row.message_id if row else None

                if old_kind.startswith("inline:") and old_message_id:
                    try:
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=old_message_id,
                            reply_markup=None,
                        )
                    except Exception:
                        pass

                if old_kind.startswith("reply:") and kind.startswith("inline:"):
                    try:
                        removed = await bot.send_message(
                            chat_id=chat_id,
                            text="\u2063",
                            reply_markup=ReplyKeyboardRemove(),
                        )
                        if getattr(removed, "message_id", None):
                            try:
                                await bot.delete_message(chat_id, removed.message_id)
                            except Exception:
                                pass
                    except Exception:
                        pass

                if row is None:
                    row = KeyboardState(
                        player_id=chat_id,
                        chat_id=chat_id,
                        kind="none",
                        message_id=None,
                    )
                    session.add(row)
                row.kind = "none"
                row.message_id = None
                await session.flush()

        return KeyboardSnapshot(chat_id, chat_id, "none", None)

    async def record(self, chat_id: int, kind: str, message_id: int | None) -> None:
        async with async_session() as session:
            async with session.begin():
                row = await session.get(KeyboardState, chat_id)
                if row is None:
                    row = KeyboardState(player_id=chat_id, chat_id=chat_id, kind=kind)
                    session.add(row)
                row.kind = kind
                row.message_id = message_id
                await session.flush()

    async def send(
        self,
        message: Message,
        text: str,
        *,
        kind: str,
        markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> Message:
        await self.set_keyboard(message.bot, message.chat.id, kind, markup)
        sent = await message.answer(text, reply_markup=markup, parse_mode=parse_mode)
        await self.record(message.chat.id, kind, getattr(sent, "message_id", None))
        return sent

    async def send_to_chat(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        *,
        kind: str,
        markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> Any:
        await self.set_keyboard(bot, chat_id, kind, markup)
        sent = await bot.send_message(chat_id, text, reply_markup=markup, parse_mode=parse_mode)
        await self.record(chat_id, kind, getattr(sent, "message_id", None))
        return sent

    async def edit_inline(
        self,
        message: Message,
        text: str,
        *,
        kind: str,
        markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> Message:
        await self.set_keyboard(message.bot, message.chat.id, kind, markup)
        edited = await message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
        await self.record(message.chat.id, kind, getattr(edited, "message_id", None) or message.message_id)
        return edited

    async def edit_caption_inline(
        self,
        message: Message,
        caption: str,
        *,
        kind: str,
        markup: Any | None = None,
        parse_mode: str | None = None,
    ) -> Message:
        await self.set_keyboard(message.bot, message.chat.id, kind, markup)
        edited = await message.edit_caption(caption=caption, reply_markup=markup, parse_mode=parse_mode)
        await self.record(message.chat.id, kind, getattr(edited, "message_id", None) or message.message_id)
        return edited

    async def send_ephemeral_inline(
        self,
        message: Message,
        text: str,
        markup: Any,
        *,
        parse_mode: str | None = None,
    ) -> Message:
        """Send an inline confirmation without changing the persistent wizard keyboard."""
        return await message.answer(text, reply_markup=markup, parse_mode=parse_mode)


keyboard_manager = KeyboardStateManager()


async def set_keyboard(bot: Bot, chat_id: int, kind: str, markup: Any | None) -> None:
    """Compatibility helper required by the Phase 4 keyboard contract."""
    await keyboard_manager.set_keyboard(bot, chat_id, kind, markup)
