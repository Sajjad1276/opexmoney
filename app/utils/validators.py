from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Nation


_NATION_NAME_RE = re.compile(r"^[\u0600-\u06FFa-zA-Z0-9\s\-]+$")
_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")
_RESERVED_CURRENCY_CODES = {"XMR", "XRP", "XLM", "OMX", "XOM", "ΩXR"}


def validate_nation_name(value: str) -> tuple[bool, str]:
    value = value.strip()
    if len(value) < 2 or len(value) > 20:
        return False, "⚠️ اسم ملت باید بین ۲ تا ۲۰ کاراکتر باشه."
    if not _NATION_NAME_RE.fullmatch(value):
        return False, "⚠️ فقط حروف فارسی، انگلیسی، عدد و خط تیره مجازه."
    if value.strip("-").strip() == "":
        return False, "⚠️ اسم ملت معتبر نیست."
    return True, ""


async def validate_currency_code(
    value: str,
    session: AsyncSession,
) -> tuple[bool, str]:
    value = value.strip().upper()

    if not _CURRENCY_CODE_RE.fullmatch(value):
        return False, "⚠️ کد ارز باید دقیقاً ۳ حرف لاتین بزرگ باشه."

    if value in _RESERVED_CURRENCY_CODES:
        return False, "⚠️ این کد رزرو سیستمه. یه کد دیگه انتخاب کن."

    existing = await session.execute(
        select(Nation.nation_id)
        .where(Nation.currency_code == value)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False, "⚠️ این کد ارز قبلاً استفاده شده. یه کد دیگه انتخاب کن."

    return True, ""
