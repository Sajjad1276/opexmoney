from __future__ import annotations

import html
import json
import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from redis.asyncio import Redis
from sqlalchemy import select

from app.ai.academy_mentor import ask_mentor
from app.database.models import Nation, User, UserLessonProgress
from app.database.session import async_session
from app.services.academy_service import (
    LEVEL_EMOJI,
    MODULE_META,
    clear_ai_session,
    complete_lesson,
    get_ai_session,
    get_lesson,
    get_module_lessons,
    get_module_status,
    get_user_progress,
    get_user_xp,
    open_lesson,
    save_ai_session,
)
from app.utils.formatting import fmt_amount, to_fa

router = Router(name="academy")
logger = logging.getLogger(__name__)

RLM = "\u200f"
ACADEMY_ERROR = "⚠️ آکادمی موقتاً در دسترس نیست. دوباره امتحان کن."


class AcademyStates(StatesGroup):
    QUIZ_IN_PROGRESS = State()
    ASKING_AI = State()


def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text[:64], callback_data=callback_data)


def build_academy_main_msg(user_xp) -> str:
    level = user_xp.level if user_xp.level in LEVEL_EMOJI else "beginner"
    lines = [
        "🎓 <b>OPEX Academy</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"⭐ سطح تو: {LEVEL_EMOJI[level]} {html.escape(level)}",
        f"🏆 XP: {to_fa(user_xp.total_xp)} امتیاز",
        "━━━━━━━━━━━━━━━━━━",
        "📚 <b>ماژول‌های آموزشی:</b>",
        "از دکمه‌های زیر یک مسیر آموزشی را باز کن.",
    ]
    return "\n".join(f"{RLM}{line}" for line in lines)


def academy_keyboard(modules: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for module in modules:
        module_id = module["module_id"]
        if module["locked"]:
            text = f"🔒 ماژول {module_id}: {module['title']}"
        elif module["status"] == "complete":
            text = f"✅ {module['emoji']} {module['title']}"
        else:
            text = (
                f"{module['emoji']} {module['title']} "
                f"({to_fa(module['done_count'])}/{to_fa(module['total_count'])})"
            )
        rows.append([_button(text, f"academy:module:{module_id}")])

    rows.append(
        [
            _button("💬 از اوپکس بپرس", "academy:ask_ai"),
            _button("↩️ بازگشت", "back_to_dashboard"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_module_msg(module_id: int, lessons: list[dict]) -> str:
    meta = MODULE_META[module_id]
    lines = [
        f"📚 <b>ماژول {to_fa(module_id)}: {html.escape(meta['title'])}</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if not lessons:
        lines.append("هنوز در این ماژول درسی ثبت نشده است.")
    else:
        for lesson in lessons:
            icon = {
                "done": "✅",
                "open": "📖",
                "locked": "🔒",
            }.get(lesson["status"], "🔒")
            lines.append(
                f"{icon} درس {to_fa(lesson['order'])}: "
                f"{html.escape(lesson['title_fa'])} "
                f"[+{to_fa(lesson['xp_reward'])} XP]"
            )
    return "\n".join(f"{RLM}{line}" for line in lines)


def module_keyboard(
    module_id: int,
    lessons: list[dict],
) -> InlineKeyboardMarkup:
    rows = []
    for lesson in lessons:
        icon = {
            "done": "✅",
            "open": "📖",
            "locked": "🔒",
        }.get(lesson["status"], "🔒")
        rows.append(
            [
                _button(
                    f"{icon} درس {lesson['order']}: {lesson['title_fa']}",
                    f"academy:lesson:{lesson['lesson_id']}",
                )
            ]
        )
    rows.append([_button("↩️ بازگشت", "academy:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_lesson_msg(lesson) -> str:
    lines = [
        f"📖 <b>درس {to_fa(lesson.module_id)}.{to_fa(lesson.order)}: "
        f"{html.escape(lesson.title_fa)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        lesson.content_fa,
        "━━━━━━━━━━━━━━━━━━",
        f"⭐ جایزه: +{to_fa(lesson.xp_reward)} XP "
        f"💰 +{to_fa(fmt_amount(lesson.xr_reward))} ΩXR",
    ]
    return "\n".join(f"{RLM}{line}" for line in lines)


def lesson_keyboard(
    lesson_id: int,
    module_id: int,
    status: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status != "done":
        rows.append(
            [_button("❓ کوئیز این درس", f"academy:quiz:{lesson_id}:0")]
        )
    rows.append(
        [
            _button("💬 سوال داری؟ بپرس", f"academy:ask:{lesson_id}"),
            _button("↩️ بازگشت", f"academy:module:{module_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_quiz(lesson) -> list[dict]:
    try:
        raw = json.loads(lesson.quiz_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    questions: list[dict] = []
    for question in raw:
        if not isinstance(question, dict):
            continue
        q = question.get("q")
        options = question.get("options")
        answer = question.get("answer")
        if not isinstance(q, str) or not isinstance(options, list):
            continue
        try:
            answer = int(answer)
        except (TypeError, ValueError):
            continue
        if not options or answer < 0 or answer >= len(options):
            continue
        questions.append(
            {
                "q": q,
                "options": [str(option) for option in options],
                "answer": answer,
                "explanation": str(question.get("explanation") or ""),
            }
        )
    return questions


async def _show_question(
    callback: CallbackQuery,
    lesson,
    questions: list[dict],
    q_idx: int,
) -> None:
    question = questions[q_idx]
    total = len(questions)
    text = "\n".join(
        [
            f"{RLM}❓ <b>سوال {to_fa(q_idx + 1)} از {to_fa(total)}</b>",
            f"{RLM}━━━━━━━━━━━━━━━━━━",
            f"{RLM}{html.escape(question['q'])}",
        ]
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    option,
                    f"academy:answer:{lesson.id}:{q_idx}:{index}",
                )
            ]
            for index, option in enumerate(question["options"])
        ]
    )
    if callback.message is None:
        return
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def open_academy(message: Message) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                user_xp = await get_user_xp(session, message.from_user.id)
                modules = await get_module_status(
                    session,
                    message.from_user.id,
                    user_xp.level,
                )
        await message.answer(
            build_academy_main_msg(user_xp),
            reply_markup=academy_keyboard(modules),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception(
            "Failed to open Academy for user=%s",
            message.from_user.id,
        )
        await message.answer(ACADEMY_ERROR, parse_mode=ParseMode.HTML)


async def back_to_academy_main(callback: CallbackQuery) -> None:
    try:
        async with async_session() as session:
            async with session.begin():
                user_xp = await get_user_xp(session, callback.from_user.id)
                modules = await get_module_status(
                    session,
                    callback.from_user.id,
                    user_xp.level,
                )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            build_academy_main_msg(user_xp),
            reply_markup=academy_keyboard(modules),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to return to Academy main for user=%s",
            callback.from_user.id,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def show_module(callback: CallbackQuery) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 3:
            await callback.answer("ماژول نامعتبر است.", show_alert=True)
            return

        module_id = int(parts[2])
        if module_id not in MODULE_META:
            await callback.answer("ماژول نامعتبر است.", show_alert=True)
            return

        async with async_session() as session:
            async with session.begin():
                user_xp = await get_user_xp(session, callback.from_user.id)
                statuses = await get_module_status(
                    session,
                    callback.from_user.id,
                    user_xp.level,
                )
                module_status = next(
                    item for item in statuses if item["module_id"] == module_id
                )
                if module_status["locked"]:
                    await callback.answer(
                        "این ماژول هنوز قفله 🔒",
                        show_alert=True,
                    )
                    return

                lessons = await get_module_lessons(
                    session,
                    callback.from_user.id,
                    module_id,
                )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            build_module_msg(module_id, lessons),
            reply_markup=module_keyboard(module_id, lessons),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to show Academy module user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def show_lesson_handler(callback: CallbackQuery) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 3:
            await callback.answer("درس نامعتبر است.", show_alert=True)
            return

        lesson_id = int(parts[2])
        async with async_session() as session:
            async with session.begin():
                lesson = await get_lesson(session, lesson_id)
                if lesson is None:
                    await callback.answer("این درس پیدا نشد.", show_alert=True)
                    return

                lessons = await get_module_lessons(
                    session,
                    callback.from_user.id,
                    lesson.module_id,
                )
                status_info = next(
                    (item for item in lessons if item["lesson_id"] == lesson_id),
                    None,
                )
                if status_info is None or status_info["status"] == "locked":
                    await callback.answer(
                        "این درس هنوز قفله 🔒",
                        show_alert=True,
                    )
                    return

                progress = await get_user_progress(
                    session,
                    callback.from_user.id,
                    lesson_id,
                )
                if progress is None and status_info["status"] == "open":
                    await open_lesson(
                        session,
                        callback.from_user.id,
                        lesson_id,
                    )
                    status = "open"
                else:
                    status = (
                        progress.status
                        if progress is not None
                        else status_info["status"]
                    )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            build_lesson_msg(lesson),
            reply_markup=lesson_keyboard(
                lesson.id,
                lesson.module_id,
                status,
            ),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to show Academy lesson user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def start_quiz(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 4:
            await callback.answer("کوئیز نامعتبر است.", show_alert=True)
            return

        lesson_id = int(parts[2])
        async with async_session() as session:
            async with session.begin():
                lesson = await get_lesson(session, lesson_id)
                if lesson is None:
                    await callback.answer("این درس پیدا نشد.", show_alert=True)
                    return

                lessons = await get_module_lessons(
                    session,
                    callback.from_user.id,
                    lesson.module_id,
                )
                status_info = next(
                    (item for item in lessons if item["lesson_id"] == lesson_id),
                    None,
                )
                if status_info is None or status_info["status"] == "locked":
                    await callback.answer(
                        "این درس هنوز قفله 🔒",
                        show_alert=True,
                    )
                    return

                if status_info["status"] == "done":
                    await callback.answer(
                        "این درس قبلاً کامل شده است ✅",
                        show_alert=True,
                    )
                    return

                questions = _parse_quiz(lesson)
                if not questions:
                    await callback.answer(
                        "این درس کوئیز نداره.",
                        show_alert=True,
                    )
                    return

                await open_lesson(
                    session,
                    callback.from_user.id,
                    lesson_id,
                )

        await state.set_state(AcademyStates.QUIZ_IN_PROGRESS)
        await state.update_data(
            lesson_id=lesson_id,
            q_idx=0,
            correct=0,
            total=len(questions),
        )

        if callback.message is None:
            await callback.answer()
            return

        await _show_question(callback, lesson, questions, 0)
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to start Academy quiz user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def quiz_next(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 4:
            await callback.answer("کوئیز نامعتبر است.", show_alert=True)
            return

        lesson_id = int(parts[2])
        q_idx = int(parts[3])
        data = await state.get_data()
        if data.get("lesson_id") != lesson_id:
            await callback.answer("این کوئیز دیگر فعال نیست.", show_alert=True)
            return

        async with async_session() as session:
            async with session.begin():
                lesson = await get_lesson(session, lesson_id)
                if lesson is None:
                    await callback.answer("این درس پیدا نشد.", show_alert=True)
                    return
                questions = _parse_quiz(lesson)

        if q_idx >= len(questions):
            await finish_quiz(
                callback,
                state,
                lesson_id,
                int(data.get("correct", 0)),
                int(data.get("total") or len(questions)),
            )
            return

        if callback.message is None:
            await callback.answer()
            return

        await state.update_data(q_idx=q_idx)
        await _show_question(callback, lesson, questions, q_idx)
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to advance Academy quiz user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def handle_answer(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != 5:
            await callback.answer("پاسخ نامعتبر است.", show_alert=True)
            return

        lesson_id = int(parts[2])
        q_idx = int(parts[3])
        choice = int(parts[4])
        data = await state.get_data()

        if data.get("lesson_id") != lesson_id:
            await callback.answer("این کوئیز دیگر فعال نیست.", show_alert=True)
            return
        if int(data.get("q_idx", -1)) != q_idx:
            await callback.answer("این سوال دیگر فعال نیست.", show_alert=True)
            return

        async with async_session() as session:
            async with session.begin():
                lesson = await get_lesson(session, lesson_id)
                if lesson is None:
                    await callback.answer("این درس پیدا نشد.", show_alert=True)
                    return
                questions = _parse_quiz(lesson)

        if not 0 <= q_idx < len(questions):
            await callback.answer("سوال نامعتبر است.", show_alert=True)
            return

        question = questions[q_idx]
        if not 0 <= choice < len(question["options"]):
            await callback.answer("گزینه نامعتبر است.", show_alert=True)
            return

        correct_count = int(data.get("correct", 0))
        is_correct = choice == question["answer"]

        if is_correct:
            correct_count += 1
            await state.update_data(correct=correct_count)
            feedback = f"{RLM}✅ <b>درست!</b>"
            if question["explanation"]:
                feedback += f"\n{RLM}{html.escape(question['explanation'])}"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button(
                            "ادامه ←",
                            f"academy:quiz:{lesson_id}:{q_idx + 1}",
                        )
                    ]
                ]
            )
        else:
            feedback = f"{RLM}❌ <b>اشتباه!</b>"
            if question["explanation"]:
                feedback += f"\n{RLM}{html.escape(question['explanation'])}"
            correct_answer = question["options"][question["answer"]]
            feedback += f"\n{RLM}جواب درست: <b>{html.escape(correct_answer)}</b>"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button(
                            "دوباره امتحان کن",
                            f"academy:quiz:{lesson_id}:{q_idx}",
                        )
                    ]
                ]
            )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            feedback,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        await callback.answer("✅ درست!" if is_correct else "❌ اشتباه!")
    except Exception:
        logger.exception(
            "Failed to handle Academy quiz answer user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def finish_quiz(
    callback: CallbackQuery,
    state: FSMContext,
    lesson_id: int,
    correct: int,
    total: int,
) -> None:
    total = max(1, int(total))
    score = int(int(correct) / total * 100)

    try:
        async with async_session() as session:
            async with session.begin():
                result = await complete_lesson(
                    session,
                    callback.from_user.id,
                    lesson_id,
                    score,
                )

        if not result.get("ok"):
            await state.clear()
            if result.get("reason") == "already_done":
                await callback.answer(
                    "این درس قبلاً کامل شده است ✅",
                    show_alert=True,
                )
            else:
                await callback.answer(
                    "تکمیل درس انجام نشد. دوباره تلاش کن.",
                    show_alert=True,
                )
            return

        await state.clear()

        lines = [
            f"{RLM}🏆 <b>کوئیز تموم شد!</b>",
            f"{RLM}━━━━━━━━━━━━━━━━━━",
            f"{RLM}✅ نتیجه: {to_fa(correct)}/{to_fa(total)} ({to_fa(score)}٪)",
            f"{RLM}⭐ +{to_fa(result['xp_gained'])} XP",
        ]
        if result["xr_gained"] > 0:
            lines.append(
                f"{RLM}💰 +{to_fa(fmt_amount(result['xr_gained']))} ΩXR"
            )
        if result["level_up"]:
            lines.append(
                f"{RLM}🎉 <b>تبریک! به سطح "
                f"{html.escape(result['new_level'])} رسیدی!</b>"
            )
        if result["module_completed"]:
            lines.append(
                f"{RLM}✨ <b>ماژول {to_fa(result['module_id'])} رو کامل کردی!</b>"
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button(
                        "↩️ برگشت به درس",
                        f"academy:lesson:{lesson_id}",
                    )
                ]
            ]
        )

        if callback.message is not None:
            await callback.message.edit_text(
                "\n".join(lines),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to finish Academy quiz user=%s lesson=%s",
            callback.from_user.id,
            lesson_id,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def start_ask_ai(
    callback: CallbackQuery,
    state: FSMContext,
    redis: Redis | None = None,
) -> None:
    try:
        parts = (callback.data or "").split(":")
        lesson_id = None

        if len(parts) == 3 and parts[1] == "ask":
            lesson_id = int(parts[2])
        elif callback.data != "academy:ask_ai":
            await callback.answer("درخواست نامعتبر است.", show_alert=True)
            return

        ai_session = await get_ai_session(redis, callback.from_user.id)
        history = (
            ai_session.get("history", [])
            if ai_session.get("lesson_id") == lesson_id
            else []
        )

        await state.set_state(AcademyStates.ASKING_AI)
        await state.update_data(
            lesson_id=lesson_id,
            ai_history=history,
        )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            "\n".join(
                [
                    f"{RLM}💬 <b>از اوپکس بپرس</b>",
                    f"{RLM}━━━━━━━━━━━━━━━━━━",
                    f"{RLM}سوالت رو بنویس.",
                    f"{RLM}فقط درباره OPEX MONEY جواب میدم!",
                ]
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("❌ انصراف", "academy:cancel_ask")]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to start Academy AI ask user=%s data=%s",
            callback.from_user.id,
            callback.data,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


async def receive_question(
    message: Message,
    state: FSMContext,
    redis: Redis | None = None,
) -> None:
    try:
        question = (message.text or "").strip()
        if not question:
            await message.answer("سوالت رو به صورت متن بفرست.")
            return

        state_data = await state.get_data()
        lesson_id = state_data.get("lesson_id")
        lesson = None

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, message.from_user.id)
                if user is None:
                    await message.answer("ابتدا در بازی ثبت‌نام کن.")
                    return

                user_xp = await get_user_xp(session, message.from_user.id)
                nation_name = None
                if user.home_nation_id is not None:
                    nation_name = await session.scalar(
                        select(Nation.name).where(
                            Nation.nation_id == user.home_nation_id
                        )
                    )
                if lesson_id is not None:
                    lesson = await get_lesson(session, int(lesson_id))

        ai_session = await get_ai_session(redis, message.from_user.id)
        history = (
            ai_session.get("history", [])
            if ai_session.get("lesson_id") == lesson_id
            else state_data.get("ai_history", [])
        )

        thinking = await message.answer(
            "در حال تفکر... 🤔",
            parse_mode=ParseMode.HTML,
        )

        reply = await ask_mentor(
            user_username=user.username or message.from_user.username or "بازیکن",
            user_level=user_xp.level,
            nation_name=nation_name,
            xr_balance=Decimal(str(user.xr_balance or Decimal("0"))),
            lesson_title=(
                lesson.title_fa
                if lesson is not None
                else "راهنمای عمومی OPEX MONEY"
            ),
            lesson_content=(
                lesson.content_fa
                if lesson is not None
                else "راهنمای عمومی بازی، نرخ ارز، معاملات و استراتژی."
            ),
            question=question,
            history=history,
        )

        updated_history = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply},
        ]
        await save_ai_session(
            redis,
            message.from_user.id,
            int(lesson_id) if lesson_id is not None else None,
            updated_history,
        )

        if lesson_id is not None:
            async with async_session() as session:
                async with session.begin():
                    progress = await session.scalar(
                        select(UserLessonProgress)
                        .where(
                            UserLessonProgress.user_id == message.from_user.id,
                            UserLessonProgress.lesson_id == int(lesson_id),
                        )
                        .with_for_update()
                    )
                    if progress is not None:
                        progress.ai_questions_count += 1

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button(
                        "💬 سوال دیگه",
                        f"academy:ask:{lesson_id}"
                        if lesson_id is not None
                        else "academy:ask_ai",
                    )
                ],
                [
                    _button(
                        "↩️ برگشت به درس"
                        if lesson_id is not None
                        else "↩️ برگشت به آکادمی",
                        f"academy:lesson:{lesson_id}"
                        if lesson_id is not None
                        else "academy:main",
                    )
                ],
            ]
        )

        try:
            await thinking.edit_text(
                reply,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            logger.exception(
                "Failed to edit Academy mentor message user=%s",
                message.from_user.id,
            )
            await message.answer(
                reply,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        logger.exception(
            "Failed to receive Academy AI question user=%s",
            message.from_user.id,
        )
        await message.answer(
            "متأسفم، الان نمی‌تونم سوالت رو پردازش کنم. دوباره امتحان کن.",
            parse_mode=ParseMode.HTML,
        )


async def cancel_ask(
    callback: CallbackQuery,
    state: FSMContext,
    redis: Redis | None = None,
) -> None:
    try:
        await state.clear()
        await clear_ai_session(redis, callback.from_user.id)

        async with async_session() as session:
            async with session.begin():
                user_xp = await get_user_xp(session, callback.from_user.id)
                modules = await get_module_status(
                    session,
                    callback.from_user.id,
                    user_xp.level,
                )

        if callback.message is None:
            await callback.answer()
            return

        await callback.message.edit_text(
            build_academy_main_msg(user_xp),
            reply_markup=academy_keyboard(modules),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception:
        logger.exception(
            "Failed to cancel Academy AI user=%s",
            callback.from_user.id,
        )
        await callback.answer(ACADEMY_ERROR, show_alert=True)


router.message.register(open_academy, F.text == "🎓 آکادمی")
router.message.register(receive_question, AcademyStates.ASKING_AI)
router.callback_query.register(
    back_to_academy_main,
    F.data == "academy:main",
)
router.callback_query.register(
    show_module,
    F.data.startswith("academy:module:"),
)
router.callback_query.register(
    show_lesson_handler,
    F.data.startswith("academy:lesson:"),
)
router.callback_query.register(
    start_quiz,
    F.data.startswith("academy:quiz:"),
    ~StateFilter(AcademyStates.QUIZ_IN_PROGRESS),
)
router.callback_query.register(
    quiz_next,
    F.data.startswith("academy:quiz:"),
    StateFilter(AcademyStates.QUIZ_IN_PROGRESS),
)
router.callback_query.register(
    handle_answer,
    F.data.startswith("academy:answer:"),
)
router.callback_query.register(
    start_ask_ai,
    F.data.startswith("academy:ask:") | (F.data == "academy:ask_ai"),
)
router.callback_query.register(
    cancel_ask,
    F.data == "academy:cancel_ask",
)
