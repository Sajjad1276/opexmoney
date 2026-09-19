"""Dynamic prompts and Anthropic client for OPEX MONEY.

This module keeps the old prompt_engine public API intact while adding:
- PromptScenario enum
- scenario-specific prompt builders
- PromptEngine.build(...)
- AIClient with retry/timeout/fallback/Redis cache
- structured AI-call logging

The HTTP client intentionally uses Python's standard library so the AI layer
does not add another runtime dependency to the bot.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import time
from enum import StrEnum
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from redis.asyncio import Redis

from ai.personality import SYSTEM_PERSONALITY
from config import settings

logger = logging.getLogger("opex.ai")

try:
    from anthropic import AnthropicError  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AnthropicError = Exception  # type: ignore


class PromptScenario(StrEnum):
    WELCOME_NEW_USER = "WELCOME_NEW_USER"
    WELCOME_BACK = "WELCOME_BACK"
    NATION_JOIN_WELCOME = "NATION_JOIN_WELCOME"
    TRADE_ANALYSIS = "TRADE_ANALYSIS"
    WAR_DECLARATION = "WAR_DECLARATION"
    DAILY_BRIEFING = "DAILY_BRIEFING"
    ACHIEVEMENT_UNLOCK = "ACHIEVEMENT_UNLOCK"
    RANK_CHANGE = "RANK_CHANGE"
    ECONOMIC_ALERT = "ECONOMIC_ALERT"
    NATION_WEEKLY_REPORT = "NATION_WEEKLY_REPORT"


FALLBACK_RESPONSES: dict[PromptScenario, str] = {
    PromptScenario.WELCOME_NEW_USER:
        "\u200f🎮 <b>خوش اومدی!</b>\nحسابت آماده‌ست. بریم شروع کنیم.",
    PromptScenario.WELCOME_BACK:
        "\u200f👋 <b>خوش برگشتی!</b>\nبازار سر جاشه؛ آخرین وضعیتت رو بررسی کن.",
    PromptScenario.NATION_JOIN_WELCOME:
        "\u200f🏛 <b>به ملت خوش اومدی!</b>\nاز اینجا به بعد، نقش تو روی اقتصاد ملت اثر می‌ذاره.",
    PromptScenario.TRADE_ANALYSIS:
        "\u200f📊 <b>معامله ثبت شد.</b>\nوضعیت بازار رو در پورتفولیو دنبال کن.",
    PromptScenario.WAR_DECLARATION:
        "\u200f⚔️ <b>جنگ اقتصادی اعلام شد.</b>\nرفتار بازار و واکنش طرف مقابل رو زیر نظر داشته باش.",
    PromptScenario.DAILY_BRIEFING:
        "\u200f📰 <b>خلاصه امروز آماده‌ست.</b>\nوضعیت اقتصاد و بازار رو از داشبورد دنبال کن.",
    PromptScenario.ACHIEVEMENT_UNLOCK:
        "\u200f🏆 <b>دستاورد جدید!</b>\nیه مرحله مهم رو پشت سر گذاشتی.",
    PromptScenario.RANK_CHANGE:
        "\u200f📈 <b>رتبه تغییر کرد.</b>\nحرکت بعدیت می‌تونه جایگاهت رو تثبیت کنه.",
    PromptScenario.ECONOMIC_ALERT:
        "\u200f🚨 <b>هشدار اقتصادی</b>\nبازار نوسان داره؛ قبل از تصمیم بعدی داده‌ها رو بررسی کن.",
    PromptScenario.NATION_WEEKLY_REPORT:
        "\u200f📊 <b>گزارش هفتگی ملت</b>\nگزارش کامل در دسترس نیست؛ شاخص‌های اصلی رو در پنل ملت بررسی کن.",
}


SCENARIO_TTLS: dict[PromptScenario, int] = {
    PromptScenario.WELCOME_NEW_USER: 0,
    PromptScenario.WELCOME_BACK: 0,
    PromptScenario.NATION_JOIN_WELCOME: 0,
    PromptScenario.TRADE_ANALYSIS: 300,
    PromptScenario.WAR_DECLARATION: 300,
    PromptScenario.DAILY_BRIEFING: 3600,
    PromptScenario.ACHIEVEMENT_UNLOCK: 0,
    PromptScenario.RANK_CHANGE: 300,
    PromptScenario.ECONOMIC_ALERT: 900,
    PromptScenario.NATION_WEEKLY_REPORT: 21600,
}


def _clean(value: Any, default: str = "نامشخص") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)


def _esc(value: Any, default: str = "نامشخص") -> str:
    return html.escape(_clean(value, default), quote=False)


def _number(value: Any, default: str = "۰") -> str:
    if value is None or value == "":
        return default
    return _clean(value)


def _context_json(ctx: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(ctx),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _scenario_header(scenario: PromptScenario) -> str:
    return (
        f"{SYSTEM_PERSONALITY.strip()}\n\n"
        f"━━━ سناریو: {scenario.value} ━━━"
    )


def _nation_personality(ctx: Mapping[str, Any]) -> str:
    nation = ctx.get("nation") or ctx.get("home_nation") or {}
    personality = ctx.get("nation_personality")
    if ctx.get("nation_personality") is not None
    else nation.get("personality") if isinstance(nation, dict) else None
    return _clean(personality, "neutral")


def _common_rules() -> str:
    return """
━━━ قوانین خروجی ━━━
- کاملاً فارسی و طبیعی بنویس.
- حداکثر ۵ خط مگر اینکه سناریو صراحتاً گزارش تحلیلی بخواهد.
- از عدد، رتبه، نرخ، نام ملت یا رویداد ساختگی استفاده نکن.
- فقط بر اساس داده‌های داخل prompt حرف بزن.
- HTML تلگرام مجاز: <b>عنوان</b>، <u>نکته مهم</u>، <i>توضیح</i>.
- از Markdown استفاده نکن.
- لحن زنده و داخل‌بازی باشد؛ شبیه دفترچه راهنما ننویس.
- پیشنهاد بده، نه فرمان.
""".strip()


class PromptEngine:
    """Build a dynamic prompt for each OPEX MONEY game scenario."""

    def __init__(self) -> None:
        self._builders = {
            PromptScenario.WELCOME_NEW_USER: self._build_welcome_new_user,
            PromptScenario.WELCOME_BACK: self._build_welcome_back,
            PromptScenario.NATION_JOIN_WELCOME: self._build_nation_join_welcome,
            PromptScenario.TRADE_ANALYSIS: self._build_trade_analysis,
            PromptScenario.WAR_DECLARATION: self._build_war_declaration,
            PromptScenario.DAILY_BRIEFING: self._build_daily_briefing,
            PromptScenario.ACHIEVEMENT_UNLOCK: self._build_achievement_unlock,
            PromptScenario.RANK_CHANGE: self._build_rank_change,
            PromptScenario.ECONOMIC_ALERT: self._build_economic_alert,
            PromptScenario.NATION_WEEKLY_REPORT: self._build_nation_weekly_report,
        }

    async def build(
        self,
        scenario: PromptScenario,
        user_context: dict,
        extra: dict = {},
    ) -> str:
        """Construct one fully rendered prompt for the requested scenario."""
        if not isinstance(scenario, PromptScenario):
            try:
                scenario = PromptScenario(str(scenario))
            except ValueError as exc:
                raise ValueError(f"Unsupported prompt scenario: {scenario}") from exc

        ctx = dict(user_context or {})
        if extra:
            ctx.update(extra)

        builder = self._builders[scenario]
        prompt = builder(ctx)
        return prompt.strip()

    def _base(self, scenario: PromptScenario, body: str) -> str:
        return (
            f"{_scenario_header(scenario)}\n\n"
            f"{body.strip()}\n\n"
            f"{_common_rules()}"
        )

    def _user_block(self, ctx: Mapping[str, Any]) -> str:
        user = ctx.get("user") if isinstance(ctx.get("user"), dict) else {}
        return f"""
━━━ اطلاعات کاربر ━━━
نام: {_esc(ctx.get("name") or user.get("name"))}
نقش: {_esc(ctx.get("role") or user.get("role"), "بازیکن")}
موجودی ΩXR: {_number(ctx.get("xr_balance") or user.get("xr_balance"))}
روزهای فعال: {_number(ctx.get("active_days"), "۰")}
""".strip()

    def _nation_block(self, ctx: Mapping[str, Any]) -> str:
        nation = ctx.get("nation") or ctx.get("home_nation") or {}
        if not isinstance(nation, dict):
            nation = {}
        return f"""
━━━ اطلاعات ملت ━━━
نام ملت: {_esc(ctx.get("nation_name") or nation.get("name"))}
واحد ارز: {_esc(ctx.get("currency_symbol") or ctx.get("currency") or nation.get("currency"))}
نرخ فعلی ملت: {_number(ctx.get("nation_rate") or ctx.get("exchange_rate") or nation.get("exchange_rate"))} ΩXR
رتبه ملت: #{_number(ctx.get("nation_rank") or ctx.get("rank") or nation.get("rank"))}
تعداد اعضا: {_number(ctx.get("nation_members") or ctx.get("members") or nation.get("members"))}
وضعیت اقتصادی ملت: {_esc(ctx.get("economic_status"), "نامشخص")}
شخصیت ملت: {_esc(_nation_personality(ctx), "neutral")}
""".strip()

    def _build_welcome_new_user(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.WELCOME_NEW_USER,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ وظیفه ━━━
یه پیام خوشامد بنویس که:
۱. کاربر رو با نامش خطاب کنه.
۲. یه نکته جالب واقعی درباره ملتی که انتخاب کرده بگه.
۳. یه توصیه استراتژیک کوتاه بده؛ فقط اگر از نرخ یا وضعیت اقتصادی داده قابل استفاده داری.
۴. کاملاً فارسی و حداکثر ۵ خط باشد.
۵. فرمت HTML با راست‌چین:
<b>سرتیتر</b> برای عنوان
<u>متن مهم</u> برای نکته کلیدی
""",
        )

    def _build_welcome_back(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.WELCOME_BACK,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ آخرین وضعیت ━━━
آخرین پیام اوپکس: {_esc(ctx.get("last_bot_message"), "ندارد")}
آخرین فعالیت: {_esc(ctx.get("last_activity"))}
تغییر نرخ از ورود قبلی: {_esc(ctx.get("rate_change"), "نامشخص")}

━━━ وظیفه ━━━
یک خوشامد کوتاه برای بازیکنی که بعد از مدتی برگشته بنویس.
به یک تغییر واقعی در وضعیت بازی اشاره کن و یک نکته مفید کوتاه بده.
""",
        )

    def _build_nation_join_welcome(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.NATION_JOIN_WELCOME,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ رویداد عضویت ━━━
نقش عضو جدید: {_esc(ctx.get("member_role"), "شهروند")}
منبع عضویت: {_esc(ctx.get("join_source"), "ورود عادی")}
نام نمایشی عضو: {_esc(ctx.get("member_name") or ctx.get("name"))}

━━━ وظیفه ━━━
از طرف ملت یک پیام خوشامد شخصی‌سازی‌شده بنویس.
شخصیت ملت باید روی لحن اثر بگذارد.
به نقش یا جایگاه عضو اشاره کن و یک جمله درباره فرصت او در اقتصاد ملت اضافه کن.
""",
        )

    def _build_trade_analysis(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.TRADE_ANALYSIS,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ معامله ━━━
نوع معامله: {_esc(ctx.get("trade_type"))}
حجم معامله: {_number(ctx.get("trade_amount"))}
هزینه ΩXR: {_number(ctx.get("trade_spend_xr"))}
نرخ معامله: {_number(ctx.get("trade_rate"))}
کارمزد: {_number(ctx.get("trade_fee_xr"))}
تغییر نرخ پس از معامله: {_esc(ctx.get("rate_impact"), "نامشخص")}
حجم معاملات ۲۴ ساعت: {_number(ctx.get("trade_volume_24h"))}

━━━ وظیفه ━━━
یک تحلیل کوتاه از اثر این معامله بر بازیکن و اقتصاد ملت بنویس.
ابتدا اندازه یا اهمیت معامله را توصیف کن، سپس اثر احتمالی روی نرخ یا نقدینگی را مشروط بیان کن.
اگر داده کافی برای نتیجه‌گیری نیست، همان را صریح بگو.
""",
        )

    def _build_war_declaration(self, ctx: dict) -> str:
        opponent = ctx.get("opponent") if isinstance(ctx.get("opponent"), dict) else {}
        return self._base(
            PromptScenario.WAR_DECLARATION,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ جنگ اقتصادی ━━━
ملت رقیب: {_esc(ctx.get("opponent_name") or opponent.get("name"))}
رتبه رقیب: #{_number(ctx.get("opponent_rank") or opponent.get("rank"))}
نرخ ارز رقیب: {_number(ctx.get("opponent_rate") or opponent.get("rate"))} ΩXR
دلیل ثبت‌شده برای جنگ: {_esc(ctx.get("reason"))}
تاریخ شروع: {_esc(ctx.get("declared_at"))}

━━━ وظیفه ━━━
متن اعلام جنگ اقتصادی را از طرف ملت تولید کن.
لحن باید با شخصیت ملت سازگار باشد.
ادعای قطعی درباره نتیجه جنگ نکن.
در پایان یک جمله کوتاه برای مراقبت از بازار و رصد واکنش رقیب بده.
""",
        )

    def _build_daily_briefing(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.DAILY_BRIEFING,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ شاخص‌های امروز ━━━
تعداد معاملات: {_number(ctx.get("trade_count"))}
حجم معاملات: {_number(ctx.get("trade_volume"))}
تغییر نرخ: {_esc(ctx.get("rate_change"))}
تغییر رتبه: {_esc(ctx.get("rank_change"))}
اعضای فعال: {_number(ctx.get("active_members"))}
رویدادهای مهم: {_esc(ctx.get("major_events"), "ندارد")}

━━━ وظیفه ━━━
خلاصه روزانه را در سه بخش کوتاه بنویس:
<b>بازار</b>، <b>ملت</b>، <b>پیشنهاد</b>.
فقط مهم‌ترین تغییرات را نگه دار و متن را جمع‌وجور کن.
""",
        )

    def _build_achievement_unlock(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.ACHIEVEMENT_UNLOCK,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ دستاورد ━━━
نام دستاورد: {_esc(ctx.get("achievement_name"))}
توضیح: {_esc(ctx.get("achievement_description"))}
سطح/کمیابی: {_esc(ctx.get("rarity"))}
دلیل باز شدن: {_esc(ctx.get("unlock_reason"))}

━━━ وظیفه ━━━
یک پیام جشن کوتاه بنویس.
نام دستاورد را برجسته کن، دلیل واقعی باز شدن را اشاره کن و یک جمله انگیزشی اضافه کن.
""",
        )

    def _build_rank_change(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.RANK_CHANGE,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ تغییر رتبه ━━━
رتبه قبلی: #{_number(ctx.get("old_rank"))}
رتبه جدید: #{_number(ctx.get("new_rank"))}
تغییر رتبه: {_esc(ctx.get("rank_delta"))}
دلیل/عامل ثبت‌شده: {_esc(ctx.get("rank_reason"))}

━━━ وظیفه ━━━
تغییر رتبه را طبیعی و کوتاه توضیح بده.
هیچ علت جدیدی اختراع نکن.
اگر رتبه بهتر شده، پیشرفت را جشن بگیر؛ اگر افت کرده، تحقیر نکن و یک نکته تحلیلی واقعی بده.
""",
        )

    def _build_economic_alert(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.ECONOMIC_ALERT,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ هشدار ━━━
عنوان هشدار: {_esc(ctx.get("alert_title"))}
شدت: {_esc(ctx.get("severity"))}
شاخص درگیر: {_esc(ctx.get("indicator"))}
مقدار فعلی: {_number(ctx.get("current_value"))}
مقدار مبنا: {_number(ctx.get("baseline_value"))}
تغییر درصدی: {_esc(ctx.get("change_percent"))}
دوره بررسی: {_esc(ctx.get("window"))}
علت ثبت‌شده: {_esc(ctx.get("alert_reason"))}

━━━ وظیفه ━━━
هشدار اقتصادی را شفاف اما غیرترساننده بنویس.
بگو چه چیزی تغییر کرده، چرا مهم است و کاربر چه چیزی را بهتر است زیر نظر داشته باشد.
""",
        )

    def _build_nation_weekly_report(self, ctx: dict) -> str:
        return self._base(
            PromptScenario.NATION_WEEKLY_REPORT,
            f"""
{self._user_block(ctx)}
{self._nation_block(ctx)}

━━━ داده ۷ روز اخیر ملت ━━━
تعداد رویدادها: {_number(ctx.get("event_count_7d"))}
اعضای فعلی: {_number(ctx.get("member_count"))}
عضویت‌های جدید: {_number(ctx.get("joins_7d"))}
خروج/اخراج: {_number(ctx.get("leaves_7d"))}
معاملات بزرگ: {_number(ctx.get("large_trades_7d"))}
حجم معاملات: {_number(ctx.get("trade_volume_7d"))}
نرخ شروع هفته: {_number(ctx.get("rate_week_open"))}
نرخ فعلی: {_number(ctx.get("rate_current"))}
تغییر نرخ: {_esc(ctx.get("rate_change_7d"))}
تغییر رتبه: {_esc(ctx.get("rank_change_7d"))}
جنگ‌ها: {_esc(ctx.get("wars_7d"), "ندارد")}
تحریم‌ها: {_esc(ctx.get("sanctions_7d"), "ندارد")}
تغییر سیاست‌ها: {_esc(ctx.get("policy_changes_7d"), "ندارد")}

━━━ وظیفه ━━━
گزارش هفتگی مخصوص بنیان‌گذار تولید کن با این ساختار:
<b>۱. تصویر کلی</b>
<b>۲. اقتصاد</b>
<b>۳. اعضا و مدیریت</b>
<b>۴. ریسک‌ها</b>
<b>۵. پیشنهادهای هفته بعد</b>

در هر بخش فقط نکات مهم را بگو و بر اساس داده‌ها تحلیل کن.
پیش‌بینی قطعی یا عدد ساختگی ننویس.
""",
        )


def _scenario_from_value(scenario: PromptScenario | str) -> PromptScenario:
    if isinstance(scenario, PromptScenario):
        return scenario
    return PromptScenario(str(scenario))


def build_dynamic_prompt(
    *,
    context: dict[str, Any],
    user_message: str,
) -> str:
    """Backward-compatible builder used by the existing AI companion.

    The conversational assistant is represented by the WELCOME_BACK scenario
    plus the actual user message.
    """
    engine = PromptEngine()

    merged = dict(context)
    merged["last_activity"] = user_message.strip()

    # PromptEngine.build is async by contract, while this compatibility
    # function is synchronous because the existing ai/__init__.py expects it.
    nation = merged.get("home_nation")
    body = engine._build_welcome_back(merged)
    return (
        f"{body}\n\n"
        "━━━ پیام جدید کاربر ━━━\n"
        f"{user_message.strip()}\n\n"
        "━━━ وظیفه مکالمه ━━━\n"
        "به پیام جدید کاربر کوتاه و طبیعی پاسخ بده و فقط از داده‌های بازی در همین prompt استفاده کن."
    ).strip()


def build_cache_key(
    *,
    model: str,
    context: dict[str, Any],
    user_message: str,
) -> str:
    """Build a deterministic Redis key for the legacy conversational flow."""
    payload = {
        "scenario": PromptScenario.WELCOME_BACK.value,
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


class AIClient:
    """Minimal Anthropic Messages API client with retries and Redis caching."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.anthropic.com/v1/messages",
        timeout_seconds: float = 10.0,
        redis_url: str | None = None,
    ) -> None:
        self.api_key = api_key or getattr(settings, "anthropic_api_key", None) or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or getattr(settings, "anthropic_model", None) or os.getenv(
            "ANTHROPIC_MODEL",
            "claude-sonnet",
        )
        self.base_url = base_url
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.redis_url = redis_url if redis_url is not None else getattr(settings, "redis_url", None)
        self._redis: Redis | None = None

    def _redis_client(self) -> Redis | None:
        if not self.redis_url:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def _cache_get(self, key: str) -> str | None:
        client = self._redis_client()
        if client is None:
            return None
        try:
            value = await client.get(key)
            return value if isinstance(value, str) else None
        except Exception:
            logger.exception("AI scenario cache read failed")
            return None

    async def _cache_set(self, key: str, value: str, ttl: int) -> None:
        if ttl <= 0:
            return
        client = self._redis_client()
        if client is None:
            return
        try:
            await client.setex(key, ttl, value)
        except Exception:
            logger.exception("AI scenario cache write failed")

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _build_cache_key(
        self,
        prompt: str,
        scenario: PromptScenario,
        max_tokens: int,
    ) -> str:
        payload = {
            "scenario": scenario.value,
            "model": self.model,
            "max_tokens": max_tokens,
            "prompt": prompt,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return f"opex:ai:scenario:{scenario.value.lower()}:{digest}"

    def _request_sync(
        self,
        *,
        prompt: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        payload = {
            "model": self.model,
            "max_tokens": max(1, int(max_tokens)),
            "temperature": 0.7,
            "system": SYSTEM_PERSONALITY.strip(),
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }

        request = urllib_request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Anthropic API HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Anthropic API connection failed: {exc}") from exc

        data = json.loads(raw)
        blocks = data.get("content") or []
        parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part).strip()
        if not text:
            raise RuntimeError("Anthropic API returned an empty text response")

        usage = data.get("usage") or {}
        tokens_used = int(usage.get("input_tokens", 0) or 0) + int(
            usage.get("output_tokens", 0) or 0
        )
        return text, tokens_used

    async def generate(
        self,
        prompt: str,
        scenario: PromptScenario,
        max_tokens: int = 200,
    ) -> str:
        """Generate an AI response with 3 retries and scenario-aware caching."""
        started = time.perf_counter()
        scenario = _scenario_from_value(scenario)
        ttl = SCENARIO_TTLS.get(scenario, 0)
        cache_key = self._build_cache_key(prompt, scenario, max_tokens)

        cached = await self._cache_get(cache_key) if ttl > 0 else None
        if cached:
            logger.info(
                "scenario=%s tokens_used=0 latency_ms=%.1f cached=true",
                scenario.value,
                (time.perf_counter() - started) * 1000,
            )
            return cached

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                text, tokens_used = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._request_sync,
                        prompt=prompt,
                        max_tokens=max_tokens,
                    ),
                    timeout=self.timeout_seconds + 1.0,
                )
                await self._cache_set(cache_key, text, ttl)
                logger.info(
                    "scenario=%s tokens_used=%s latency_ms=%.1f cached=false",
                    scenario.value,
                    tokens_used,
                    (time.perf_counter() - started) * 1000,
                )
                return text
            except (asyncio.TimeoutError, RuntimeError, ValueError, json.JSONDecodeError, AnthropicError) as exc:
                last_error = exc
                if attempt < 2:
                    delay = 0.8 * (2 ** attempt)
                    await asyncio.sleep(delay)

        logger.error(
            "scenario=%s tokens_used=0 latency_ms=%.1f cached=false status=fallback error=%s",
            scenario.value,
            (time.perf_counter() - started) * 1000,
            last_error,
        )
        return FALLBACK_RESPONSES[scenario]


prompt_engine = PromptEngine()
ai_client = AIClient()


__all__ = [
    "AIClient",
    "FALLBACK_RESPONSES",
    "PromptEngine",
    "PromptScenario",
    "SCENARIO_TTLS",
    "SYSTEM_PERSONALITY",
    "ai_client",
    "build_cache_key",
    "build_dynamic_prompt",
    "prompt_engine",
]
