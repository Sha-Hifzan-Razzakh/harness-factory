"""Grader protocol and GradingPipeline (B8). Graders read the environment after the run, not the model's claims."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from harness_factory.env.state import EnvState, StateDiff
from harness_factory.observability.span import Span
from harness_factory.shapes.result import RunResult, Violation
from harness_factory.tasks.model import Task


@dataclass
class GradeContext:
    task: Task
    result: RunResult
    before: EnvState
    after: EnvState
    diff: StateDiff
    workspace: Path
    db_path: Path
    spans: list[Span]
    max_steps: int
    remote: dict[str, Any] = field(default_factory=dict)   # server -> its test tenant's state after the run


class CheckResult(BaseModel):
    id: str
    passed: bool
    weight: float
    critical: bool = False
    detail: str = ""


class Grade(BaseModel):
    grader_id: str
    score: float                       # 0..1
    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    detail: str = ""


class Grader(Protocol):
    id: str

    def grade(self, ctx: GradeContext) -> Grade: ...


class GradingPipeline:
    def __init__(self, graders: dict[str, Grader]) -> None:
        self.graders = graders

    def unknown(self, task) -> list[str]:
        """Grader ids the task asks for that this pipeline doesn't have."""
        return [g for g in task.grader_ids if g not in self.graders]

    def check(self, task) -> None:
        """Fail before a run starts (and before any model call) when a task names a grader that doesn't exist."""
        unknown = self.unknown(task)
        if unknown:
            raise ValueError(f"task {task.key()} asks for unknown grader(s) {', '.join(map(repr, unknown))}; "
                             f"known: {', '.join(sorted(self.graders))}")

    def run(self, ctx: GradeContext) -> list[Grade]:
        out = []
        for gid in ctx.task.grader_ids:
            if gid not in self.graders:
                raise KeyError(f"task {ctx.task.key()} asks for unknown grader {gid!r}")
            out.append(self.graders[gid].grade(ctx))
        return out


def default_pipeline() -> GradingPipeline:
    from harness_factory.grading.state import StateDiffGrader
    from harness_factory.grading.trajectory import TrajectoryGrader
    return GradingPipeline({"state_diff": StateDiffGrader(), "trajectory": TrajectoryGrader()})
