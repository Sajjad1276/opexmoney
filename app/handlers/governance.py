from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database.models import Proposal, User, Vote
from app.database.session import async_session
from app.keyboards.inline import (
    governance_confirm_keyboard,
    governance_history_keyboard,
    governance_main_keyboard,
    governance_proposal_list_keyboard,
    governance_revoke_keyboard,
    governance_rule_keyboard,
    governance_vote_keyboard,
)
from app.services.governance_service import (
    cast_vote,
    create_proposal,
    get_active_overrides,
    get_governance_history,
    is_proposer_eligible,
    revoke_override,
)
from app.services.rules.registry import RULE_REGISTRY, get_rule, parse_rule_input
from app.services.nation_service import get_user_active_nation_context
from app.services.rules.resolver import resolve
from app.states.governance import GovernanceStates
from app.utils.formatting import to_fa
from app.utils.ui import close_inline_panel, remember_inline_panel, send_submenu_panel


governance_router = Router(name="governance")


def _format_value(rule, value) -> str:
    if rule.value_type == "percent":
        return f"{to_fa(str(value))}%"
    if rule.value_type == "bool":
        return "روشن" if value else "خاموش"
    return to_fa(str(value))


def _remaining(until: datetime | None) -> str:
    if until is None:
        return "بدون پایان"
    seconds = max(0, int((until - datetime.utcnow()).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{to_fa(hours)} ساعت"
    return f"{to_fa(minutes)} دقیقه"


async def _send_governance_home(call: CallbackQuery | None, message: Message | None = None):
    user_id = call.from_user.id if call else message.from_user.id
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, user_id)
    if user is None:
        text = "🔴 حساب پیدا نشد. /start بزن."
        if call:
            await call.answer(text, show_alert=True)
        else:
            await message.answer(text)
        return

    text = (
        "📜 <b>قانون اساسی OPEX</b>\n"
        "<blockquote>⁠</blockquote>\n"
        "اینجا قوانین اقتصادی ملت‌ها دیده، پیشنهاد و تصویب میشن.\n\n"
        "هر طرح وارد رأی‌گیری میشه و فقط بعد از عبور از حداقل مشارکت و رأی موافق فعال میشه."
    )
    markup = governance_main_keyboard(user.role == "founder")
    if call:
        if call.message:
            await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await call.answer()
    else:
        await send_submenu_panel(
            message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


@governance_router.message(F.text == "📜 قوانین")
async def governance_menu_entry(message: Message):
    await _send_governance_home(None, message)

@governance_router.callback_query(F.data == "governance_main")
async def governance_main(call: CallbackQuery):
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_active")
async def governance_active(call: CallbackQuery):
    async with async_session() as session:
        async with session.begin():
            overrides = await get_active_overrides(session)
            user = await session.get(User, call.from_user.id)

    lines = ["📜 <b>قوانین فعال</b>", "<blockquote>⁠</blockquote>"]
    if not overrides:
        lines.append("فعلاً قانون ویژه‌ای فعال نیست.\nمقدار پایه رجیستری اجرا میشه.")
    for override in overrides[:12]:
        rule = get_rule(override.rule_key)
        scope_text = {
            "global": "جهانی",
            "nation": "ملت",
            "player": "بازیکن",
        }.get(override.scope, override.scope)
        lines.append(
            f"• <b>{html.escape(rule.title_fa)}</b>\n"
            f"  مقدار: <b>{html.escape(_format_value(rule, override.value))}</b> · "
            f"{scope_text}\n"
            f"  ⏳ {html.escape(_remaining(override.active_until))}"
        )
    if len(overrides) > 12:
        lines.append(f"\n... و {to_fa(len(overrides) - 12)} قانون دیگر")

    await call.message.edit_text(
        "\n".join(lines),
        reply_markup=governance_main_keyboard(user.role == "founder"),
        parse_mode="HTML",
    )
    await call.answer()


@governance_router.callback_query(F.data == "gov_new")
async def governance_new(call: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        async with session.begin():
            eligible = await is_proposer_eligible(session, call.from_user.id)
            user = await session.get(User, call.from_user.id)

    if not user or not eligible:
        await call.answer(
            "⚠️ فعلاً شرایط ثبت طرح قانون رو نداری.",
            show_alert=True,
        )
        return

    await state.clear()
    await call.message.edit_text(
        "📝 <b>ثبت طرح جدید</b>\n"
        "<blockquote>⁠</blockquote>\n"
        "یک قانون از فهرست مجاز انتخاب کن.\n"
        "هیچ قانون خارج از این فهرست قابل ثبت نیست.",
        reply_markup=governance_rule_keyboard(
            [(key, definition.title_fa) for key, definition in RULE_REGISTRY.items()]
        ),
        parse_mode="HTML",
    )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_rule:"))
async def governance_select_rule(call: CallbackQuery, state: FSMContext):
    key = call.data.removeprefix("gov_rule:")
    if key not in RULE_REGISTRY:
        await call.answer("⚠️ این قانون مجاز نیست.", show_alert=True)
        return

    rule = RULE_REGISTRY[key]
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            context = (
                await get_user_active_nation_context(
                    session,
                    call.from_user.id,
                    repair=False,
                    lock=False,
                )
                if user is not None
                else None
            )
            if user is None or context is None or not await is_proposer_eligible(session, user.user_id):
                await call.answer("⚠️ شرایط ثبت طرح رو نداری.", show_alert=True)
                return
            active_nation, _role, _source = context
            current = await resolve(
                session,
                key,
                nation_id=active_nation.nation_id,
                player_id=user.user_id,
            )

    await state.set_state(GovernanceStates.WAITING_VALUE)
    await state.update_data(rule_key=key)

    range_text = (
        f"بین {html.escape(_format_value(rule, rule.min_value))} "
        f"تا {html.escape(_format_value(rule, rule.max_value))}"
    )
    scope_text = "برای ملت خودت" if rule.target_scope == "nation" else "در سطح جهانی"

    await call.message.edit_text(
        f"⚙️ <b>{html.escape(rule.title_fa)}</b>\n"
        f"مقدار فعلی: <b>{html.escape(_format_value(rule, current))}</b>\n"
        f"بازه امن: <b>{range_text}</b>\n"
        f"دامنه: {scope_text}\n\n"
        "مقدار پیشنهادی رو به عدد بفرست.",
        parse_mode="HTML",
    )
    await remember_inline_panel(state, call.message)
    await call.answer()


@governance_router.message(GovernanceStates.WAITING_VALUE, F.text)
async def governance_receive_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("rule_key")
    if not key or key not in RULE_REGISTRY:
        await state.clear()
        await message.answer("⚠️ اطلاعات طرح ناقصه. دوباره از قانون اساسی شروع کن.")
        return

    rule = RULE_REGISTRY[key]
    try:
        parsed = parse_rule_input(message.text or "", rule)
    except (ValueError, ArithmeticError):
        await message.answer(
            "⚠️ مقدار معتبر نیست. دوباره عدد رو در بازه اعلام‌شده وارد کن."
        )
        return

    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, message.from_user.id)
            context = (
                await get_user_active_nation_context(
                    session,
                    message.from_user.id,
                    repair=False,
                    lock=False,
                )
                if user is not None
                else None
            )
            if user is None or context is None or not await is_proposer_eligible(session, user.user_id):
                await state.clear()
                await message.answer("⚠️ دیگه شرایط ثبت این طرح رو نداری.")
                return

            active_nation, _role, _source = context
            target_id = active_nation.nation_id if rule.target_scope == "nation" else None
            current = await resolve(
                session,
                key,
                nation_id=active_nation.nation_id,
                player_id=user.user_id,
            )

    await state.update_data(
        rule_key=key,
        proposed_value=str(parsed),
        target_scope=rule.target_scope,
        target_id=target_id,
        current_value=str(current),
    )
    await state.set_state(GovernanceStates.CONFIRM_PROPOSAL)

    await close_inline_panel(state, message.bot)

    preview_message = await message.answer(
        "📋 <b>پیش‌نمایش طرح</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"قانون: <b>{html.escape(rule.title_fa)}</b>\n"
        f"مقدار فعلی: <b>{html.escape(_format_value(rule, current))}</b>\n"
        f"مقدار پیشنهادی: <b>{html.escape(_format_value(rule, parsed))}</b>\n\n"
        "بعد از ثبت، طرح وارد صف رأی‌گیری میشه.",
        reply_markup=governance_confirm_keyboard(),
        parse_mode="HTML",
    )


    await remember_inline_panel(state, preview_message)

@governance_router.callback_query(
    F.data == "gov_confirm",
    StateFilter(GovernanceStates.CONFIRM_PROPOSAL),
)
async def governance_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required = ("rule_key", "proposed_value", "target_scope")
    if any(key not in data for key in required):
        await state.clear()
        await call.answer("⚠️ اطلاعات طرح ناقصه.", show_alert=True)
        return

    async with async_session() as session:
        async with session.begin():
            try:
                proposal = await create_proposal(
                    session,
                    proposer_player_id=call.from_user.id,
                    rule_key=data["rule_key"],
                    proposed_value=Decimal(data["proposed_value"]),
                    target_scope=data["target_scope"],
                    target_id=data.get("target_id"),
                )
            except ValueError as exc:
                await call.answer(str(exc), show_alert=True)
                return

    await state.clear()
    await call.message.edit_text(
        "✅ <b>طرح ثبت شد.</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"شماره طرح: <b>#{proposal.id}</b>\n"
        "⏱ رأی‌گیری طبق چرخه اقتصادی باز میشه.\n"
        "برای دیدن وضعیت طرح‌ها برو به «رأی‌گیری‌های جاری».",
        reply_markup=governance_main_keyboard(False),
        parse_mode="HTML",
    )
    await call.answer("✅ طرح ثبت شد")


@governance_router.callback_query(F.data == "gov_cancel")
async def governance_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("لغو شد")
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_voting")
async def governance_voting(call: CallbackQuery):
    async with async_session() as session:
        async with session.begin():
            proposals = (
                await session.execute(
                    select(Proposal)
                    .where(Proposal.status == "voting")
                    .order_by(Proposal.voting_closes_at.asc(), Proposal.id.asc())
                    .limit(10)
                )
            ).scalars().all()

    if not proposals:
        text = "🗳 <b>رأی‌گیری‌های جاری</b>\n<blockquote>⁠</blockquote>\nفعلاً رأی‌گیری بازی در جریانی نیست."
        markup = governance_main_keyboard(False)
    else:
        text = (
            "🗳 <b>رأی‌گیری‌های جاری</b>\n"
            "<blockquote>⁠</blockquote>\n"
            "روی هر طرح بزن تا جزئیات و دکمه‌های رأی رو ببینی."
        )
        markup = governance_proposal_list_keyboard(proposals)

    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_proposal:"))
async def governance_proposal(call: CallbackQuery):
    proposal_id = int(call.data.split(":", 1)[1])
    async with async_session() as session:
        async with session.begin():
            proposal = await session.get(Proposal, proposal_id)
            if proposal is None:
                await call.answer("⚠️ طرح پیدا نشد.", show_alert=True)
                return
            rule = get_rule(proposal.rule_key)
            rows = (
                await session.execute(
                    select(Vote.choice, Vote.weight)
                    .where(Vote.proposal_id == proposal.id)
                )
            ).all()

    weights = {
        "for": sum((row[1] for row in rows if row[0] == "for"), Decimal("0")),
        "against": sum((row[1] for row in rows if row[0] == "against"), Decimal("0")),
        "abstain": sum((row[1] for row in rows if row[0] == "abstain"), Decimal("0")),
    }

    if proposal.status == "voting":
        markup = governance_vote_keyboard(proposal.id)
    else:
        markup = governance_main_keyboard(False)

    text = (
        f"📜 <b>طرح #{proposal.id}</b>\n"
        "<blockquote>⁠</blockquote>\n"
        f"قانون: <b>{html.escape(rule.title_fa)}</b>\n"
        f"مقدار پیشنهادی: <b>{html.escape(_format_value(rule, proposal.proposed_value))}</b>\n"
        f"وضعیت: <b>{html.escape({"draft": "پیش‌نویس", "voting": "در حال رأی‌گیری", "passed": "تصویب‌شده", "rejected": "ردشده", "expired": "منقضی‌شده", "active": "فعال", "revoked": "لغوشده"}.get(proposal.status, "نامشخص"))}</b>\n"
        f"⏳ پایان رأی‌گیری: <b>{html.escape(_remaining(proposal.voting_closes_at))}</b>\n\n"
        f"✅ موافق: <b>{to_fa(weights['for'])}</b>\n"
        f"❌ مخالف: <b>{to_fa(weights['against'])}</b>\n"
        f"⚪ ممتنع: <b>{to_fa(weights['abstain'])}</b>"
    )
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_vote:"))
async def governance_vote(call: CallbackQuery):
    _, proposal_id_raw, choice = call.data.split(":", 2)
    try:
        async with async_session() as session:
            async with session.begin():
                vote = await cast_vote(
                    session,
                    proposal_id=int(proposal_id_raw),
                    player_id=call.from_user.id,
                    choice=choice,
                )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    except Exception:
        await call.answer(
            "⚠️ رأی ثبت نشد. چند ثانیه بعد دوباره امتحان کن.",
            show_alert=True,
        )
        return

    await call.answer(f"✅ رأی ثبت شد · وزن {vote.weight}")


@governance_router.callback_query(F.data.startswith("gov_history:"))
async def governance_history(call: CallbackQuery):
    offset = max(0, int(call.data.split(":", 1)[1]))
    async with async_session() as session:
        async with session.begin():
            rows = await get_governance_history(session, offset=offset, limit=9)

    lines = ["📚 <b>تاریخ قوانین</b>", "<blockquote>⁠</blockquote>"]
    if not rows:
        lines.append("هنوز سابقه‌ای ثبت نشده.")
    action_labels = {
        "activate": "فعال‌سازی",
        "replace": "جایگزینی",
        "revoke": "لغو",
        "circuit_breaker": "ترمز ایمنی",
    }
    for ledger, username in rows:
        actor = html.escape(username) if username else "سیستم"
        rule = RULE_REGISTRY.get(ledger.rule_key)
        rule_label = html.escape(rule.title_fa) if rule else html.escape(ledger.rule_key)
        action = action_labels.get(ledger.action, "تغییر")
        lines.append(
            f"• {actor} · <b>{action}</b> · {rule_label}\n"
            f"  {html.escape(ledger.old_value or '—')} → "
            f"{html.escape(ledger.new_value or '—')}"
        )

    await call.message.edit_text(
        "\n".join(lines),
        reply_markup=governance_history_keyboard(
            offset,
            has_next=len(rows) == 9,
        ),
        parse_mode="HTML",
    )
    await call.answer()


@governance_router.callback_query(F.data == "gov_revoke_list")
async def governance_revoke_list(call: CallbackQuery):
    async with async_session() as session:
        async with session.begin():
            user = await session.get(User, call.from_user.id)
            if user is None or user.role != "founder":
                await call.answer(
                    "⚠️ فقط بنیان‌گذار می‌تونه قانون رو فوراً لغو کنه.",
                    show_alert=True,
                )
                return
            overrides = await get_active_overrides(session)

    if not overrides:
        await call.answer("قانون فعالی برای لغو نیست.", show_alert=True)
        return

    await call.message.edit_text(
        "👑 <b>لغو فوری قانون</b>\n"
        "<blockquote>⁠</blockquote>\n"
        "لغو فوری، قانون مربوطه رو از همین لحظه غیرفعال می‌کنه.",
        reply_markup=governance_revoke_keyboard(overrides[:10]),
        parse_mode="HTML",
    )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_revoke:"))
async def governance_revoke(call: CallbackQuery):
    override_id = int(call.data.split(":", 1)[1])
    try:
        async with async_session() as session:
            async with session.begin():
                await revoke_override(
                    session,
                    actor_player_id=call.from_user.id,
                    override_id=override_id,
                )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    except Exception:
        await call.answer(
            "⚠️ لغو قانون انجام نشد. دوباره امتحان کن.",
            show_alert=True,
        )
        return

    await call.answer("✅ قانون لغو شد")
    await _send_governance_home(call)
