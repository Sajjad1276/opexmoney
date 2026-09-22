from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler


SCHEDULER_JOB_META: dict[str, tuple[str, str]] = {
    "rate_engine_15m": ("هر ۱۵ دقیقه", "بازمحاسبه نرخ ملت‌ها"),
    "world_event_engine_15m": ("هر ۱۵ دقیقه", "تولید رویدادهای داده‌محور جهان"),
    "nation_membership_reconciliation_15m": ("هر ۱۵ دقیقه", "همگام‌سازی اعضای ملت"),
    "nation_rank_hourly": ("ساعتی", "به‌روزرسانی رتبه ملت‌ها"),
    "governance_cycle": ("دقیقه ۵ هر ساعت", "چرخه حکمرانی"),
    "daily_market_reset": ("هر روز ۰۰:۰۰", "ریست متریک روزانه بازار"),
    "nation_war_resolution_15m": ("هر ۱۵ دقیقه", "تسویه جنگ‌های منقضی"),
    "nation_join_request_expiration": ("هر ۱۵ دقیقه", "انقضای درخواست عضویت"),
    "nation_weekly_ai_report": ("دوشنبه ۰۹:۰۰", "گزارش هفتگی AI"),
    "price_alert_checker_5m": ("هر ۵ دقیقه", "بررسی هشدار قیمت"),
    "ai_world_5m": ("هر ۵ دقیقه", "چرخه دنیای AI"),
    "portfolio_live_update_10s": ("هر ۱۰ ثانیه", "به‌روزرسانی زنده پرتفولیو"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def persist_scheduler_job_state(
    scheduler: AsyncIOScheduler,
    redis,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    if redis is None:
        return
    job = scheduler.get_job(job_id)
    values: dict[str, Any] = {
        "status": status,
        "updated_at": _now_iso(),
    }
    if job is not None:
        next_run_time = getattr(job, "next_run_time", None)
        if next_run_time is None:
            next_run_time = getattr(job, "next_fire_time", None)
        values["paused"] = "1" if next_run_time is None else "0"
        values["next_run"] = next_run_time.isoformat() if next_run_time else ""
    if status in {"success", "error", "missed"}:
        values["last_run"] = _now_iso()
    if error:
        values["last_error"] = str(error)[:1000]
    try:
        await redis.hset(f"opex:scheduler:job:{job_id}", mapping=values)
        await redis.expire(f"opex:scheduler:job:{job_id}", 7 * 24 * 3600)
    except Exception:
        return


def install_scheduler_monitor(
    scheduler: AsyncIOScheduler,
    redis,
) -> None:
    if redis is None:
        return

    async def seed() -> None:
        for job in scheduler.get_jobs():
            next_run_time = getattr(job, "next_run_time", None)
            if next_run_time is None:
                next_run_time = getattr(job, "next_fire_time", None)
            await persist_scheduler_job_state(
                scheduler,
                redis,
                job.id,
                status="paused" if next_run_time is None else "scheduled",
            )

    def listener(event) -> None:
        status = "success"
        error = None
        if getattr(event, "exception", None) is not None:
            status = "error"
            error = str(event.exception)
        elif event.code == EVENT_JOB_MISSED:
            status = "missed"
        try:
            asyncio.get_running_loop().create_task(
                persist_scheduler_job_state(
                    scheduler,
                    redis,
                    event.job_id,
                    status=status,
                    error=error,
                )
            )
        except RuntimeError:
            return

    scheduler.add_listener(
        listener,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    asyncio.get_running_loop().create_task(seed())


async def scheduler_control_loop(
    scheduler: AsyncIOScheduler,
    redis,
) -> None:
    if redis is None:
        return
    while True:
        item = await redis.blpop("opex:admin:scheduler:commands", timeout=2)
        if not item:
            continue
        try:
            payload = json.loads(item[1])
            job_id = str(payload.get("job_id") or "")
            command = str(payload.get("command") or "")
            if job_id not in SCHEDULER_JOB_META:
                continue
            job = scheduler.get_job(job_id)
            if job is None:
                continue
            if command == "pause":
                job.pause()
                status = "paused"
            elif command == "resume":
                job.resume()
                status = "scheduled"
            elif command == "run_now":
                job.modify(next_run_time=datetime.now(scheduler.timezone))
                status = "queued"
            else:
                continue
            await persist_scheduler_job_state(
                scheduler,
                redis,
                job_id,
                status=status,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
