"""RunResult and RunStatus (B4): what every shape returns."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from harness_factory.observability.span import Span
from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    NEEDS_INPUT = "NEEDS_INPUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    ERROR = "ERROR"
    INCOMPLETE = "INCOMPLETE"        # no equivalent provider left; not a harness failure


class Violation(BaseModel):
    kind: str                         # e.g. tool_not_allowed, injection_compliance
    critical: bool = False
    detail: str = ""


class RunResult(BaseModel):
    run_key: RunKey
    output: Any = None
    status: RunStatus = RunStatus.ERROR   # shapes report how they ended; grading sets SUCCESS/PARTIAL/FAIL
    finished: bool = False                # the shape reached its own end (not budget/policy/error)
    scored: bool = False                  # stopped for a harness reason: graded on the environment
                                          # (False = infrastructure or setup/crash: INCOMPLETE / ERROR)
    error: str | None = None
    trajectory: list[Span] = Field(default_factory=list)
    cost: CostLedger = Field(default_factory=CostLedger)
    violations: list[Violation] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
