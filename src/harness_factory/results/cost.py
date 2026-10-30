"""Efficiency C and the cost baseline (B8).

C is relative: "what did this run cost compared with what the task usually costs?"

    C = min(cost / median, 2) / 2             0 = free, 0.5 = at the median, 1 = twice the median or more
    E = max(0, E0 - w_c * C)                  E0 = the run's composite before cost (stored per run)

  (Requirements v1, "Metrics and effectiveness score".) C is linear below the median too, so a harness that
  solves a task for half the usual cost ranks above one that solves it at the usual cost.

- The median is per provider and task (task_version), over successful runs only. A class median would penalise
  every harness on the bigger task of a class; counting failed runs would make the median the price of giving
  up early; and pooling providers would score real runs against scripted replays.
- Cost is reference USD for a provider when every one of its successful runs is priced, otherwise total tokens
  (e.g. the scripted provider, whose price is 0). The basis per provider is recorded in the baseline.
- A baseline is frozen and content-addressed. Saving one and scoring against it keeps old numbers stable when
  new runs arrive; the "live" baseline is recomputed from whatever is in the store.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from harness_factory.hashing import content_hash
from harness_factory.results.model import ScoreWeights
from harness_factory.shapes.result import RunStatus

CostBasis = Literal["usd", "tokens"]
Row = Mapping[str, Any]          # a results-store row (see results.store.record_row)


def row_cost(row: Row, basis: CostBasis) -> float:
    return float(row["usd"]) if basis == "usd" else float(row["tokens_in"] + row["tokens_out"])


def efficiency(cost: float, median: float) -> float:
    if median <= 0:
        return 0.0
    return min(max(cost, 0.0) / median, 2.0) / 2.0


def weight_profile_of(task_class: str) -> str:
    """Task class keys are depth|side_effects|risk; the side-effect part picks the weight profile."""
    return task_class.split("|")[1].lower()


def baseline_key(row: Row) -> str:
    """Medians are per provider and task: a scripted replay run and a real Groq run never share a median."""
    return f"{row['provider']}|{row['task_version']}"


class CostBaseline(BaseModel):
    bases: dict[str, CostBasis]               # provider -> cost basis used for that provider's runs
    medians: dict[str, float]                 # "provider|task_version" -> median cost of successful runs
    counts: dict[str, int]                    # same key -> runs the median was taken over
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash({"bases": self.bases, "medians": self.medians}, 12)

    @property
    def basis(self) -> str:
        """One word for display: the shared basis, or "mixed" when providers differ."""
        kinds = set(self.bases.values())
        return kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else "tokens")

    @staticmethod
    def from_rows(rows: Iterable[Row], note: str = "") -> CostBaseline:
        rows = list(rows)
        bases: dict[str, CostBasis] = {}
        for provider in sorted({r["provider"] for r in rows}):
            ok = [r for r in rows if r["provider"] == provider and r["status"] == RunStatus.SUCCESS.value]
            bases[provider] = "usd" if ok and all(r["usd"] > 0 for r in ok) else "tokens"
        by_key: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if r["status"] == RunStatus.SUCCESS.value:
                by_key[baseline_key(r)].append(row_cost(r, bases[r["provider"]]))
        unsolved = {baseline_key(r) for r in rows} - set(by_key)      # nobody solved it: use all its runs
        for r in rows:
            if baseline_key(r) in unsolved:
                by_key[baseline_key(r)].append(row_cost(r, bases[r["provider"]]))
        return CostBaseline(bases=bases, medians={k: statistics.median(v) for k, v in sorted(by_key.items())},
                            counts={k: len(v) for k, v in sorted(by_key.items())}, note=note)

    def c(self, row: Row) -> float | None:
        """Efficiency C for one run, or None if this baseline has no median for its provider and task."""
        median = self.medians.get(baseline_key(row))
        basis = self.bases.get(row["provider"])
        return None if median is None or basis is None else efficiency(row_cost(row, basis), median)

    def adjusted(self, row: Row) -> float:
        """Cost-adjusted E for one run. A provider/task outside the baseline gets C = 0."""
        e0 = float(row["composite"])
        if e0 <= 0:
            return 0.0
        w_c = ScoreWeights.for_profile(weight_profile_of(row["task_class"])).w_c
        return max(0.0, e0 - w_c * (self.c(row) or 0.0))
