"""Dynamic prompt construction for the OPEX MONEY AI companion."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai.personality import SYSTEM_PERSONALITY


def build_dynamic_prompt(
    *,
    context: dict[str, Any],
    user_message: str,
) -> str:
    """Build the user-facing model input from current game context."""
    compact_context = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )

    return f"""
وضعیت زنده بازی کاربر:
{compact_context}

پیام جدید کاربر:
{user_message.strip()}

وظیفه:
پاسخ کوتاه و طبیعی بده. به وضعیت واقعی بازیکن تکیه کن.
اگر اطلاعات کافی نیست، حدس نزن.
پیشنهاد بده، نه فرمان.
حداکثر ۴ خط.
پاسخ را فقط به شکل یک شیء JSON معتبر برگردان:
{{"reply":"متن پاسخ"}}
"""


def build_cache_key(
    *,
    model: str,
    context: dict[str, Any],
    user_message: str,
) -> str:
    """Build a privacy-conscious deterministic Redis cache key."""
    payload = {
        "model": model,
        "context": context,
        "user_message": user_message.strip(),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")

    digest = hashlib.sha256(raw).hexdigest()
    return f"opex:ai:response:{digest}"


__all__ = [
    "SYSTEM_PERSONALITY",
    "build_cache_key",
    "build_dynamic_prompt",
]
