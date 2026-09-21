from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CurrencyHolding, Nation, NationMember, User
from app.services.nation_service import get_user_active_nation_context
from app.diagnostics.support_telemetry import get_recent_telemetry
from app.services.support.models import (
    RepairAction,
    SupportCategory,
    SupportCheck,
    SupportDiagnosis,
)


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(value.casefold() in normalized for value in values)


def _repairable_state(state: str | None) -> bool:
    if not state:
        return False
    return state.startswith("MarketStates:") or state.startswith("SettingsStates:")


def _error_text(errors: list[dict[str, Any]]) -> str | None:
    if not errors:
        return None
    latest = errors[-1]
    error_type = str(latest.get("error_type") or "UnknownError")
    message = str(latest.get("message") or "").strip()
    return f"{error_type}: {message}".strip(": ")


async def diagnose_support_issue(
    session: AsyncSession,
    *,
    user_id: int,
    report: str,
    previous_fsm_state: str | None,
) -> SupportDiagnosis:
    clean_report = " ".join((report or "").split())[:1200]
    telemetry = await get_recent_telemetry(user_id)
    recent_events = telemetry["events"]
    recent_errors = telemetry["errors"]
    latest_error = _error_text(recent_errors)

    user = await session.get(User, user_id, with_for_update=True)
    nation_context = (
        await get_user_active_nation_context(
            session,
            user_id,
            repair=True,
            lock=True,
        )
        if user is not None
        else None
    )
    nation = nation_context[0] if nation_context is not None else None
    membership = None
    if nation is not None:
        membership = await session.scalar(
            select(NationMember)
            .where(
                NationMember.user_id == user_id,
                NationMember.nation_id == nation.nation_id,
                NationMember.is_active.is_(True),
            )
            .limit(1)
        )
    elif user is not None:
        membership = await session.scalar(
            select(NationMember)
            .where(
                NationMember.user_id == user_id,
                NationMember.is_active.is_(True),
            )
            .order_by(NationMember.joined_at.desc(), NationMember.id.asc())
            .limit(1)
        )

    holding = (
        await session.scalar(
            select(CurrencyHolding).where(
                CurrencyHolding.user_id == user_id,
                CurrencyHolding.nation_id == nation.nation_id,
            )
        )
        if user is not None and nation is not None
        else None
    )

    market_requested = _contains_any(
        clean_report,
        (
            "بازار",
            "market",
            "ارز",
            "خرید",
            "فروش",
            "باز نمیشه",
            "باز نمی",
            "خطا میده",
            "خطا می‌ده",
        ),
    )

    home_nation_ok = user is not None and nation is not None and bool(nation.is_active)
    membership_ok = membership is not None and bool(membership.is_active)
    home_membership_sync = (
        user is None
        or membership is None
        or user.home_nation_id == membership.nation_id
    )
    checks = (
        SupportCheck(
            "user",
            user is not None,
            "حساب کاربر موجود است." if user else "حساب کاربر پیدا نشد.",
        ),
        SupportCheck(
            "home_nation",
            home_nation_ok,
            "ملت اصلی فعال است." if home_nation_ok else "ملت اصلی فعال پیدا نشد.",
        ),
        SupportCheck(
            "currency_holding",
            holding is not None,
            "دارایی ارزی کاربر موجود است." if holding is not None else "دارایی ارزی این ملت پیدا نشد.",
        ),
        SupportCheck(
            "active_membership",
            membership_ok,
            "عضویت فعال وجود دارد." if membership_ok else "عضویت فعال پیدا نشد.",
        ),
        SupportCheck(
            "home_membership_sync",
            home_membership_sync,
            (
                "ملت اصلی حساب با عضویت فعال هماهنگ است."
                if home_membership_sync
                else "ملت اصلی حساب با عضویت فعال ناهماهنگ است."
            ),
        ),
    )

    evidence: list[str] = []
    if recent_events:
        evidence.append(f"آخرین مسیر ثبت‌شده: {recent_events[-1].get('event', 'نامشخص')}")
    if latest_error:
        evidence.append(f"آخرین خطای ثبت‌شده: {latest_error}")
    if previous_fsm_state:
        evidence.append(f"وضعیت مرحله قبل از پشتیبانی: {previous_fsm_state}")

    repair_action = RepairAction.NONE
    category = SupportCategory.GENERAL
    summary = "مشکل ثبت شد و وضعیت حساب بررسی شد."
    root_cause = "علت قطعی از داده‌های فعلی قابل اثبات نیست."
    confidence = 0.52
    code_fix_required = False
    market_retry = False

    membership_mismatch = (
        user is not None
        and membership is not None
        and user.home_nation_id != membership.nation_id
    )

    if membership_mismatch:
        category = SupportCategory.DATA_INTEGRITY
        summary = "بین ملت اصلی حساب و عضویت فعال ناهماهنگی وجود دارد."
        root_cause = "شناسه ملت اصلی با عضویت فعال کاربر یکی نیست."
        confidence = 0.99
        repair_action = RepairAction.SYNC_HOME_NATION
    elif user is None or nation is None:
        category = SupportCategory.ACCOUNT
        summary = "اطلاعات پایه حساب یا ملت فعال ناقص است."
        root_cause = "کاربر یا ملت اصلی فعال در پایگاه داده پیدا نشد."
        confidence = 0.97
    elif holding is None and market_requested:
        category = SupportCategory.DATA_INTEGRITY
        summary = "حساب بازار دارایی لازم برای ملت اصلی را ندارد."
        root_cause = "رکورد CurrencyHolding برای ملت اصلی موجود نیست."
        confidence = 0.94
    elif market_requested and _repairable_state(previous_fsm_state):
        category = SupportCategory.FSM
        summary = "وضعیت مرحله‌ای قبلی بازار یا تنظیمات گیر کرده است."
        root_cause = f"FSM روی مرحله «{previous_fsm_state}» باقی مانده بود."
        confidence = 0.93
        repair_action = RepairAction.RESET_FSM
        market_retry = True
    elif latest_error:
        category = SupportCategory.MARKET if market_requested else SupportCategory.RUNTIME
        summary = "خطای واقعی اخیر در مسیر کاربر ثبت شده است."
        root_cause = latest_error
        confidence = 0.88
        code_fix_required = True
        market_retry = market_requested
    elif market_requested:
        category = SupportCategory.MARKET
        summary = "پیش‌شرط‌های پایگاه داده بازار سالم هستند."
        root_cause = "خطای قابل استناد در تله‌متری اخیر ثبت نشده است."
        confidence = 0.67
        market_retry = True

    return SupportDiagnosis(
        category=category,
        summary=summary,
        root_cause=root_cause,
        confidence=confidence,
        checks=checks,
        evidence=tuple(evidence),
        repair_action=repair_action,
        code_fix_required=code_fix_required,
        market_retry=market_retry,
    )


__all__ = ["diagnose_support_issue"]
