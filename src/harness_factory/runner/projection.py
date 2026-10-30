"""Campaign projection and preflight (MOD-05): what a plan will cost, per quota bucket, before it starts.

Each planned run is estimated from history, most specific first:

  1. same model, recipe and task       median of its scored clean runs
  2. same model and recipe             median over its tasks
  3. same recipe, same provider kind   any model (scripted runs never stand in for real models, and vice versa)
  4. a default per shape               V1 / V2 / V3, from Phase 1 (gpt-oss-120b on V2: about 6 calls, 5k tokens)

Per bucket (the provider, or provider|model with per-model limits) that gives requests, tokens and dollars, and
from today's remaining quota and the daily limits: how many runs today covers, how many days the plan needs,
and the minutes of pacing the per-minute limits impose. A multi-day plan is normal on free tiers: runs past
today's quota end INCOMPLETE and `--resume` finishes them after the reset.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from pydantic import BaseModel

from harness_factory.recipes.model import Variant
from harness_factory.results.cost import is_clean
from harness_factory.results.store import UNSCORED

# (model calls, tokens) per run when nothing has been measured yet
SHAPE_DEFAULTS = {Variant.V1_SINGLE_CALL: (1, 2_000), Variant.V2_TOOL_LOOP: (6, 6_000),
                  Variant.V3_WORKFLOW_GRAPH: (10, 14_000), Variant.V4_PLANNER: (8, 12_000)}
INPUT_SHARE = 0.86       # Phase 1: 82,860 of 96,093 tokens were input; used to price a token estimate


class RunEstimate(BaseModel):
    calls: float
    tokens: float
    usd: float | None        # measured, when history has it
    source: str              # "task", "recipe", "recipe (any model)", "default"


class BucketProjection(BaseModel):
    bucket: str
    runs: int
    requests: int
    tokens: int
    usd: float
    runs_today: int          # runs that fit today's remaining quota, in plan order
    days: int                # calendar days including today (1 = finishes today)
    pacing_min: float        # minutes the per-minute limits impose, whatever the day limits
    sources: dict[str, int]  # estimate source -> runs


class Estimator:
    def __init__(self, rows: list[dict], scripted_providers: set[str]):
        self.scripted = scripted_providers
        by_task, by_recipe, by_recipe_kind = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in rows:
            if r["status"] in UNSCORED or not is_clean(r):
                continue
            sample = (r["model_calls"], r["tokens_in"] + r["tokens_out"], r["usd"])
            by_task[(r["canonical_model"], r["recipe_id"], r["task_version"])].append(sample)
            by_recipe[(r["canonical_model"], r["recipe_id"])].append(sample)
            by_recipe_kind[(r["recipe_id"], r["provider"] in self.scripted)].append(sample)
        self.levels = [("task", by_task), ("recipe", by_recipe), ("recipe (any model)", by_recipe_kind)]

    def estimate(self, model: str, provider: str, recipe, task_version: str) -> RunEstimate:
        keys = [(model, recipe.id, task_version), (model, recipe.id), (recipe.id, provider in self.scripted)]
        for (name, table), key in zip(self.levels, keys):
            samples = table.get(key)
            if samples:
                usd = statistics.median(s[2] for s in samples)
                return RunEstimate(calls=statistics.median(s[0] for s in samples),
                                   tokens=statistics.median(s[1] for s in samples),
                                   usd=usd if usd > 0 else None, source=name)
        calls, tokens = SHAPE_DEFAULTS.get(recipe.variant, SHAPE_DEFAULTS[Variant.V2_TOOL_LOOP])
        return RunEstimate(calls=calls, tokens=tokens, usd=None, source="default")


def project(f, specs) -> list[BucketProjection]:
    """Projection per quota bucket for the given RunSpecs, in plan order."""
    scripted = {p.id for p in f.providers.providers if p.kind == "scripted"}
    est = Estimator(f.store.rows() if f.store else [], scripted)
    per: dict[str, list[tuple[RunEstimate, float]]] = defaultdict(list)
    for s in specs:
        recipe = f.recipes[s.recipe_id]
        e = est.estimate(s.canonical_model, s.provider, recipe, f.tasks.get(s.task_id).key())
        served = f.providers.served(s.provider, s.canonical_model)
        usd = e.usd if e.usd is not None else served.cost(int(e.tokens * INPUT_SHARE), int(e.tokens * (1 - INPUT_SHARE)))
        per[f.providers.bucket(s.provider, s.canonical_model)].append((e, usd))
    out = []
    for bucket, items in per.items():
        lim = f.providers.limits(bucket)
        left = f.ledger.remaining(bucket)
        requests = sum(e.calls for e, _ in items)
        tokens = sum(e.tokens for e, _ in items)
        # runs that fit today's remaining quota, in plan order
        r_left = math.inf if left["rpd"] is None else max(0, left["rpd"])
        t_left = math.inf if left["tpd"] is None else max(0, left["tpd"])
        today = 0
        for e, _ in items:
            if e.calls > r_left or e.tokens > t_left:
                break
            r_left -= e.calls
            t_left -= e.tokens
            today += 1
        # whole days for the rest, at the daily limits
        rest = items[today:]
        extra = 0
        if rest:
            need_r, need_t = sum(e.calls for e, _ in rest), sum(e.tokens for e, _ in rest)
            extra = max(math.ceil(need_r / lim.rpd) if lim.rpd else 0, math.ceil(need_t / lim.tpd) if lim.tpd else 0, 1)
        pacing = max(requests / lim.rpm if lim.rpm else 0.0, tokens / lim.tpm if lim.tpm else 0.0)
        sources: dict[str, int] = defaultdict(int)
        for e, _ in items:
            sources[e.source] += 1
        out.append(BucketProjection(bucket=bucket, runs=len(items), requests=round(requests), tokens=round(tokens),
                                    usd=round(sum(u for _, u in items), 4), runs_today=today, days=1 + extra,
                                    pacing_min=round(pacing, 1), sources=dict(sources)))
    return sorted(out, key=lambda p: p.bucket)
