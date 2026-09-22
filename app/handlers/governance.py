from __future__ import annotations

import html
import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database.models import Nation, NationMemberRole, Proposal, RuleOverride, User, Vote
from app.database.session import async_session
from app.keyboards.inline import (
    governance_confirm_keyboard,
    governance_main_keyboard,
    governance_proposal_list_keyboard,
    governance_revoke_keyboard,
    governance_rule_keyboard,
    governance_vote_keyboard,
)
from app.services.governance_service import (
    create_proposal,
    get_active_proposals,
    get_proposal,
    get_rule_overrides,
    revoke_override,
    vote_on_proposal,
)
from app.services.nation_service import get_user_active_nation_context
from app.services.rules.registry import RULE_REGISTRY
from app.utils.formatting import fmt_amount, fmt_rate, to_fa

logger = logging.getLogger(__name__)

governance_router = Router(name="governance")


async def _send_governance_home(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
                lock=True,
            )
            nation = context[0] if context is not None else None
            role = context[1] if context is not None else None
            is_founder = role == NationMemberRole.FOUNDER.value

    if nation is None:
        await call.answer("⚠️ ابتدا باید عضو یک ملت باشید.", show_alert=True)
        return

    text = (
        f"📜 <b>حکمرانی {html.escape(nation.name)}</b>\n"
        "\n"
        "اینجا می‌تونی قوانین ملت رو تغییر بدی یا به طرح‌های پیشنهادی رأی بدی."
    )
    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=governance_main_keyboard(is_founder),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data == "governance_main")
async def governance_main(call: CallbackQuery) -> None:
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_active")
async def gov_active(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
                lock=True,
            )
            nation = context[0] if context is not None else None
            if nation is None:
                return
            overrides = await get_rule_overrides(session, nation.nation_id)

    lines = [f"📜 <b>قوانین فعال {html.escape(nation.name)}</b>", ""]
    if not overrides:
        lines.append("هیچ قانون خاصی فعال نیست.")
    else:
        for o in overrides:
            rule = RULE_REGISTRY.get(o.rule_key)
            title = rule.title_fa if rule else o.rule_key
            lines.append(f"• <b>{html.escape(title)}</b>: {o.value}")

    if call.message:
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=governance_main_keyboard(),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data == "gov_new")
async def gov_new(call: CallbackQuery) -> None:
    rules = [(k, r.title_fa) for k, r in RULE_REGISTRY.items()]
    if call.message:
        await call.message.edit_text(
            "📝 <b>انتخاب قانون برای تغییر</b>",
            reply_markup=governance_rule_keyboard(rules),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_rule:"))
async def gov_rule_select(call: CallbackQuery, state: FSMContext) -> None:
    rule_key = call.data.split(":")[1]
    await state.update_data(rule_key=rule_key)
    if call.message:
        await call.message.edit_text(
            f"📝 <b>مقدار جدید برای {rule_key} را وارد کن:</b>",
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def gov_rule_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rule_key = data.get("rule_key")
    if not rule_key:
        return
    value = Decimal(message.text)
    await state.update_data(rule_value=value)
    await message.answer(
        f"✅ طرح پیشنهادی: {rule_key} = {value}\nتأیید می‌کنید؟",
        reply_markup=governance_confirm_keyboard(),
    )


@governance_router.callback_query(F.data == "gov_confirm")
async def gov_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rule_key = data.get("rule_key")
    value = data.get("rule_value")
    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
                lock=True,
            )
            nation = context[0] if context is not None else None
            if nation:
                await create_proposal(session, nation.nation_id, call.from_user.id, rule_key, value)
    await call.answer("✅ طرح ثبت شد.", show_alert=True)
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_voting")
async def gov_voting(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
                lock=True,
            )
            nation = context[0] if context is not None else None
            if not nation:
                return
            proposals = await get_active_proposals(session, nation.nation_id)

    if not proposals:
        await call.answer("هیچ رأی‌گیری فعالی وجود ندارد.", show_alert=True)
        return

    if call.message:
        await call.message.edit_text(
            "🗳 <b>رأی‌گیری‌های جاری</b>",
            reply_markup=governance_proposal_list_keyboard(proposals),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_proposal:"))
async def gov_proposal_detail(call: CallbackQuery) -> None:
    proposal_id = int(call.data.split(":")[1])
    async with async_session() as session:
        async with session.begin():
            proposal = await get_proposal(session, proposal_id)
            if not proposal:
                return
            rule = RULE_REGISTRY.get(proposal.rule_key)
            title = rule.title_fa if rule else proposal.rule_key

    text = (
        f"🗳 <b>طرح #{proposal.id}</b>\n"
        f"قانون: {html.escape(title)}\n"
        f"مقدار: {proposal.proposed_value}\n"
        "\n"
        "رأی خود را ثبت کنید:"
    )
    if call.message:
        await call.message.edit_text(
            text,
            reply_markup=governance_vote_keyboard(proposal.id),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_vote:"))
async def gov_vote(call: CallbackQuery) -> None:
    _, proposal_id, choice = call.data.split(":")
    async with async_session() as session:
        async with session.begin():
            await vote_on_proposal(session, int(proposal_id), call.from_user.id, choice)
    await call.answer("✅ رأی شما ثبت شد.", show_alert=True)
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_revoke_list")
async def gov_revoke_list(call: CallbackQuery) -> None:
    async with async_session() as session:
        async with session.begin():
            context = await get_user_active_nation_context(
                session,
                call.from_user.id,
                repair=True,
                lock=True,
            )
            nation = context[0] if context is not None else None
            if not nation:
                return
            overrides = await get_rule_overrides(session, nation.nation_id)

    if call.message:
        await call.message.edit_text(
            "👑 <b>لغو فوری قانون</b>",
            reply_markup=governance_revoke_keyboard(overrides),
            parse_mode="HTML",
        )
    await call.answer()


@governance_router.callback_query(F.data.startswith("gov_revoke:"))
async def gov_revoke(call: CallbackQuery) -> None:
    override_id = int(call.data.split(":")[1])
    async with async_session() as session:
        async with session.begin():
            await revoke_override(session, override_id)
    await call.answer("✅ قانون لغو شد.", show_alert=True)
    await _send_governance_home(call)


@governance_router.callback_query(F.data == "gov_cancel")
async def gov_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _send_governance_home(call)
