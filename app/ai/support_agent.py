from __future__ import annotations

import html
import json
import logging

from google.genai import types

from ai import companion
from ai.response_parser import parse_ai_response
from app.services.support.models import EngineeringResult, RepairResult, SupportDiagnosis
from config import settings

logger = logging.getLogger("opex.support.ai")

_FALLBACK = (
    "مشکل بررسی شد، اما پاسخ هوشمند فعلاً در دسترس نبود. "
    "نتیجه بررسی بر اساس وضعیت واقعی حساب اعمال شده است."
)


def _fallback(
    diagnosis: SupportDiagnosis,
    repair: RepairResult,
    engineering: EngineeringResult | None,
) -> str:
    if engineering is not None:
        if engineering.status == "fixed":
            return (
                "<b>باگ پیدا و اصلاح شد.</b>\n"
                f"{html.escape(engineering.summary)}"
            )
        if engineering.status in {
            "ci_failed",
            "blocked",
            "credentials_missing",
            "timeout",
            "busy",
            "no_patch",
            "no_context",
            "merge_failed",
            "error",
        }:
            return (
                "<b>مشکل شناسایی شد، اما اصلاح نهایی اعمال نشد.</b>\n"
                f"{html.escape(engineering.summary)}"
            )

    if repair.applied and repair.verified:
        if diagnosis.repair_action.value == "reset_fsm":
            return (
                "<b>مشکل پیدا و اصلاح شد.</b>\n"
                "وضعیت مرحله‌ای قدیمی پاک شد. حالا دوباره بازار را باز کن."
            )
        return (
            "<b>مشکل پیدا و اصلاح شد.</b>\n"
            f"{html.escape(repair.detail)}"
        )

    if diagnosis.code_fix_required:
        return (
            "<b>خطای واقعی پیدا شد.</b>\n"
            "آخرین خطای ثبت‌شده مربوط به اجرای داخلی بازی است و "
            "برای اصلاح کد به بررسی مهندسی نیاز دارد."
        )

    return f"<b>بررسی انجام شد.</b>\n{html.escape(diagnosis.summary)}"


async def generate_support_reply(
    *,
    report: str,
    diagnosis: SupportDiagnosis,
    repair: RepairResult,
    engineering: EngineeringResult | None,
) -> str:
    fallback = _fallback(diagnosis, repair, engineering)
    if not settings.support_ai_enabled:
        return fallback

    client = companion._get_client()
    if client is None:
        return fallback

    payload = {
        "user_report": report[: settings.support_max_report_chars],
        "category": diagnosis.category.value,
        "summary": diagnosis.summary,
        "root_cause": diagnosis.root_cause,
        "confidence": diagnosis.confidence,
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail}
            for item in diagnosis.checks
        ],
        "evidence": list(diagnosis.evidence),
        "engineering": (
            {
                "status": engineering.status,
                "summary": engineering.summary,
                "changed_files": list(engineering.changed_files),
                "branch": engineering.branch,
                "pull_request": engineering.pull_request,
            }
            if engineering is not None
            else None
        ),
        "repair": {
            "action": repair.action.value,
            "applied": repair.applied,
            "verified": repair.verified,
            "detail": repair.detail,
        },
    }

    system = """
تو «پشتیبانی هوشمند OPEX MONEY» هستی.
فقط از داده تشخیصی داده‌شده استفاده کن.
هیچ خطایی را حدس نزن.
اگر repair.applied و repair.verified هر دو true نیستند، هرگز نگو «رفع شد».
اگر engineering وجود دارد، فقط در status=fixed بگو باگ اصلاح شد.
هرگز درباره پول، دارایی، نقش، مجوز یا امتیاز کاربر چیزی برای رفع مشکل پیشنهاد نکن.
هیچ شناسه کاربر، SQL، traceback، نام فایل یا جزئیات داخلی را به کاربر نشان نده.
حداکثر 6 خط فارسی روان.
"""

    prompt = (
        "نتیجه تشخیص واقعی:\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n"
        + "یک پاسخ مستقیم برای بازیکن بساز."
    )

    candidates = [
        settings.ai_model,
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    seen: set[str] = set()
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2,
                    max_output_tokens=220,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return parse_ai_response(text).reply
        except Exception:
            logger.warning("Support AI model failed: %s", model, exc_info=True)
            continue

    return fallback


__all__ = ["generate_support_reply"]
