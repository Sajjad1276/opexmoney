from __future__ import annotations

import re


_NATION_NAME_RE = re.compile(
    r"^[A-Za-z\u0621-\u063A\u0641-\u064A\u067E\u06A9\u06CC\u0686\u0698\u06AF]+"
    r"(?:[ -][A-Za-z\u0621-\u063A\u064A\u067E\u0686\u0698\u06AF]+)*$"
)
_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


def validate_nation_name(value: str) -> tuple[bool, str]:
    value = value.strip()
    if not 2 <= len(value) <= 20:
        return False, "⚠️ اسم ملت باید بین ۲ تا ۲۰ کاراکتر باشه."
    if not _NATION_NAME_RE.fullmatch(value):
        return False, "⚠️ اسم ملت فقط می‌تونه فارسی، انگلیسی، فاصله یا خط تیره داشته باشه."
    return True, ""


def validate_currency_code(value: str) -> tuple[bool, str]:
    value = value.strip()
    if not _CURRENCY_CODE_RE.fullmatch(value):
        return False, "⚠️ کد ارز باید دقیقاً ۳ حرف لاتین بزرگ باشه."
    if value in {"XR", "ΩXR"}:
        return False, "⚠️ این کد ارز برای سیستم رزرو شده."
    return True, ""
