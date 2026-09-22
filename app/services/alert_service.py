from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation, PriceAlert
from app.utils.formatting import fmt_rate, to_fa

logger = logging.getLogger(__name__)


def parse_direction(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    aliases = {
        "above": "above",
        "up": "above",
        "بالا": "above",
        "بیشتر": "above",
        "below": "below",
        "down": "below",
        "پایین": "below",
        "کمتر": "below",
    }
    return aliases.get(value)


async def create_price_alert(
    session: AsyncSession,
    *,
    user_id: int,
    currency_code: str,
    target_price: Decimal,
    direction: str | None = None,
) -> PriceAlert:
    code = currency_code.strip().upper()
    if not code:
        raise ValueError("کد ارز را وارد کن.")

    target_price = Decimal(str(target_price))
    if target_price <= 0:
        raise ValueError("قیمت هدف باید بیشتر از صفر باشد.")

    nation = await session.scalar(
        select(Nation)
        .where(
            Nation.currency_code == code,
            Nation.is_active.is_(True),
        )
        .limit(1)
    )
    if nation is None:
        raise ValueError("این ارز در بازار فعال نیست.")

    normalized_direction = parse_direction(direction)
    if normalized_direction is None:
        current = Decimal(str(nation.exchange_rate))
        normalized_direction = "above" if current < target_price else "below"

    alert = PriceAlert(
        user_id=user_id,
        currency_code=code,
        target_price=target_price,
        direction=normalized_direction,
        triggered=False,
        is_active=True,
    )
    session.add(alert)
    await session.flush()
    return alert


async def list_price_alerts(
    session: AsyncSession,
    user_id: int,
) -> list[PriceAlert]:
    return list(
        (
            await session.execute(
                select(PriceAlert)
                .where(PriceAlert.user_id == user_id)
                .order_by(PriceAlert.triggered.asc(), PriceAlert.created_at.desc())
            )
        ).scalars().all()
    )


async def delete_price_alert(
    session: AsyncSession,
    *,
    user_id: int,
    alert_id: int,
) -> bool:
    result = await session.execute(
        delete(PriceAlert).where(
            PriceAlert.id == alert_id,
            PriceAlert.user_id == user_id,
        )
    )
    return bool(result.rowcount)


def _condition_met(current: Decimal, target: Decimal, direction: str) -> bool:
    if direction == "above":
        return current >= target
    if direction == "below":
        return current <= target
    return False


async def check_price_alerts(
    session: AsyncSession,
    bot: Bot,
) -> int:
    rows = (
        await session.execute(
            select(PriceAlert, Nation)
            .join(
                Nation,
                Nation.currency_code == PriceAlert.currency_code,
            )
            .where(
                PriceAlert.triggered.is_(False),
                PriceAlert.is_active.is_(True),
                Nation.is_active.is_(True),
            )
            .order_by(PriceAlert.id.asc())
        )
    ).all()

    triggered_count = 0

    for alert, nation in rows:
        current = Decimal(str(nation.exchange_rate))
        target = Decimal(str(alert.target_price))

        if not _condition_met(
            current,
            target,
            alert.direction,
        ):
            continue

        direction_icon = "▲" if alert.direction == "above" else "▼"
        message = (
            "🔔 <b>هشدار قیمت!</b>\n"
            f"<code>{html.escape(alert.currency_code)}</code> "
            f"به <b>{fmt_rate(current)}</b> OPX رسید.\n"
            f"هدف شما: <b>{fmt_rate(target)}</b> OPX {direction_icon}"
        )

        try:
            await bot.send_message(
                alert.user_id,
                message,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Could not deliver price alert id=%s user=%s",
                alert.id,
                alert.user_id,
            )
            continue

        alert.triggered = True
        triggered_count += 1

    await session.flush()
    return triggered_count


async def count_active_alerts(
    session: AsyncSession,
    user_id: int,
) -> int:
    from sqlalchemy import func

    value = await session.scalar(
        select(func.count(PriceAlert.id))
        .where(
            PriceAlert.user_id == user_id,
            PriceAlert.triggered.is_(False),
        )
    )
    return int(value or 0)
