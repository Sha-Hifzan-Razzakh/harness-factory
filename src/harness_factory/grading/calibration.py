"""Judge calibration (GRD-03, ADR 0014): how far a judge agrees with people who scored the same deliverables.

A calibration set is a YAML file:

    name: <id>
    note: <what it is>
    rubrics:                       # named rubrics the items refer to (or an item carries its own `rubric:`)
      <rubric id>: {criteria: [{id, text, weight}], threshold: 0.7}
    items:
      - id: <item id>
        rubric: <rubric id>
        subject: |                 # the deliverable, as an agent would have written it
          ...
        human_score: 0.85          # a person's weighted score, 0..1, with the same criteria
        human_pass: true           # optional; default human_score >= threshold

`hfx judge calibrate` runs the judge over every item and reports Cohen's kappa on pass/fail (the decision that
feeds the outcome) and the mean absolute error of the scores. The record it stores (ResultsStore,
judge_calibrations) is keyed by the judge hash: editing the judge's model or prompts needs a new calibration.
A judge is trusted only when the latest record on >= MIN_ITEMS items has kappa >= MIN_KAPPA.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from harness_factory.grading.judge import MIN_ITEMS, MIN_KAPPA, RubricJudge
from harness_factory.grading.verdict import RubricVerdict
from harness_factory.hashing import content_hash
from harness_factory.tasks.model import Rubric


class CalibrationItem(BaseModel):
    id: str
    subject: str
    rubric: str | Rubric
    human_score: float = Field(ge=0, le=1)
    human_pass: bool | None = None


class CalibrationSet(BaseModel):
    name: str
    note: str = ""
    rubrics: dict[str, Rubric] = Field(default_factory=dict)
    items: list[CalibrationItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _refs(self) -> CalibrationSet:
        ids = [i.id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("calibration item ids must be unique")
        for i in self.items:
            if isinstance(i.rubric, str) and i.rubric not in self.rubrics:
                raise ValueError(f"item {i.id}: unknown rubric {i.rubric!r}")
        return self

    def rubric_of(self, item: CalibrationItem) -> Rubric:
        return self.rubrics[item.rubric] if isinstance(item.rubric, str) else item.rubric

    def human_pass(self, item: CalibrationItem) -> bool:
        if item.human_pass is not None:
            return item.human_pass
        return item.human_score >= self.rubric_of(item).threshold - 1e-9

    def content_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def load_calibration_set(path: Path) -> CalibrationSet:
    return CalibrationSet.model_validate(yaml.safe_load(Path(path).read_text()))


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa of two raters' pass/fail labels: (p_o - p_e) / (1 - p_e). None when undefined: no items,
    or both raters gave a single label to everything (p_e = 1), where agreement says nothing about skill."""
    if len(a) != len(b):
        raise ValueError("the two raters must label the same items")
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return None
    return (po - pe) / (1 - pe)


def mean_abs_error(a: list[float], b: list[float]) -> float | None:
    if not a:
        return None
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


class ItemResult(BaseModel):
    id: str
    human_score: float
    human_pass: bool
    judge_score: float | None
    judge_pass: bool | None
    error: str | None = None


class CalibrationRecord(BaseModel):
    """What the store keeps: judge hash -> agreement. n counts the items the judge graded (failures excluded)."""
    judge_hash: str
    provider: str
    model: str
    set_name: str
    set_hash: str
    n: int
    failed: int
    kappa: float | None
    mae: float | None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def qualifies(self) -> bool:
        return self.n >= MIN_ITEMS and self.kappa is not None and self.kappa >= MIN_KAPPA


class CalibrationReport(BaseModel):
    record: CalibrationRecord
    items: list[ItemResult]
    usd: float = 0.0
    tokens: int = 0

    def disagreements(self) -> list[ItemResult]:
        return [i for i in self.items if i.judge_pass is not None and i.judge_pass != i.human_pass]


def agreement(items: list[ItemResult]) -> tuple[int, float | None, float | None]:
    """(n graded, kappa on pass/fail, MAE on scores) over the items the judge graded."""
    done = [i for i in items if i.judge_score is not None]
    return (len(done), cohen_kappa([i.human_pass for i in done], [bool(i.judge_pass) for i in done]),
            mean_abs_error([i.human_score for i in done], [float(i.judge_score) for i in done]))


async def calibrate(judge: RubricJudge, cset: CalibrationSet, concurrency: int = 4) -> CalibrationReport:
    """Run the judge over every item (paced by the gateway's quota ledger) and measure agreement."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(item: CalibrationItem) -> RubricVerdict:
        async with sem:
            return await judge.score(cset.rubric_of(item), item.subject, check_id=item.id,
                                     label=f"calibrate|{cset.name}|{item.id}", calibrated=False)

    verdicts = await asyncio.gather(*(one(i) for i in cset.items))
    results = [ItemResult(id=i.id, human_score=i.human_score, human_pass=cset.human_pass(i),
                          judge_score=v.score if v.available else None, judge_pass=v.passed if v.available else None,
                          error=v.error if not v.available else None)
               for i, v in zip(cset.items, verdicts, strict=True)]
    n, kappa, mae = agreement(results)
    record = CalibrationRecord(judge_hash=judge.hash, provider=judge.config.provider, model=judge.config.model,
                               set_name=cset.name, set_hash=cset.content_hash(), n=n, failed=len(results) - n,
                               kappa=None if kappa is None else round(kappa, 6),
                               mae=None if mae is None else round(mae, 6))
    return CalibrationReport(record=record, items=results, usd=round(sum(v.usage.usd for v in verdicts), 9),
                             tokens=sum(v.usage.tokens_in + v.usage.tokens_out for v in verdicts))
