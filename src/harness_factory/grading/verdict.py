"""RubricVerdict: what the rubric judge said about one rubric check of one run (grading/judge.py, ADR 0014).

Kept apart from the judge so the outcome grader (grading/state.py) and GradeContext can read verdicts without
importing the gateway.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JudgeUsage(BaseModel):
    """The judge's own model calls. Never part of the run's CostLedger, so never part of C."""
    model_calls: int = 0
    failed_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

    def __add__(self, other: JudgeUsage) -> JudgeUsage:
        return JudgeUsage(model_calls=self.model_calls + other.model_calls,
                          failed_calls=self.failed_calls + other.failed_calls,
                          tokens_in=self.tokens_in + other.tokens_in, tokens_out=self.tokens_out + other.tokens_out,
                          usd=round(self.usd + other.usd, 9))


class CriterionScore(BaseModel):
    id: str
    score: float                 # 0..1
    reason: str = ""


class RubricVerdict(BaseModel):
    """available=False: the judge could not give a grade (provider down, quota, setup, an unparseable reply, or
    the run was not scored). A missing or empty deliverable is available: it fails without asking the judge."""
    check_id: str
    judge_hash: str               # the judge: model, provider model id, prompt sources, sampling, protocol
    rubric_hash: str              # the rubric it scored against
    calibrated: bool              # a qualifying calibration record existed for judge_hash when it was graded
    available: bool
    score: float | None = None    # weighted criterion score, 0..1
    passed: bool | None = None    # score >= rubric threshold
    criteria: list[CriterionScore] = Field(default_factory=list)
    error: str | None = None      # why it is unavailable (or why the deliverable failed without a call)
    error_kind: str | None = None  # ErrorKind value of a judge failure
    subject_chars: int = 0
    truncated: bool = False
    usage: JudgeUsage = Field(default_factory=JudgeUsage)

    @property
    def counts(self) -> bool:
        """Whether this check carries its weight in the outcome: only a calibrated judge's available grade."""
        return self.available and self.calibrated

    def identity(self) -> dict[str, str]:
        return {"judge_hash": self.judge_hash, "rubric_hash": self.rubric_hash}
