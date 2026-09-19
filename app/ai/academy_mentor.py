from __future__ import annotations

import logging
from decimal import Decimal

from google.genai import types

from ai import companion
from config import settings

logger = logging.getLogger(__name__)

RLM = "\u200f"
FALLBACK = "متأسفم، الان نمی‌تونم جواب بدم. بعداً دوباره امتحان کن."


def _normalize_response(text: str) -> str:
    clean = (text or "").strip()
    fence = chr(96) * 3
    if clean.startswith(fence) and clean.endswith(fence):
        clean = clean[len(fence):-len(fence)].strip()

    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines:
        return FALLBACK

    return "\n".join(f"{RLM}{line}" for line in lines[:5])


async def ask_mentor(
    user_username: str,
    user_level: str,
    nation_name: str | None,
    xr_balance: Decimal,
    lesson_title: str,
    lesson_content: str,
    question: str,
    history: list[dict],
) -> str:
    system = f"""تو «اوپکس» هستی — دستیار هوشمند آموزشی بازی OPEX MONEY.
فقط درباره این بازی جواب می‌دی. سوالات خارج از موضوع رو مؤدبانه رد کن.

اطلاعات کاربر:
  نام: {user_username}
  سطح: {user_level}
  ملت: {nation_name or "بدون ملت"}
  موجودی ΩXR: {xr_balance}

درس فعلی: {lesson_title}
محتوا: {lesson_content[:500]}

قوانین پاسخ:
- حداکثر ۵ خط فارسی روان
- سرتیتر <b>بولد</b>، نکته مهم <u>زیرخط</u>
- مثال واقعی با اعداد از بازی بده
- اگه سوال خارج از OPEX بود: «این سوال از حوزه بازی خارجه! درباره نرخ ارز، معاملات یا استراتژی بپرس.»
- هر خط رو با \u200f شروع کن"""

    transcript: list[str] = []
    for item in history[-8:]:
        role = "کاربر" if item.get("role") == "user" else "اوپکس"
        content = str(item.get("content", "")).strip()
        if content:
            transcript.append(f"{role}: {content}")
    transcript.append(f"کاربر: {question.strip()}")

    prompt = "\n".join(transcript) + "\n\nاوپکس، به آخرین سوال کاربر پاسخ بده."

    try:
        client = companion._get_client()
        if client is None:
            return FALLBACK

        response = await client.aio.models.generate_content(
            model=settings.ai_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.5,
                max_output_tokens=250,
            ),
        )
        return _normalize_response(response.text or "")
    except Exception:
        logger.exception(
            "Academy mentor call failed for model=%s",
            settings.ai_model,
        )
        return FALLBACK
