from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ButtonStyle
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import text

from app.database.session import async_session
from app.services.nation_service import create_nation
from app.services.user_service import get_user
from app.states.founder import FounderStates
from app.utils.validators import validate_currency_code, validate_nation_name

logger = logging.getLogger(__name__)
founder_router = Router(name="founder")


def _groups_keyboard(groups: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🏛 {group['title'][:45]}",
                callback_data=f"founder_group:{group['group_id']}",
                style=ButtonStyle.PRIMARY,
            )
        ]
        for group in groups
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ انصراف",
                callback_data="cancel_founder",
                style=ButtonStyle.DANGER,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأسیس",
                    callback_data="founder_confirm",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="cancel_founder",
                    style=ButtonStyle.DANGER,
                ),
            ]
        ]
    )


async def _ensure_founder_schema(session) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS bot_groups (
                group_id BIGINT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_nations_currency_code "
            "ON nations(currency_code)"
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_nations_active_group "
            "ON nations(group_id) WHERE is_active = TRUE"
        )
    )


async def _known_groups() -> list[dict]:
    async with async_session() as session:
        await _ensure_founder_schema(session)
        await session.commit()
        result = await session.execute(
            text(
                """
                SELECT group_id, title
                FROM bot_groups
                WHERE is_active = TRUE
                ORDER BY title ASC
                """
            )
        )
        return [dict(row) for row in result.mappings().all()]


async def _register_group(update: ChatMemberUpdated) -> None:
    chat = update.chat
    if chat.type not in {"group", "supergroup"}:
        return

    status = update.new_chat_member.status
    is_active = status in {"member", "administrator"}
    title = chat.title or "گروه بدون نام"
    username = getattr(chat, "username", None)

    async with async_session() as session:
        async with session.begin():
            await _ensure_founder_schema(session)
            if is_active:
                await session.execute(
                    text(
                        """
                        INSERT INTO bot_groups
                            (group_id, title, username, is_active, created_at, updated_at)
                        VALUES
                            (:group_id, :title, :username, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ON CONFLICT (group_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            username = EXCLUDED.username,
                            is_active = TRUE,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "group_id": chat.id,
                        "title": title,
                        "username": username,
                    },
                )
            else:
                await session.execute(
                    text(
                        """
                        UPDATE bot_groups
                        SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                        WHERE group_id = :group_id
                        """
                    ),
                    {"group_id": chat.id},
                )


@founder_router.my_chat_member()
async def track_bot_groups(update: ChatMemberUpdated) -> None:
    try:
        await _register_group(update)
    except Exception:
        logger.exception("Failed to update bot group registry")


async def _shared_groups(bot, user_id: int) -> list[dict]:
    groups = await _known_groups()
    shared: list[dict] = []

    for group in groups:
        try:
            member = await bot.get_chat_member(
                chat_id=group["group_id"],
                user_id=user_id,
            )
            if member.status in {"creator", "administrator", "member"}:
                shared.append(group)
            elif member.status == "restricted" and getattr(member, "is_member", False):
                shared.append(group)
        except (TelegramBadRequest, TelegramForbiddenError):
            continue

    return shared


@founder_router.callback_query(F.data == "start_founder")
async def start_founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()

    async with async_session() as session:
        user = await get_user(session, call.from_user.id)
        if user is None:
            if call.message:
                await call.message.answer("⚠️ اول باید ثبت‌نام کنی.")
            await call.answer()
            return

        if user.role == "founder":
            if call.message:
                await call.message.answer("⚠️ هر معامله‌گر فقط می‌تونه یک ملت بسازه.")
            await call.answer()
            return

        trader_name = user.username

    groups = await _shared_groups(call.bot, call.from_user.id)
    if not groups:
        await state.clear()
        if call.message:
            await call.message.answer("⚠️ ربات در هیچ گروه مشترکی با تو نیست.")
        await call.answer()
        return

    available: list[dict] = []

    async with async_session() as session:
        await _ensure_founder_schema(session)
        for group in groups:
            active = await session.scalar(
                text(
                    """
                    SELECT 1
                    FROM nations
                    WHERE group_id = :group_id AND is_active = TRUE
                    LIMIT 1
                    """
                ),
                {"group_id": group["group_id"]},
            )
            if active is None:
                available.append(group)

    if not available:
        await state.clear()
        if call.message:
            await call.message.answer("⚠️ همه گروه‌های مشترک تو قبلاً ملت دارند.")
        await call.answer()
        return

    await state.update_data(username=trader_name)
    await state.set_state(FounderStates.WAITING_FOR_GROUP)

    if call.message is not None:
        await call.message.edit_text(
            "🏛 <b>چه گروهی رو می‌خوای پایتخت ملتت کنی؟</b>",
            reply_markup=_groups_keyboard(available),
            parse_mode="HTML",
        )

    await call.answer()


@founder_router.callback_query(
    FounderStates.WAITING_FOR_GROUP,
    F.data.startswith("founder_group:"),
)
async def select_group(call: CallbackQuery, state: FSMContext) -> None:
    try:
        group_id = int(call.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await state.clear()
        if call.message:
            await call.message.answer("❌ گروه انتخاب‌شده معتبر نیست.")
        await call.answer()
        return

    groups = await _shared_groups(call.bot, call.from_user.id)
    selected = next(
        (group for group in groups if group["group_id"] == group_id),
        None,
    )

    if selected is None:
        await state.clear()
        if call.message:
            await call.message.answer("⚠️ این گروه دیگه در دسترس نیست.")
        await call.answer()
        return

    async with async_session() as session:
        active = await session.scalar(
            text(
                """
                SELECT 1
                FROM nations
                WHERE group_id = :group_id AND is_active = TRUE
                LIMIT 1
                """
            ),
            {"group_id": group_id},
        )

    if active is not None:
        await state.clear()
        if call.message:
            await call.message.answer("⚠️ این گروه قبلاً پایتخت یک ملت شده.")
        await call.answer()
        return

    await state.update_data(
        group_id=group_id,
        group_name=selected["title"],
    )
    await state.set_state(FounderStates.SET_NATION_NAME)

    if call.message is not None:
        await call.message.edit_text(
            "🏛 <b>اسم ملتت رو بنویس.</b>\n\n"
            "حداکثر ۲۰ کاراکتر؛ فارسی، انگلیسی، فاصله یا خط تیره.",
            parse_mode="HTML",
        )
    await call.answer()


@founder_router.message(FounderStates.SET_NATION_NAME, F.text)
async def set_nation_name(message: Message, state: FSMContext) -> None:
    valid, error = validate_nation_name(message.text or "")
    if not valid:
        await message.answer(error)
        return

    await state.update_data(nation_name=(message.text or "").strip())
    await state.set_state(FounderStates.SET_CURRENCY_CODE)

    await message.answer(
        "💱 <b>کد ارزت رو بنویس.</b>\n\n"
        "۳ حرف لاتین بزرگ؛ مثل IRN یا PRS.",
        parse_mode="HTML",
    )


@founder_router.message(FounderStates.SET_CURRENCY_CODE, F.text)
async def set_currency_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    valid, error = validate_currency_code(code)

    if not valid:
        await message.answer(error)
        return

    async with async_session() as session:
        exists = await session.scalar(
            text(
                "SELECT 1 FROM nations WHERE currency_code = :code LIMIT 1"
            ),
            {"code": code},
        )

    if exists is not None:
        await message.answer(
            "⚠️ این کد ارز قبلاً استفاده شده. یک کد دیگه انتخاب کن."
        )
        return

    data = await state.get_data()
    await state.update_data(currency_code=code)
    await state.set_state(FounderStates.CONFIRM_CREATE)

    await message.answer(
        "🏛 <b>ملت آماده تأسیسه.</b>\n"
        f"🏛 ملت: {html.escape(data['nation_name'])}\n"
        f"💱 ارز: {html.escape(code)}\n"
        f"🗺 پایتخت: {html.escape(data['group_name'])}\n"
        f"👑 بنیان‌گذار: {html.escape(data.get('username') or message.from_user.first_name or 'معامله‌گر')}",
        reply_markup=_confirm_keyboard(),
        parse_mode="HTML",
    )


@founder_router.callback_query(
    FounderStates.CONFIRM_CREATE,
    F.data == "founder_confirm",
)
async def confirm_create(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    required = {"group_id", "group_name", "nation_name", "currency_code"}

    if not required.issubset(data):
        await state.clear()
        if call.message:
            await call.message.answer("⚠️ فرآیند تأسیس منقضی شد.")
        await call.answer()
        return

    async with async_session() as session:
        try:
            nation = await create_nation(
                session=session,
                founder_user_id=call.from_user.id,
                group_id=int(data["group_id"]),
                nation_name=str(data["nation_name"]),
                currency_code=str(data["currency_code"]),
            )
        except ValueError as exc:
            await state.clear()
            if call.message:
                await call.message.answer(f"⚠️ {exc}")
            await call.answer()
            return
        except Exception:
            await state.clear()
            logger.exception("Nation creation failed")
            if call.message:
                await call.message.answer("❌ تأسیس ملت انجام نشد. دوباره تلاش کن.")
            await call.answer()
            return

    await state.clear()

    if call.message is not None:
        await call.message.edit_text(
            f"🎉 <b>ملت {html.escape(nation.name)} رسماً تأسیس شد!</b>\n"
            "👑 تو بنیان‌گذار این ملتی.\n"
            f"💰 موجودی اولیه: ۱۰۰۰ {html.escape(nation.currency_code)}\n"
            f"🌐 ارز ملت: {html.escape(nation.currency_code)}\n"
            "پنل ملت آماده مدیریت اقتصاده.",
            parse_mode="HTML",
        )

    try:
        await call.bot.send_message(
            chat_id=nation.group_id,
            text=(
                f"🏛 <b>ملت {html.escape(nation.name)} تأسیس شد!</b>\n"
                f"💱 ارز رسمی: {html.escape(nation.currency_code)}\n"
                f"👑 بنیان‌گذار: {html.escape(str(data.get('username') or call.from_user.first_name or 'معامله‌گر'))}\n"
                "📈 نرخ اولیه: ۱.۰۰ ΩXR\n"
                "برای پیوستن، ربات رو اجرا کن."
            ),
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.exception(
            "Nation created but group announcement failed: %s",
            nation.group_id,
        )

    await call.answer()


@founder_router.callback_query(F.data == "cancel_founder")
async def cancel_founder(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()

    if call.message is not None:
        try:
            await call.message.edit_text(
                "❌ تأسیس ملت لغو شد.\n\nهر وقت خواستی دوباره شروع کن.",
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass

    await call.answer()
