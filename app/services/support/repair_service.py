from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NationMember, User
from app.services.support.models import RepairAction, RepairResult, SupportDiagnosis


async def apply_safe_repair(
    session: AsyncSession,
    *,
    user_id: int,
    diagnosis: SupportDiagnosis,
) -> RepairResult:
    action = diagnosis.repair_action

    if action is RepairAction.NONE:
        detail = (
            "برای این مورد تعمیر خودکار امنی وجود ندارد."
            if diagnosis.code_fix_required
            else "نیازی به تعمیر خودکار نیست."
        )
        return RepairResult(action, False, True, detail)

    if action is RepairAction.RESET_FSM:
        return RepairResult(
            action,
            True,
            True,
            "وضعیت مرحله‌ای قدیمی در خروج از پشتیبانی پاک می‌شود.",
        )

    if action is RepairAction.SYNC_HOME_NATION:
        user = await session.get(User, user_id, with_for_update=True)
        membership = await session.scalar(
            select(NationMember)
            .where(
                NationMember.user_id == user_id,
                NationMember.is_active.is_(True),
            )
            .limit(1)
        )
        if user is None or membership is None:
            return RepairResult(
                action,
                False,
                False,
                "عضویت فعال برای اصلاح ملت اصلی پیدا نشد.",
            )

        user.home_nation_id = membership.nation_id
        await session.flush()

        verified = user.home_nation_id == membership.nation_id
        return RepairResult(
            action,
            True,
            verified,
            "ملت اصلی حساب با عضویت فعال همگام شد."
            if verified
            else "همگام‌سازی ملت اصلی تأیید نشد.",
        )

    return RepairResult(action, False, False, "عملیات تعمیر ناشناخته است.")


__all__ = ["apply_safe_repair"]
