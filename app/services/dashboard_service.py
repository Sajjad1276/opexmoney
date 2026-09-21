from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import html

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    CurrencyHolding,
    Nation,
    NationMember,
    NationMemberHistory,
    NationWar,
    RateHistory,
    User,
)
from app.services.market_intelligence import get_market_overview, get_risk_label
from app.services.nation_service import get_user_active_nation_context
from app.utils.formatting import fmt_amount, fmt_pct, fmt_rate, to_fa


@dataclass(frozen=True)
class LiveDashboard:
    has_nation: bool
    nation_name: str | None
    nation_flag: str | None
    currency_code: str | None
    rate: Decimal | None
    change_24h: Decimal | None
    local_balance: Decimal
    dollar_balance: Decimal
    national_rank: int | None
    event_title: str
    event_detail: str
    suggestion: str
    growth_status: str
    pressure_status: str
    risk_status: str


async def _resolve_verified_nation(
    session: AsyncSession,
    user: User,
) -> Nation | None:
    """Resolve the active nation from the canonical game membership invariant.

    User.home_nation_id and an active NationMember are authoritative.
    NationTelegramMember is only a Telegram-state projection and must not
    block valid nation access when that projection is absent.
    """
    context = await get_user_active_nation_context(
        session,
        user.user_id,
        repair=False,
        lock=False,
    )
    return context[0] if context is not None else None

async def _national_rank(
    session: AsyncSession,
    *,
    user_id: int,
    nation: Nation,
    holding_amount: Decimal,
) -> int:
    stmt = (
        select(func.count(CurrencyHolding.id))
        .join(
            NationMember,
            and_(
                NationMember.nation_id == CurrencyHolding.nation_id,
                NationMember.user_id == CurrencyHolding.user_id,
                NationMember.is_active.is_(True),
            ),
        )
        .where(
            CurrencyHolding.nation_id == nation.nation_id,
            CurrencyHolding.amount > holding_amount,
        )
    )

    higher = int(await session.scalar(stmt) or 0)
    return higher + 1

async def _growth_status(session: AsyncSession, nation: Nation, now: datetime) -> str:
    if nation.is_ai:
        return "پایدار"

    old = await session.scalar(
        select(NationMemberHistory.member_count)
        .where(
            NationMemberHistory.nation_id == nation.nation_id,
            NationMemberHistory.recorded_at <= now - timedelta(days=7),
        )
        .order_by(NationMemberHistory.recorded_at.desc())
        .limit(1)
    )
    if old in (None, 0):
        return "در حال شکل‌گیری"

    current = max(0, int(nation.member_count or 0))
    pct = (Decimal(current - int(old)) / Decimal(int(old))) * Decimal("100")
    if pct >= 10:
        return f"رشد +{to_fa(int(pct))}٪"
    if pct <= -10:
        return f"کاهش {to_fa(abs(int(pct)))}٪"
    return "پایدار"


async def _risk_status(
    session: AsyncSession,
    nation: Nation,
    now: datetime,
) -> str:
    rows = (
        await session.execute(
            select(RateHistory.rate)
            .where(
                RateHistory.nation_id == nation.nation_id,
                RateHistory.calculated_at >= now - timedelta(hours=24),
            )
            .order_by(RateHistory.calculated_at.asc())
        )
    ).scalars().all()
    rates = [float(value) for value in rows]
    if not rates:
        rates = [float(nation.exchange_rate)]
    return str(get_risk_label(rates))


async def build_live_dashboard(
    session: AsyncSession,
    user_id: int,
) -> LiveDashboard:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("کاربر پیدا نشد.")

    nation = await _resolve_verified_nation(session, user)
    dollar_balance = Decimal(str(user.xr_balance or Decimal("0")))

    overview = await get_market_overview(
        session,
        nation.nation_id if nation is not None else None,
        limit=8,
    )

    if nation is None:
        winner_code, winner_change = overview["top_mover"]["winner"]
        if winner_code:
            event_title = "مهم‌ترین حرکت بازار"
            event_detail = (
                f"{winner_code} در ۲۴ ساعت گذشته "
                f"{fmt_pct(winner_change)} تغییر کرده است."
            )
            suggestion = (
                f"حرکت {winner_code} را بررسی کن و قبل از معامله، دلیل تغییرش را ببین."
            )
        else:
            event_title = "بازار"
            event_detail = "بازار هنوز حرکت برجسته‌ای ثبت نکرده است."
            suggestion = "یک ارز را انتخاب کن و وضعیت بازار را بررسی کن."

        return LiveDashboard(
            has_nation=False,
            nation_name=None,
            nation_flag=None,
            currency_code=None,
            rate=None,
            change_24h=None,
            local_balance=Decimal("0"),
            dollar_balance=dollar_balance,
            national_rank=None,
            event_title=event_title,
            event_detail=event_detail,
            suggestion=suggestion,
            growth_status="بدون ملت",
            pressure_status=str(overview["mood"]).replace("بازار امروز ", ""),
            risk_status="فقط بازار آزاد",
        )

    holding = await session.scalar(
        select(CurrencyHolding)
        .where(
            CurrencyHolding.user_id == user_id,
            CurrencyHolding.nation_id == nation.nation_id,
        )
        .limit(1)
    )
    local_balance = Decimal(str(holding.amount if holding is not None else 0))
    national_rank = await _national_rank(
        session,
        user_id=user_id,
        nation=nation,
        holding_amount=local_balance,
    )

    base = Decimal(str(nation.rate_24h_open or nation.exchange_rate or 0))
    current = Decimal(str(nation.exchange_rate or 0))
    change = (
        ((current - base) / base) * Decimal("100")
        if base > 0
        else Decimal("0")
    )

    active_war = await session.scalar(
        select(NationWar)
        .where(
            NationWar.status == "active",
            NationWar.ends_at > datetime.utcnow(),
            or_(
                NationWar.nation_id == nation.nation_id,
                NationWar.opponent_nation_id == nation.nation_id,
            ),
        )
        .order_by(NationWar.ends_at.asc())
        .limit(1)
    )

    if active_war is not None:
        opponent_id = (
            active_war.opponent_nation_id
            if active_war.nation_id == nation.nation_id
            else active_war.nation_id
        )
        opponent = await session.get(Nation, opponent_id)
        event_title = "جنگ اقتصادی در جریان است"
        event_detail = (
            f"ملت {html.escape(opponent.name) if opponent is not None else 'رقیب'} "
            f"تا {active_war.ends_at.strftime('%H:%M')} در وضعیت جنگی است."
        )
    elif abs(change) >= Decimal("3"):
        direction = "صعود" if change > 0 else "فشار فروش"
        event_title = "حرکت مهم ارز ملت"
        event_detail = (
            f"{html.escape(nation.currency_code)} با {fmt_pct(change)} تغییر، "
            f"در وضعیت {direction} قرار دارد."
        )
    else:
        winner_code, winner_change = overview["top_mover"]["winner"]
        loser_code, loser_change = overview["top_mover"]["loser"]
        event_title = "مهم‌ترین حرکت بازار"
        if winner_code and loser_code:
            event_detail = (
                f"{winner_code} {fmt_pct(winner_change)} بالا رفته و "
                f"{loser_code} {fmt_pct(loser_change)} پایین آمده است."
            )
        else:
            event_detail = "بازار در حال پایش است."

    if change <= Decimal("-3"):
        suggestion = "فشار فروش روی ارز ملتت بالاست. علت حرکت را بررسی کن."
        pressure_status = "فشار فروش"
    elif change >= Decimal("3"):
        suggestion = "تقاضا برای ارز ملتت بالاست. تغییرات بازار را زیر نظر بگیر."
        pressure_status = "فشار خرید"
    else:
        winner_code, winner_change = overview["top_mover"]["winner"]
        suggestion = (
            f"{winner_code} الان حرکت بیشتری دارد. وضعیت آن را بررسی کن."
            if winner_code
            else "بازار را بررسی کن و یک موقعیت مناسب پیدا کن."
        )
        pressure_status = "متعادل"

    growth_status = await _growth_status(session, nation, datetime.utcnow())
    risk_status = await _risk_status(session, nation, datetime.utcnow())

    return LiveDashboard(
        has_nation=True,
        nation_name=nation.name,
        nation_flag=nation.flag_emoji,
        currency_code=nation.currency_code,
        rate=current,
        change_24h=change,
        local_balance=local_balance,
        dollar_balance=dollar_balance,
        national_rank=national_rank,
        event_title=event_title,
        event_detail=event_detail,
        suggestion=suggestion,
        growth_status=growth_status,
        pressure_status=pressure_status,
        risk_status=risk_status,
    )


def render_live_dashboard(dashboard: LiveDashboard) -> str:
    if not dashboard.has_nation:
        return (
            "<b>داشبورد زنده OPEX MONEY</b>\n"
            "ــــــــــــــــــــ\n\n"
            f"<b>موجودی:</b> {fmt_amount(dashboard.dollar_balance)} دلار\n\n"
            "<b>ملت:</b> هنوز عضو هیچ ملتی نیستی\n"
            "<i>بازار و معامله بدون ملت آزاد است.</i>\n\n"
            f"<b>اتفاق مهم</b>\n{dashboard.event_detail}\n\n"
            f"<b>پیشنهاد اقتصادی</b>\n{dashboard.suggestion}\n\n"
            f"<b>بازار:</b> {dashboard.pressure_status}"
        )

    flag = dashboard.nation_flag or "🏴"
    return (
        f"{html.escape(flag)} <b>{html.escape(dashboard.nation_name or '')}</b>\n"
        f"<code>{html.escape(dashboard.currency_code or '')}</code> · "
        f"<b>{fmt_rate(dashboard.rate or 0)} دلار</b> · "
        f"<b>{fmt_pct(dashboard.change_24h or 0)}</b> ۲۴h\n\n"
        f"<b>موجودی:</b> {fmt_amount(dashboard.local_balance)} "
        f"{html.escape(dashboard.currency_code or '')} + "
        f"{fmt_amount(dashboard.dollar_balance)} دلار\n"
        f"<b>رتبه ملت:</b> #{to_fa(dashboard.national_rank or 0)}\n\n"
        f"<b>{html.escape(dashboard.event_title)}</b>\n"
        f"{dashboard.event_detail}\n\n"
        f"<b>پیشنهاد اقتصادی</b>\n"
        f"{dashboard.suggestion}\n\n"
        f"<b>وضعیت ملت</b>\n"
        f"رشد: {dashboard.growth_status}\n"
        f"فشار: {dashboard.pressure_status}\n"
        f"خطر: {dashboard.risk_status}"
    )
