from __future__ import annotations

from enum import StrEnum
from typing import Any

from aiogram import Bot
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KeyboardState
from app.database.session import async_session


class KeyboardKind(StrEnum):
    NONE = "none"
    REPLY = "reply"
    INLINE = "inline"


class KeyboardStateManager:
    """Single gateway for keyboard transitions.

    Handlers must use send_message/edit_message instead of passing reply_markup
    directly to aiogram methods.
    """

    def __init__(self, session_factory=async_session):
        self.session_factory = session_factory

    async def _read(self, chat_id: int) -> KeyboardState | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(KeyboardState).where(KeyboardState.chat_id == chat_id)
            )

    async def _write(
        self,
        chat_id: int,
        *,
        kind: KeyboardKind,
        name: str,
        message_id: int | None,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                current = await session.get(KeyboardState, chat_id)
                if current is None:
                    current = KeyboardState(chat_id=chat_id)
                    session.add(current)
                current.kind = kind.value
                current.name = name
                current.message_id = message_id

    async def _remove_inline(
        self,
        bot: Bot,
        previous: KeyboardState | None,
    ) -> None:
        if not previous or previous.kind != KeyboardKind.INLINE.value:
            return
        if previous.message_id is None:
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=previous.chat_id,
                message_id=previous.message_id,
                reply_markup=None,
            )
        except Exception:
            # A stale inline message must never block the next keyboard.
            return

    async def send_message(
        self,
        bot: Bot,
        *,
        chat_id: int,
        text: str,
        kind: KeyboardKind = KeyboardKind.NONE,
        name: str = "",
        markup: Any = None,
        parse_mode: str | None = None,
        **kwargs: Any,
    ) -> Message:
        previous = await self._read(chat_id)

        if previous and previous.kind == KeyboardKind.REPLY.value and kind != KeyboardKind.REPLY:
            # Reply keyboards cannot be removed by editing a message. Send the
            # actual prompt with ReplyKeyboardRemove, then attach the Inline
            # markup by editing that same message.
            sent = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=parse_mode,
                **kwargs,
            )
            if kind == KeyboardKind.INLINE and markup is not None:
                sent = await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=sent.message_id,
                    reply_markup=markup,
                ) or sent
            await self._write(
                chat_id,
                kind=kind,
                name=name,
                message_id=getattr(sent, "message_id", None),
            )
            return sent

        if previous and previous.kind == KeyboardKind.INLINE.value and (
            kind != KeyboardKind.INLINE or previous.message_id is not None
        ):
            await self._remove_inline(bot, previous)

        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode=parse_mode,
            **kwargs,
        )
        await self._write(
            chat_id,
            kind=kind,
            name=name,
            message_id=getattr(sent, "message_id", None),
        )
        return sent

    async def edit_message(
        self,
        message: Message,
        *,
        text: str,
        kind: KeyboardKind = KeyboardKind.NONE,
        name: str = "",
        markup: Any = None,
        parse_mode: str | None = None,
        **kwargs: Any,
    ) -> Message | None:
        chat_id = message.chat.id
        previous = await self._read(chat_id)

        if previous and previous.kind == KeyboardKind.REPLY.value and kind != KeyboardKind.REPLY:
            try:
                await message.bot.send_message(
                    chat_id=chat_id,
                    text="↪️",
                    reply_markup=ReplyKeyboardRemove(),
                    disable_notification=True,
                )
            except Exception:
                pass

        if previous and previous.kind == KeyboardKind.INLINE.value and (
            kind != KeyboardKind.INLINE or previous.message_id != message.message_id
        ):
            await self._remove_inline(message.bot, previous)

        edited = await message.edit_text(
            text=text,
            reply_markup=markup if kind == KeyboardKind.INLINE else None,
            parse_mode=parse_mode,
            **kwargs,
        )
        await self._write(
            chat_id,
            kind=kind,
            name=name,
            message_id=getattr(edited or message, "message_id", None),
        )
        return edited or message

    async def edit_message_id(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        kind: KeyboardKind = KeyboardKind.NONE,
        name: str = "",
        markup: Any = None,
        parse_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        previous = await self._read(chat_id)
        if previous and previous.kind == KeyboardKind.REPLY.value and kind != KeyboardKind.REPLY:
            await bot.send_message(
                chat_id=chat_id,
                text="↪️",
                reply_markup=ReplyKeyboardRemove(),
                disable_notification=True,
            )
        if previous and previous.kind == KeyboardKind.INLINE.value and (
            kind != KeyboardKind.INLINE or previous.message_id != message_id
        ):
            await self._remove_inline(bot, previous)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup if kind == KeyboardKind.INLINE else None,
            parse_mode=parse_mode,
            **kwargs,
        )
        await self._write(
            chat_id,
            kind=kind,
            name=name,
            message_id=message_id,
        )

    async def edit_caption_id(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        kind: KeyboardKind = KeyboardKind.NONE,
        name: str = "",
        markup: Any = None,
        parse_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        previous = await self._read(chat_id)
        if previous and previous.kind == KeyboardKind.REPLY.value and kind != KeyboardKind.REPLY:
            await bot.send_message(
                chat_id=chat_id,
                text="↪️",
                reply_markup=ReplyKeyboardRemove(),
                disable_notification=True,
            )
        if previous and previous.kind == KeyboardKind.INLINE.value and (
            kind != KeyboardKind.INLINE or previous.message_id != message_id
        ):
            await self._remove_inline(bot, previous)
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            reply_markup=markup if kind == KeyboardKind.INLINE else None,
            parse_mode=parse_mode,
            **kwargs,
        )
        await self._write(
            chat_id,
            kind=kind,
            name=name,
            message_id=message_id,
        )

    async def clear(self, bot: Bot, chat_id: int) -> None:
        previous = await self._read(chat_id)
        if previous and previous.kind == KeyboardKind.INLINE.value:
            await self._remove_inline(bot, previous)
        if previous and previous.kind == KeyboardKind.REPLY.value:
            await bot.send_message(
                chat_id=chat_id,
                text="↪️",
                reply_markup=ReplyKeyboardRemove(),
                disable_notification=True,
            )
        await self._write(
            chat_id,
            kind=KeyboardKind.NONE,
            name="",
            message_id=None,
        )


_keyboard_manager = KeyboardStateManager()


async def set_keyboard(
    bot: Bot,
    chat_id: int,
    kind: KeyboardKind,
    markup: Any = None,
    *,
    name: str = "",
    message_id: int | None = None,
) -> None:
    """Persist keyboard state.

    Use send_message/edit_message for user-visible keyboard transitions.
    """
    current = await _keyboard_manager._read(chat_id)
    if current and current.kind != kind.value:
        if current.kind == KeyboardKind.INLINE.value and current.message_id:
            await _keyboard_manager._remove_inline(bot, current)
        elif current.kind == KeyboardKind.REPLY.value and kind != KeyboardKind.REPLY:
            await bot.send_message(
                chat_id=chat_id,
                text="↪️",
                reply_markup=ReplyKeyboardRemove(),
                disable_notification=True,
            )
    await _keyboard_manager._write(
        chat_id,
        kind=kind,
        name=name,
        message_id=message_id or (current.message_id if current else None),
    )


keyboard_manager = _keyboard_manager
