"""Grader protocol, registry and GradingPipeline (B8). Graders read the environment after the run, not the model's
claims.

A grader declares
  id       what tasks name in grader_ids
  version  recorded with every grade it gives; changing it is a scoring change (call it out in the PR)
  family   which score family it feeds: outcome (O, and the run's status), trajectory (Q), safety, or None (its
           grade is stored but feeds nothing)
  combine  how it combines with the task's other graders of the same family:
             outcome / trajectory: "product" (default) or "min" over the graders' scores
             safety: "count" (default; a failed grade adds one violation) or "critical" (a failed grade is a
                     critical violation: E = 0 and the run FAILs)

Register one with @register_grader; default_pipeline() holds every registered grader, so a new grader needs no
core edit. A task with a single outcome grader (state_diff) and a single trajectory grader scores exactly as it
did before graders were pluggable: O = state_diff's score, Q = trajectory's score.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from harness_factory.env.state import EnvState, StateDiff
from harness_factory.observability.span import Span
from harness_factory.shapes.result import RunResult, Violation
from harness_factory.tasks.model import Task

Family = Literal["outcome", "trajectory", "safety"]
SCORE_RULES = ("product", "min")         # outcome, trajectory
SAFETY_RULES = ("count", "critical")     # safety


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
    # Set by the pipeline from the grader's declaration, so a stored grade says what produced it.
    version: str | None = None
    family: Family | None = None
    combine: str | None = None


class Grader(Protocol):
    id: str
    version: str
    family: Family | None
    combine: str

    def grade(self, ctx: GradeContext) -> Grade: ...


def check_declaration(g: Grader) -> None:
    """A grader must say its version, and a family and rule that exist."""
    gid = getattr(g, "id", None)
    if not gid or not isinstance(gid, str):
        raise ValueError(f"grader {g!r} has no id")
    if not isinstance(getattr(g, "version", None), str) or not g.version:
        raise ValueError(f"grader {gid!r} declares no version")
    family = getattr(g, "family", None)
    rule = getattr(g, "combine", None)
    if family is None:
        return
    allowed = SAFETY_RULES if family == "safety" else SCORE_RULES if family in ("outcome", "trajectory") else None
    if allowed is None:
        raise ValueError(f"grader {gid!r}: unknown family {family!r}; use outcome, trajectory, safety or None")
    if rule not in allowed:
        raise ValueError(f"grader {gid!r}: family {family} combines by {' or '.join(allowed)}, not {rule!r}")


# Filled at import time by @register_grader (like ShapeRegistry), never by a run.
_REGISTRY: dict[str, Callable[[], Grader]] = {}


def register_grader(factory: Callable[[], Grader]) -> Callable[[], Grader]:
    """Class decorator (or any zero-argument factory): default_pipeline() instantiates it."""
    g = factory()
    check_declaration(g)
    if g.id in _REGISTRY and _REGISTRY[g.id] is not factory:
        raise ValueError(f"grader {g.id!r} is already registered")
    _REGISTRY[g.id] = factory
    return factory


def unregister_grader(grader_id: str) -> None:
    """For tests that register a toy grader."""
    _REGISTRY.pop(grader_id, None)


def registered_graders() -> dict[str, Callable[[], Grader]]:
    _load_builtins()
    return dict(_REGISTRY)


def _load_builtins() -> None:
    import harness_factory.grading.state
    import harness_factory.grading.trajectory  # noqa: F401  (each registers its grader)


class FamilyInputs(BaseModel):
    """The graders' contribution to a run's FamilyScores: None = no grader of the task feeds that family."""
    outcome: float | None = None
    trajectory: float | None = None


def combine_families(grades: list[Grade]) -> FamilyInputs:
    """O and Q from the grades that feed them, combined by their declared rule. Safety graders work through
    their violations (added by the pipeline), so every part of the run that reads violations sees them."""
    out: dict[str, float] = {}
    for g in grades:
        if g.family not in ("outcome", "trajectory"):
            continue
        if g.family not in out:
            out[g.family] = g.score
        elif g.combine == "min":
            out[g.family] = min(out[g.family], g.score)
        else:
            out[g.family] = out[g.family] * g.score
    return FamilyInputs(**out)


class GradingPipeline:
    def __init__(self, graders: dict[str, Grader]) -> None:
        for g in graders.values():
            check_declaration(g)
        self.graders = graders

    def versions(self, grader_ids: list[str] | None = None) -> dict[str, str]:
        return {gid: g.version for gid, g in self.graders.items() if grader_ids is None or gid in grader_ids}

    def run(self, ctx: GradeContext) -> list[Grade]:
        chosen = []
        for gid in ctx.task.grader_ids:
            if gid not in self.graders:
                raise KeyError(f"task {ctx.task.key()} asks for unknown grader {gid!r}")
            chosen.append(self.graders[gid])
        rules: dict[str, set[str]] = {}
        for g in chosen:
            if g.family is not None:
                rules.setdefault(g.family, set()).add(g.combine)
        mixed = {f: sorted(r) for f, r in rules.items() if len(r) > 1}
        if mixed:     # product and min don't commute; one rule per family keeps the result order-free
            raise ValueError(f"task {ctx.task.key()}: graders of one family must share a combine rule, got {mixed}")
        out = []
        for g in chosen:
            grade = g.grade(ctx)
            grade.version, grade.family, grade.combine = g.version, g.family, g.combine if g.family else None
            if g.family == "safety" and not grade.passed:
                grade.violations.append(Violation(kind="grader_failed", critical=g.combine == "critical",
                                                  detail=g.id))
            out.append(grade)
        return out


def default_pipeline() -> GradingPipeline:
    """Every registered grader (the built-ins and any registered since)."""
    return GradingPipeline({gid: make() for gid, make in registered_graders().items()})
