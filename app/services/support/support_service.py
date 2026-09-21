from __future__ import annotations

from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.support_agent import generate_support_reply
from app.diagnostics.support_telemetry import get_recent_telemetry
from app.services.support.code_repair import repair_code
from app.services.support.diagnostic_service import diagnose_support_issue
from app.services.support.models import EngineeringResult, SupportResult
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
        """
        Run only the database-backed diagnostic and safe repair phase.

        The caller owns the transaction. No GitHub or long-running AI work is
        started from this phase.
        """
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
        await session.flush()

        return SupportResult(
            response_text="",
            diagnosis=diagnosis,
            repair=repair,
            engineering=None,
        )

    @staticmethod
    async def finalize(
        *,
        user_id: int,
        report: str,
        result: SupportResult,
    ) -> SupportResult:
        """
        Run code engineering only after the DB transaction has committed.

        This allows the agent to read source, create a branch, run CI, repair
        CI failures, and merge without holding a DB connection or lock.
        """
        engineering: EngineeringResult | None = None

        if result.diagnosis.code_fix_required:
            telemetry = await get_recent_telemetry(user_id)
            latest_error = telemetry["errors"][-1] if telemetry["errors"] else {}
            latest_event = telemetry["events"][-1] if telemetry["events"] else {}
            engineering = await repair_code(
                report=report,
                traceback=str(latest_error.get("traceback") or ""),
                event=str(latest_event.get("event") or ""),
            )

        response_text = await generate_support_reply(
            report=report,
            diagnosis=result.diagnosis,
            repair=result.repair,
            engineering=engineering,
        )
        return SupportResult(
            response_text=response_text,
            diagnosis=result.diagnosis,
            repair=result.repair,
            engineering=engineering,
        )


support_service = SupportService()


__all__ = ["SupportService", "support_service"]
