from __future__ import annotations

from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.support_agent import generate_support_reply
from app.services.support.diagnostic_service import diagnose_support_issue
from app.services.support.models import SupportResult
from app.services.support.repair_service import apply_safe_repair
from app.states.support import SupportStates
from config import settings


class SupportService:
    @staticmethod
    async def begin(state: FSMContext) -> None:
        previous_state = await state.get_state()
        if previous_state and previous_state.startswith("SupportStates:"):
            previous_state = None
        await state.set_state(SupportStates.WAITING_REPORT)
        await state.update_data(support_previous_state=previous_state)

    @staticmethod
    async def analyze(
        session: AsyncSession,
        state: FSMContext,
        *,
        user_id: int,
        report: str,
    ) -> SupportResult:
        data = await state.get_data()
        previous_state = data.get("support_previous_state")
        clean_report = " ".join((report or "").split())[
            : settings.support_max_report_chars
        ]

        diagnosis = await diagnose_support_issue(
            session,
            user_id=user_id,
            report=clean_report,
            previous_fsm_state=previous_state,
        )
        repair = await apply_safe_repair(
            session,
            user_id=user_id,
            diagnosis=diagnosis,
        )
        response_text = await generate_support_reply(
            report=clean_report,
            diagnosis=diagnosis,
            repair=repair,
        )
        return SupportResult(
            response_text=response_text,
            diagnosis=diagnosis,
            repair=repair,
        )


support_service = SupportService()


__all__ = ["SupportService", "support_service"]
