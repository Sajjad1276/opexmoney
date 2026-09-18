from __future__ import annotations

import itertools
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation
from app.utils.name_filter import is_blocked_trader_name


_NATION_NAME_RE = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+)*$")
_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")
_RESERVED_CURRENCY_CODES = {"XMR", "XRP", "XLM", "OMX", "XOM", "OXR"}
_RESERVED_NATION_NAMES = {
    "ADMIN",
    "BOT",
    "GLOBAL",
    "OPEX",
    "OPEX MONEY",
    "SYSTEM",
    "TELEGRAM",
    "WORLD",
}


def validate_nation_name(value: str) -> tuple[bool, str]:
    value = " ".join(value.strip().split())
    if len(value) < 2 or len(value) > 20:
        return False, "⚠️ اسم ملت باید بین ۲ تا ۲۰ کاراکتر باشه."
    if not _NATION_NAME_RE.fullmatch(value):
        return False, "⚠️ اسم ملت فقط باید انگلیسی و بدون عدد یا علامت باشه."
    if value.upper() in _RESERVED_NATION_NAMES or is_blocked_trader_name(value):
        return False, "⚠️ این اسم مجاز نیست. یه اسم دیگه انتخاب کن."
    return True, ""


async def validate_currency_code(value: str, session: AsyncSession) -> tuple[bool, str]:
    value = value.strip().upper()
    if not _CURRENCY_CODE_RE.fullmatch(value):
        return False, "⚠️ کد ارز باید دقیقاً ۳ حرف لاتین بزرگ باشه."
    if value in _RESERVED_CURRENCY_CODES:
        return False, "⚠️ این کد رزرو سیستمه. یه کد دیگه انتخاب کن."
    existing = await session.execute(
        select(Nation.nation_id).where(Nation.currency_code == value).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False, "⚠️ این کد ارز قبلاً استفاده شده. یه کد دیگه انتخاب کن."
    return True, ""


async def generate_unique_currency_code(
    nation_name: str,
    session: AsyncSession,
) -> str:
    normalized = re.sub(r"[^A-Z]", "", nation_name.upper())
    if len(normalized) < 3:
        raise ValueError("⚠️ اسم ملت برای ساخت کد ارز کافی نیست.")

    words = nation_name.upper().split()
    candidates: list[str] = []

    def add(candidate: str) -> None:
        candidate = candidate.upper()
        if _CURRENCY_CODE_RE.fullmatch(candidate) and candidate not in candidates:
            candidates.append(candidate)

    add(normalized[:3])
    if len(words) >= 3:
        add("".join(word[0] for word in words[:3]))
    if len(words) >= 2:
        first = re.sub(r"[^A-Z]", "", words[0])
        second = re.sub(r"[^A-Z]", "", words[1])
        add((first[:2] + second[0])[:3])
        add((first[0] + second[:2])[:3])
    add(normalized[:2] + normalized[-1])
    add(normalized[0] + normalized[-2:])

    unique_letters = list(dict.fromkeys(normalized))
    for combo in itertools.permutations(unique_letters, 3):
        add("".join(combo))
        if len(candidates) >= 512:
            break

    result = await session.execute(select(Nation.currency_code))
    used_codes = {row[0].upper() for row in result.all()}

    for code in candidates:
        if code not in _RESERVED_CURRENCY_CODES and code not in used_codes:
            return code

    # The currency namespace has only 26^3 values. This fallback guarantees
    # uniqueness even after many similarly named nations have consumed the
    # natural abbreviations.
    for first, second, third in itertools.product("ABCDEFGHIJKLMNOPQRSTUVWXYZ", repeat=3):
        code = f"{first}{second}{third}"
        if code not in _RESERVED_CURRENCY_CODES and code not in used_codes:
            return code

    raise ValueError("⚠️ ظرفیت کدهای ارز پر شده. فعلاً امکان تأسیس ملت جدید نیست.")
