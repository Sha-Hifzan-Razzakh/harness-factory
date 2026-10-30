"""EffectivenessRecord, FamilyScores, ScoreWeights, CellAggregate (B8)."""

from __future__ import annotations

import random
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunStatus, Violation

WeightProfile = Literal["read", "reversible", "irreversible"]

# From Requirements v1, "Metrics and effectiveness score".
_WEIGHTS: dict[WeightProfile, tuple[float, float, float, float]] = {
    "read": (0.50, 0.15, 0.10, 0.25),
    "reversible": (0.45, 0.25, 0.15, 0.15),
    "irreversible": (0.40, 0.30, 0.20, 0.10),
}


class FamilyScores(BaseModel):
    outcome: float = 0.0          # O: partial-credit score 0..1
    robustness: float | None = None  # R: perturbed / clean pass rate, capped at 1; None until faults are injected (P2)
    trajectory: float = 1.0       # Q: 1 - share of invalid and wasted calls
    efficiency: float = 0.0       # C: normalized cost, 0 = at or below class median
    safety: int = 0               # number of violations
    reliability: float | None = None  # seed spread, set at aggregation


class ScoreWeights(BaseModel):
    profile: WeightProfile
    w_o: float
    w_r: float
    w_q: float
    w_c: float
    version: str = "v1"

    @staticmethod
    def for_profile(profile: WeightProfile) -> ScoreWeights:
        o, r, q, c = _WEIGHTS[profile]
        return ScoreWeights(profile=profile, w_o=o, w_r=r, w_q=q, w_c=c)

    def composite(self, s: FamilyScores, critical_violation: bool) -> float:
        """E = sum(w_i * x_i) / sum(w_i) over measured positive families, minus w_c * C; 0 on a critical violation.

        Two rules keep a run that achieved nothing at E = 0:
          - trajectory counts as Q * O: a clean path to a wrong end earns nothing;
          - an unmeasured family (R on clean runs) is left out and the rest renormalized,
            rather than defaulting to 1.0 and handing out free credit.
        """
        if critical_violation:
            return 0.0
        parts = [(self.w_o, s.outcome), (self.w_q, s.trajectory * s.outcome)]
        if s.robustness is not None:
            parts.append((self.w_r, s.robustness))
        positive = sum(w * x for w, x in parts) / sum(w for w, _ in parts)
        return positive - self.w_c * s.efficiency


class EffectivenessRecord(BaseModel):
    run_key: RunKey
    task_class: str
    recipe_id: str
    status: RunStatus
    scores: FamilyScores
    composite: float
    cost: CostLedger
    violations: list[Violation] = Field(default_factory=list)
    grades: list[dict] = Field(default_factory=list)
    trace_path: str | None = None
    output: str | None = None                                       # the run's final answer text
    messages: list[dict] = Field(default_factory=list, repr=False)  # full transcript; not written to the store


class CellAggregate(BaseModel):
    cell_id: str
    n: int
    mean: float
    spread: float
    ci95: tuple[float, float]
    pass_rate: float

    @staticmethod
    def from_values(cell_id: str, values: list[float], passes: list[bool], seed: int = 0) -> CellAggregate:
        n = len(values)
        mean = statistics.fmean(values) if values else 0.0
        spread = statistics.pstdev(values) if n > 1 else 0.0
        if n > 1:
            rng = random.Random(seed)
            boots = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(2000))
            ci = (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1])
        else:
            ci = (mean, mean)
        return CellAggregate(cell_id=cell_id, n=n, mean=mean, spread=spread, ci95=ci,
                             pass_rate=(sum(passes) / n) if n else 0.0)
