from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SupportCategory(StrEnum):
    MARKET = "market"
    FSM = "fsm"
    DATA_INTEGRITY = "data_integrity"
    ACCOUNT = "account"
    RUNTIME = "runtime"
    GENERAL = "general"


class RepairAction(StrEnum):
    NONE = "none"
    RESET_FSM = "reset_fsm"
    SYNC_HOME_NATION = "sync_home_nation"


@dataclass(frozen=True)
class SupportCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class SupportDiagnosis:
    category: SupportCategory
    summary: str
    root_cause: str
    confidence: float
    checks: tuple[SupportCheck, ...] = ()
    evidence: tuple[str, ...] = ()
    repair_action: RepairAction = RepairAction.NONE
    code_fix_required: bool = False
    market_retry: bool = False


@dataclass(frozen=True)
class RepairResult:
    action: RepairAction
    applied: bool
    verified: bool
    detail: str


@dataclass(frozen=True)
class EngineeringResult:
    status: str
    summary: str
    changed_files: tuple[str, ...] = ()
    branch: str | None = None
    pull_request: int | None = None


@dataclass(frozen=True)
class SupportResult:
    response_text: str
    diagnosis: SupportDiagnosis
    repair: RepairResult
    engineering: EngineeringResult | None = None
