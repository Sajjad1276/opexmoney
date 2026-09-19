from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ActivityType,
    CurrencyHolding,
    Nation,
    NationRank,
    RateHistory,
    User,
    UserActivity,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 300


class NationRow(TypedDict):
    rank: int
    nation_name: str
    currency_code: str
    exchange_rate: Decimal
    member_count: int
    rate_change_pct: Decimal
    rate_emoji: str


class UserWealthRow(TypedDict):
    rank: int
    username: str
    total_xr: Decimal
    is_current: bool


class UserTraderRow(TypedDict):
    rank: int
    username: str
    trade_count: int
    is_current: bool


def _cache_key(tab: str, user_id: int) -> str:
    return f"ranking:{tab}:{user_id}"


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported cache value: {type(value)!r}")


async def _cache_get(redis, key: str) -> dict | None:
    if redis is None:
        return None
    try:
        value = await redis.get(key)
        if not value:
            return None
        return json.loads(value)
    except Exception:
        logger.warning("Ranking Redis get failed for key %s", key, exc_info=True)
        return None


async def _cache_set(redis, key: str, value: dict) -> None:
    if redis is None:
        return
    try:
        await redis.setex(
            key,
            CACHE_TTL,
            json.dumps(value, default=_json_default, ensure_ascii=False),
        )
    except Exception:
        logger.warning("Ranking Redis set failed for key %s", key, exc_info=True)


def _nation_from_cache(row: dict) -> NationRow:
    return {
        "rank": int(row["rank"]),
        "nation_name": str(row["nation_name"]),
        "currency_code": str(row["currency_code"]),
        "exchange_rate": Decimal(str(row["exchange_rate"])),
        "member_count": int(row["member_count"]),
        "rate_change_pct": Decimal(str(row["rate_change_pct"])),
        "rate_emoji": str(row["rate_emoji"]),
    }


def _wealth_from_cache(row: dict) -> UserWealthRow:
    return {
        "rank": int(row["rank"]),
        "username": str(row["username"]),
        "total_xr": Decimal(str(row["total_xr"])),
        "is_current": bool(row["is_current"]),
    }


def _trader_from_cache(row: dict) -> UserTraderRow:
    return {
        "rank": int(row["rank"]),
        "username": str(row["username"]),
        "trade_count": int(row["trade_count"]),
        "is_current": bool(row["is_current"]),
    }


def _nation_to_cache(row: NationRow) -> dict:
    return dict(row)


def _wealth_to_cache(row: UserWealthRow) -> dict:
    return dict(row)


def _trader_to_cache(row: UserTraderRow) -> dict:
    return dict(row)


def _rate_emoji(change: Decimal) -> str:
    if change > 0:
        return "📈"
    if change < 0:
        return "📉"
    return "➡️"


async def get_nation_ranking(
    session: AsyncSession,
    redis,
    current_user_id: int,
) -> dict:
    key = _cache_key("nations", current_user_id)
    cached = await _cache_get(redis, key)
    if cached is not None:
        cached["top10"] = [_nation_from_cache(row) for row in cached.get("top10", [])]
        cached["current_rank"] = (
            int(cached["current_rank"])
            if cached.get("current_rank") is not None
            else None
        )
        cached["total_nations"] = int(cached.get("total_nations", 0))
        return cached

    user_home_nation_id = await session.scalar(
        select(User.home_nation_id).where(User.user_id == current_user_id)
    )

    yesterday = datetime.utcnow().date() - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday, datetime.min.time())
    today_start = yesterday_start + timedelta(days=1)

    previous_rate = (
        select(RateHistory.rate)
        .where(
            RateHistory.nation_id == Nation.nation_id,
            RateHistory.calculated_at >= yesterday_start,
            RateHistory.calculated_at < today_start,
        )
        .order_by(RateHistory.calculated_at.desc())
        .limit(1)
        .scalar_subquery()
    )

    rows = (
        await session.execute(
            select(
                Nation,
                NationRank.rank.label("stored_rank"),
                previous_rate.label("previous_rate"),
            )
            .outerjoin(NationRank, NationRank.nation_id == Nation.nation_id)
            .where(Nation.is_active.is_(True))
        )
    ).all()

    ordered = sorted(
        rows,
        key=lambda item: (
            0 if item.stored_rank is not None else 1,
            int(item.stored_rank) if item.stored_rank is not None else 0,
            -int(item.Nation.member_count or 0),
            item.Nation.nation_id,
        ),
    )

    full_rows: list[NationRow] = []
    for position, item in enumerate(ordered, start=1):
        nation = item.Nation
        previous = (
            Decimal(str(item.previous_rate))
            if item.previous_rate is not None
            else Decimal("0")
        )
        current_rate = Decimal(str(nation.exchange_rate))
        if previous == 0:
            rate_change_pct = Decimal("0.0")
        else:
            rate_change_pct = (
                (current_rate - previous) / previous * Decimal("100")
            )

        row: NationRow = {
            "rank": int(item.stored_rank) if item.stored_rank is not None else position,
            "nation_name": str(nation.name),
            "currency_code": str(nation.currency_code),
            "exchange_rate": current_rate,
            "member_count": int(nation.member_count or 0),
            "rate_change_pct": rate_change_pct,
            "rate_emoji": _rate_emoji(rate_change_pct),
        }
        full_rows.append(row)

    current_rank = None
    if user_home_nation_id is not None:
        for index, item in enumerate(ordered, start=1):
            if item.Nation.nation_id == user_home_nation_id:
                current_rank = int(item.stored_rank) if item.stored_rank is not None else index
                break

    result = {
        "top10": full_rows[:10],
        "current_rank": current_rank,
        "total_nations": len(full_rows),
    }
    await _cache_set(
        redis,
        key,
        {
            "top10": [_nation_to_cache(row) for row in result["top10"]],
            "current_rank": result["current_rank"],
            "total_nations": result["total_nations"],
        },
    )
    return result


async def get_wealth_ranking(
    session: AsyncSession,
    redis,
    current_user_id: int,
) -> dict:
    key = _cache_key("rich", current_user_id)
    cached = await _cache_get(redis, key)
    if cached is not None:
        cached["top10"] = [_wealth_from_cache(row) for row in cached.get("top10", [])]
        cached["current_rank"] = (
            int(cached["current_rank"])
            if cached.get("current_rank") is not None
            else None
        )
        cached["total_users"] = int(cached.get("total_users", 0))
        return cached

    holding_value = (
        select(
            CurrencyHolding.user_id.label("user_id"),
            func.coalesce(
                func.sum(CurrencyHolding.amount * Nation.exchange_rate),
                0,
            ).label("holding_xr"),
        )
        .join(Nation, Nation.nation_id == CurrencyHolding.nation_id)
        .where(Nation.is_active.is_(True))
        .group_by(CurrencyHolding.user_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(
                User.user_id,
                User.username,
                User.xr_balance,
                func.coalesce(holding_value.c.holding_xr, 0).label("holding_xr"),
            )
            .outerjoin(holding_value, holding_value.c.user_id == User.user_id)
            .where(User.home_nation_id.is_not(None))
            .order_by(
                (
                    User.xr_balance
                    + func.coalesce(holding_value.c.holding_xr, 0)
                ).desc(),
                User.user_id.asc(),
            )
        )
    ).all()

    top10: list[UserWealthRow] = []
    current_rank = None

    for position, row in enumerate(rows, start=1):
        total_xr = Decimal(str(row.xr_balance or 0)) + Decimal(str(row.holding_xr or 0))
        is_current = int(row.user_id) == int(current_user_id)
        if is_current:
            current_rank = position
        if position <= 10:
            top10.append(
                {
                    "rank": position,
                    "username": str(row.username),
                    "total_xr": total_xr,
                    "is_current": is_current,
                }
            )

    result = {
        "top10": top10,
        "current_rank": current_rank,
        "total_users": len(rows),
    }
    await _cache_set(
        redis,
        key,
        {
            "top10": [_wealth_to_cache(row) for row in top10],
            "current_rank": current_rank,
            "total_users": len(rows),
        },
    )
    return result


async def get_trader_ranking(
    session: AsyncSession,
    redis,
    current_user_id: int,
) -> dict:
    key = _cache_key("traders", current_user_id)
    cached = await _cache_get(redis, key)
    if cached is not None:
        cached["top10"] = [_trader_from_cache(row) for row in cached.get("top10", [])]
        cached["current_rank"] = (
            int(cached["current_rank"])
            if cached.get("current_rank") is not None
            else None
        )
        cached["total_users"] = int(cached.get("total_users", 0))
        return cached

    since = datetime.utcnow() - timedelta(hours=24)
    trade_count = func.count(UserActivity.id)

    rows = (
        await session.execute(
            select(
                User.user_id,
                User.username,
                trade_count.label("trade_count"),
            )
            .outerjoin(
                UserActivity,
                (
                    (UserActivity.user_id == User.user_id)
                    & (UserActivity.created_at >= since)
                    & (
                        UserActivity.activity_type.in_(
                            [ActivityType.TRADE]
                        )
                    )
                ),
            )
            .where(User.home_nation_id.is_not(None))
            .group_by(User.user_id, User.username)
            .order_by(
                trade_count.desc(),
                User.user_id.asc(),
            )
        )
    ).all()

    top10: list[UserTraderRow] = []
    current_rank = None

    for position, row in enumerate(rows, start=1):
        trade_count_value = int(row.trade_count or 0)
        is_current = int(row.user_id) == int(current_user_id)
        if is_current:
            current_rank = position
        if position <= 10:
            top10.append(
                {
                    "rank": position,
                    "username": str(row.username),
                    "trade_count": trade_count_value,
                    "is_current": is_current,
                }
            )

    result = {
        "top10": top10,
        "current_rank": current_rank,
        "total_users": len(rows),
    }
    await _cache_set(
        redis,
        key,
        {
            "top10": [_trader_to_cache(row) for row in top10],
            "current_rank": current_rank,
            "total_users": len(rows),
        },
    )
    return result


MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


def rank_prefix(n: int) -> str:
    return MEDAL.get(n, f"  #{n}")


def _fmt_rate_change(value: Decimal) -> str:
    return f"{value:+.2f}" if value != 0 else "0.00"


def build_nation_msg(data: dict) -> str:
    lines = [
        "🏆 <b>رتبه‌بندی ملت‌ها</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if not data["top10"]:
        lines.append("⚠️ هنوز داده‌ای برای نمایش وجود ندارد.")
    else:
        for row in data["top10"]:
            lines.append(
                f"{rank_prefix(row['rank'])} "
                f"{row['nation_name']} · {row['currency_code']}"
            )
            lines.append(
                f"        💹 {row['exchange_rate']:.2f} ΩXR · "
                f"{row['rate_emoji']} {_fmt_rate_change(row['rate_change_pct'])}٪ · "
                f"👥 {row['member_count']} نفر"
            )

    lines.append("━━━━━━━━━━━━━━━━━━")
    current_rank = data["current_rank"]
    if current_rank is not None and current_rank <= 10:
        lines.append(
            f"🏛 ملت تو: #{current_rank} از {data['total_nations']}"
        )
    elif current_rank is not None:
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"📍 ملت تو: #{current_rank} از {data['total_nations']}"
        )
    else:
        lines.append("🏛 هنوز عضو هیچ ملتی نیستی")

    return "\n".join(lines)


def build_wealth_msg(data: dict) -> str:
    lines = [
        "💰 <b>ثروتمندترین‌ها</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if not data["top10"]:
        return "\n".join(lines + ["⚠️ هنوز داده‌ای برای نمایش وجود ندارد."])

    for row in data["top10"]:
        lines.append(
            f"{rank_prefix(row['rank'])} "
            f"{row['username']}"
        )
        lines.append(
            f"        💎 {row['total_xr']:.2f} ΩXR"
        )

    lines.append("━━━━━━━━━━━━━━━━━━")
    current_rank = data["current_rank"]
    top_ranks = {row["rank"] for row in data["top10"]}
    if current_rank is not None and current_rank not in top_ranks:
        lines.append(
            f"📍 رتبه تو: #{current_rank} از {data['total_users']} نفر"
        )

    return "\n".join(lines)


def build_trader_msg(data: dict) -> str:
    lines = [
        "📈 <b>فعال‌ترین معامله‌گران (۲۴ ساعت)</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if not data["top10"]:
        return "\n".join(lines + ["⚠️ هنوز داده‌ای برای نمایش وجود ندارد."])

    for row in data["top10"]:
        lines.append(
            f"{rank_prefix(row['rank'])} "
            f"{row['username']}"
        )
        lines.append(
            f"        🔄 {row['trade_count']} معامله"
        )

    lines.append("━━━━━━━━━━━━━━━━━━")
    current_rank = data["current_rank"]
    top_ranks = {row["rank"] for row in data["top10"]}
    if current_rank is not None and current_rank not in top_ranks:
        lines.append(
            f"📍 رتبه تو: #{current_rank} از {data['total_users']} نفر"
        )

    return "\n".join(lines)
