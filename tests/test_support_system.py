from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.keyboards.inline import support_panel_keyboard, support_result_keyboard
from app.services.support.code_repair import _policy_ok
from app.services.support.models import (
    RepairAction,
    RepairResult,
    SupportCategory,
    SupportCheck,
    SupportDiagnosis,
)
from app.services.support.support_service import SupportService


class FakeState:
    def __init__(self) -> None:
        self.data = {"support_previous_state": "MarketStates:WAITING_BUY_AMOUNT"}

    async def get_data(self) -> dict:
        return dict(self.data)


@pytest.mark.asyncio
async def test_support_service_keeps_repair_decision_deterministic(monkeypatch) -> None:
    diagnosis = SupportDiagnosis(
        category=SupportCategory.FSM,
        summary="وضعیت بازار گیر کرده است.",
        root_cause="FSM روی مرحله خرید مانده است.",
        confidence=0.93,
        checks=(SupportCheck("user", True, "ok"),),
        repair_action=RepairAction.RESET_FSM,
        market_retry=True,
    )
    repair = RepairResult(
        action=RepairAction.RESET_FSM,
        applied=True,
        verified=True,
        detail="state reset",
    )

    async def fake_diagnose(*args, **kwargs):
        return diagnosis

    async def fake_repair(*args, **kwargs):
        return repair

    async def fake_reply(*args, **kwargs):
        return "<b>رفع شد.</b>"

    monkeypatch.setattr(
        "app.services.support.support_service.diagnose_support_issue",
        fake_diagnose,
    )
    monkeypatch.setattr(
        "app.services.support.support_service.apply_safe_repair",
        fake_repair,
    )
    monkeypatch.setattr(
        "app.services.support.support_service.generate_support_reply",
        fake_reply,
    )

    result = await SupportService.analyze(
        SimpleNamespace(),
        FakeState(),
        user_id=100,
        report="بازار باز نمی‌شود",
    )

    assert result.diagnosis.category is SupportCategory.FSM
    assert result.repair.applied is True
    assert result.diagnosis.market_retry is True
    assert result.response_text == "<b>رفع شد.</b>"


def test_support_keyboard_contract() -> None:
    callbacks = {
        button.callback_data
        for row in support_panel_keyboard().inline_keyboard
        for button in row
    }
    assert {"support_check_recent", "support_close"} <= callbacks

    result_callbacks = {
        button.callback_data
        for row in support_result_keyboard(market_retry=True).inline_keyboard
        for button in row
    }
    assert {
        "support_new_report",
        "market_main",
        "back_to_dashboard",
    } <= result_callbacks


def test_repair_policy_has_no_economy_mutation_action() -> None:
    assert set(RepairAction) == {
        RepairAction.NONE,
        RepairAction.RESET_FSM,
        RepairAction.SYNC_HOME_NATION,
    }
