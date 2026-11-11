"""Campaign projection and preflight (MOD-05): what a plan will cost, per quota bucket, before it starts.

Each planned run is estimated from history, most specific first (medians of scored clean runs; scripted runs never
stand in for real models, and vice versa):

  1. task                     same model, recipe and task
  2. recipe                   same model and recipe, over its tasks
  3. task (same shape)        same model and task, any recipe of the same shape: how heavy this task is on this model
  4. recipe (scaled)          the recipe on other models, its tokens scaled by this model's typical run size over
                              theirs (each model's median tokens per run, over everything it ran)
  5. recipe (any model)       the recipe on other models, unscaled (this model has no history to scale by)
  6. ceiling (recipe caps)    nothing above matches: the recipe's own caps (max_steps calls, max_tokens tokens), an
                              upper bound, never a guess low enough to pass a budget it would break
  7. default                  no history at all of this provider kind: a default per shape, from Phase 1
                              (gpt-oss-120b on V2: about 6 calls, 5k tokens)

Levels 4-5 price tokens at the planned model's own rates: another model's dollars say nothing about this one's.

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
                  Variant.V3_WORKFLOW_GRAPH: (10, 14_000), Variant.V4_PLANNER: (8, 12_000),
                  Variant.V5_ORCHESTRATOR: (10, 16_000)}
INPUT_SHARE = 0.86       # Phase 1: 82,860 of 96,093 tokens were input; used to price a token estimate


CEILING = "ceiling (recipe caps)"


class RunEstimate(BaseModel):
    calls: float
    tokens: float
    usd: float | None        # measured on this model, when history has it; otherwise priced from the catalog
    source: str              # a level of the module docstring: "task", ..., "ceiling (recipe caps)", "default"

    @property
    def upper_bound(self) -> bool:
        return self.source == CEILING


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
    def __init__(self, rows: list[dict], scripted_providers: set[str], recipes: dict | None = None):
        """recipes: id -> recipe, for the shape of the recipes in history (level 3). Without it, level 3 is skipped."""
        self.scripted = scripted_providers
        shape = {rid: r.variant for rid, r in (recipes or {}).items()}
        by_task, by_recipe, by_task_shape = defaultdict(list), defaultdict(list), defaultdict(list)
        self._by_recipe_kind: dict[tuple, list] = defaultdict(list)   # (recipe, kind) -> (model, sample)
        model_tokens: dict[str, list[float]] = defaultdict(list)
        self._kinds: set[bool] = set()
        for r in rows:
            if r["status"] in UNSCORED or not is_clean(r):
                continue
            kind = r["provider"] in self.scripted
            sample = (r["model_calls"], r["tokens_in"] + r["tokens_out"], r["usd"])
            model = r["canonical_model"]
            by_task[(model, r["recipe_id"], r["task_version"])].append(sample)
            by_recipe[(model, r["recipe_id"])].append(sample)
            if r["recipe_id"] in shape:
                by_task_shape[(model, r["task_version"], shape[r["recipe_id"]])].append(sample)
            self._by_recipe_kind[(r["recipe_id"], kind)].append((model, sample))
            model_tokens[model].append(sample[1])
            self._kinds.add(kind)
        self._by_task, self._by_recipe, self._by_task_shape = by_task, by_recipe, by_task_shape
        self._typical = {m: statistics.median(t) for m, t in model_tokens.items() if t}

    @staticmethod
    def _median(samples: list[tuple], source: str, priced_here: bool) -> RunEstimate:
        usd = statistics.median(s[2] for s in samples) if priced_here else 0.0
        return RunEstimate(calls=statistics.median(s[0] for s in samples), tokens=statistics.median(s[1] for s in samples),
                           usd=usd if usd > 0 else None, source=source)

    def estimate(self, model: str, provider: str, recipe, task_version: str) -> RunEstimate:
        kind = provider in self.scripted
        if samples := self._by_task.get((model, recipe.id, task_version)):
            return self._median(samples, "task", priced_here=True)
        if samples := self._by_recipe.get((model, recipe.id)):
            return self._median(samples, "recipe", priced_here=True)
        if samples := self._by_task_shape.get((model, task_version, recipe.variant)):
            return self._median(samples, "task (same shape)", priced_here=True)
        others = [(m, s) for m, s in self._by_recipe_kind.get((recipe.id, kind), []) if m != model]
        if others and model in self._typical:
            target = self._typical[model]
            scaled = [(s[0], s[1] * target / self._typical[m], 0.0) for m, s in others if self._typical.get(m)]
            if scaled:
                return self._median(scaled, "recipe (scaled)", priced_here=False)
        if others:
            return self._median([s for _, s in others], "recipe (any model)", priced_here=False)
        if kind in self._kinds:
            return RunEstimate(calls=recipe.config.max_steps, tokens=recipe.config.max_tokens, usd=None, source=CEILING)
        calls, tokens = SHAPE_DEFAULTS.get(recipe.variant, SHAPE_DEFAULTS[Variant.V2_TOOL_LOOP])
        return RunEstimate(calls=calls, tokens=tokens, usd=None, source="default")


def project(f, specs) -> list[BucketProjection]:
    """Projection per quota bucket for the given RunSpecs, in plan order."""
    scripted = {p.id for p in f.providers.providers if p.kind == "scripted"}
    est = Estimator(f.store.rows() if f.store else [], scripted, f.recipes)
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
