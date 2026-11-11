"""Allocation planner (P2, MOD-04, MOD-06; ADR 0011): which provider serves each cell of a campaign.

A campaign is first planned without providers: tasks x recipes x models x fault profiles x seeds. Then, model by
model in plan order, the planner picks a *serving group* among the active providers that serve the model
(catalog/providers.yaml, optionally narrowed to `providers`):

  - a single provider, or
  - an equivalent pool: providers with a current `equivalent` calibration for the model (ADR 0010,
    `results.calibration.pools`: every pair inside has its own verdict and each provider still serves the model id
    it was calibrated with). Only a pool may split a model's runs, and its runs aggregate into one pooled cell.

Without such a verdict every cell of the model goes to one provider, so a model's evidence never mixes providers
that were not shown to behave alike (MOD-04: within a cell the provider never changes; MOD-06). The RunKey keeps
its provider: the allocation only decides which provider's RunSpecs are planned.

Capacity. Each run is estimated per provider with the projection's Estimator (MOD-05, history first, then shape
defaults). A provider's capacity is what is left today in its quota bucket (QuotaLedger; `scope: model` gives each
model its own bucket) minus what earlier models of this same plan already took from that bucket. A bucket the
ledger says is spent today is excluded, as the projection's preflight refuses it.

Ranking, by the campaign's strategy (ties end on the label, so the same inputs give the same allocation):

  capacity (default)   days needed, then spare runs left today, then more providers, then list-price dollars
  cost                 list-price dollars, then days needed, then spare runs, then more providers

A pool always has at least the spare capacity of its best member, so under `capacity` an equivalent pool wins.

Split. A pool divides the model's runs in proportion to each member's remaining capacity today, counted in runs and
capped at the model's run count (so two unlimited providers split evenly; a spent member gets nothing), by largest
remainder. Runs are interleaved in plan order, so every cell gets runs from every member.

Refusals, as the projection's preflight: a model no candidate serves, a model whose every candidate is spent today
or needs more days than `deadline_days`, and a plan whose list-price total exceeds `budget_usd`. Every decision,
refusals included, is logged in var/decisions/ (the selector's convention); an allocation that refuses nothing is
saved, content-addressed, in var/allocations/<id>.json and runs from there (`hfx allocate run <id>`), so `--resume`
finds the same RunKeys. Re-planning mid-campaign (MOD-07) is not part of this.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from harness_factory import settings
from harness_factory.hashing import content_hash
from harness_factory.results.calibration import latest, pools
from harness_factory.runner.projection import INPUT_SHARE, Estimator
from harness_factory.runner.runner import RunSpec, plan

STRATEGIES = ("capacity", "cost")
Strategy = Literal["capacity", "cost"]
INF = math.inf


class GroupScore(BaseModel):
    """One serving group considered for a model, with the numbers it was ranked by."""
    label: str                               # provider id, or the pool label ("a+b") its cells aggregate under
    providers: list[str]
    runs: dict[str, int] = {}                # provider -> runs it would serve
    capacity: dict[str, float | None] = {}   # provider -> runs that fit today before this model (None = no limit)
    days: int = 0
    usd: float = 0.0
    spare: float | None = None               # runs still fitting today after this model (None = no limit)
    excluded: str | None = None


class AllocationDecision(BaseModel):
    kind: Literal["allocation"] = "allocation"
    canonical_model: str
    strategy: Strategy
    rule: str
    chosen: str | None                       # label of the serving group; None = refused
    providers: dict[str, int] = {}           # provider -> runs
    usd: float = 0.0                         # list-price estimate of the chosen group's runs
    cells: dict[str, str] = {}               # "task class|recipe|model" -> provider or pool label
    candidates: list[GroupScore] = []
    calibration: list[str] = []              # the verdict in force for every pair of candidates
    refused: str | None = None
    made_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash(self.model_dump(mode="json", exclude={"made_at"}), 12)

    def summary(self) -> str:
        if self.refused:
            return f"{self.canonical_model}: refused: {self.refused}"
        runs = ", ".join(f"{p} {n}" for p, n in self.providers.items())
        return f"{self.canonical_model}: {self.chosen} ({runs}): {self.rule}"


class Allocation(BaseModel):
    strategy: Strategy
    recipe_ids: list[str]
    models: list[str]
    seeds: int
    task_ids: list[str] | None = None
    faults: list[str | None] = [None]
    candidates: list[str] | None = None      # providers the planner was limited to; None = every active one
    deadline_days: int | None = None
    budget_usd: float | None = None
    specs: list[RunSpec]
    decisions: list[AllocationDecision]
    refusals: list[str] = []
    usd: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash({"strategy": self.strategy, "specs": [s.model_dump() for s in self.specs]}, 12)

    @property
    def ok(self) -> bool:
        return not self.refusals


# ------------------------------------------------------------------ quota arithmetic

def _fit(r: float, t: float, calls: float, tokens: float) -> float:
    """Whole runs of (calls, tokens) that fit in r requests and t tokens; INF when neither is limited."""
    n = INF
    if r != INF:
        n = min(n, math.floor(r / calls) if calls > 0 else INF)
    if t != INF:
        n = min(n, math.floor(t / tokens) if tokens > 0 else INF)
    return n


class _Bucket:
    """A quota bucket as this plan sees it: today's remaining quota from the ledger, and the runs already planned
    onto it by earlier models."""

    def __init__(self, f, name: str):
        self.name = name
        self.limits = f.providers.limits(name)
        self.exhausted = f.ledger.exhausted(name)
        left = f.ledger.remaining(name)
        self.r0 = 0.0 if self.exhausted else (INF if left["rpd"] is None else max(0, left["rpd"]))
        self.t0 = 0.0 if self.exhausted else (INF if left["tpd"] is None else max(0, left["tpd"]))
        self.items: list[tuple[float, float]] = []

    def left(self, extra: list[tuple[float, float]] = ()) -> tuple[float, float]:
        items = self.items + list(extra)
        return (max(0.0, self.r0 - sum(c for c, _ in items)), max(0.0, self.t0 - sum(t for _, t in items)))

    def days(self, extra: list[tuple[float, float]] = ()) -> int:
        """Calendar days to finish everything planned onto this bucket, counted as the projection counts them:
        runs that fit today in plan order, the rest at the daily limits."""
        items = self.items + list(extra)
        r, t, today = self.r0, self.t0, 0
        for calls, tokens in items:
            if calls > r or tokens > t:
                break
            r, t, today = r - calls, t - tokens, today + 1
        rest = items[today:]
        if not rest:
            return 1
        lim = self.limits
        need_r, need_t = sum(c for c, _ in rest), sum(t for _, t in rest)
        return 1 + max(math.ceil(need_r / lim.rpd) if lim.rpd else 0, math.ceil(need_t / lim.tpd) if lim.tpd else 0, 1)


def _shares(capacity: dict[str, float], n: int) -> dict[str, int]:
    """n runs split in proportion to capacity capped at n (largest remainder); no capacity anywhere: evenly."""
    w = {p: min(c, n) for p, c in capacity.items()}
    total = sum(w.values())
    if total == 0:
        w, total = dict.fromkeys(capacity, 1), len(capacity)
    exact = {p: n * w[p] / total for p in w}
    out = {p: math.floor(exact[p]) for p in w}
    for p in sorted(w, key=lambda p: (out[p] - exact[p], p))[: n - sum(out.values())]:
        out[p] += 1
    return out


def _interleave(shares: dict[str, int], n: int) -> list[str]:
    """One provider per run, in plan order, each reaching its share evenly spread (so every cell gets each)."""
    taken = dict.fromkeys(shares, 0)
    order = []
    for _ in range(n):
        p = min((q for q in shares if taken[q] < shares[q]), key=lambda q: ((taken[q] + 1) / shares[q], q))
        taken[p] += 1
        order.append(p)
    return order


def _num(x: float) -> float | None:
    return None if x == INF else x


# ------------------------------------------------------------------ the planner

def _rank(g: GroupScore, strategy: str) -> tuple:
    spare = -(INF if g.spare is None else g.spare)
    if strategy == "cost":
        return (round(g.usd, 6), g.days, spare, -len(g.providers), g.label)
    return (g.days, spare, -len(g.providers), round(g.usd, 6), g.label)


def _calibration_notes(model: str, candidates: list[str], records: list[dict], pooled: dict) -> list[str]:
    in_force = latest(records)
    pool_of = {p: label for (m, p, _), label in pooled.items() if m == model}
    notes = []
    for a, b in combinations(sorted(candidates), 2):
        eq = in_force.get((model, (a, b)))
        if eq is None:
            notes.append(f"{a}~{b}: not calibrated")
        elif eq.verdict == "equivalent" and (a not in pool_of or pool_of.get(a) != pool_of.get(b)):
            notes.append(f"{a}~{b}: equivalent ({eq.id}) but not in force: a served model id changed")
        else:
            notes.append(f"{a}~{b}: {eq.verdict} ({eq.id}, {eq.n} pairs)")
    return notes


def allocate(f, recipe_ids: list[str] | str, models: list[str], seeds: int = 1, task_ids: list[str] | None = None,
             faults: list[str | None] | None = None, providers: list[str] | None = None,
             strategy: Strategy = "capacity", deadline_days: int | None = None, budget_usd: float | None = None,
             equivalences: list[dict] | None = None) -> Allocation:
    """Assign every planned run to a provider. `equivalences`: saved calibration records (default: the store's)."""
    from harness_factory.provision.provisioner import task_class

    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; known: {', '.join(STRATEGIES)}")
    recipe_ids = [recipe_ids] if isinstance(recipe_ids, str) else list(recipe_ids)
    for p in providers or []:
        f.providers.get(p)                                   # unknown provider: fail before planning
    demand = plan(f, recipe_ids, "", models, seeds, task_ids=task_ids, faults=faults)    # provider filled below
    records = equivalences if equivalences is not None else (f.store.equivalences() if f.store else [])
    pooled = pools(records, f.providers)
    scripted = {p.id for p in f.providers.providers if p.kind == "scripted"}
    est = Estimator(f.store.rows() if f.store else [], scripted, f.recipes)
    buckets: dict[str, _Bucket] = {}
    assigned: dict[int, str] = {}                            # index in demand -> provider
    decisions, refusals = [], []

    for model in models:
        idx = [i for i, s in enumerate(demand) if s.canonical_model == model]
        candidates = [p for p in f.providers.providers_for(model) if providers is None or p in providers]
        cells = {i: f"{task_class(f.tasks.get(demand[i].task_id), f.declared)}|{demand[i].recipe_id}|{model}"
                 for i in idx}
        if not candidates:
            why = "no active provider serves it" + (f" among {', '.join(providers)}" if providers else "")
            decisions.append(AllocationDecision(canonical_model=model, strategy=strategy, rule="refused",
                                                chosen=None, refused=why))
            refusals.append(f"{model}: {why}")
            continue
        # per provider: (calls, tokens, usd) of every run of this model, and its bucket
        runs: dict[str, list[tuple[float, float, float]]] = {}
        for p in candidates:
            served = f.providers.served(p, model)
            out = []
            for i in idx:
                s = demand[i]
                e = est.estimate(model, p, f.recipes[s.recipe_id], f.tasks.get(s.task_id).key())
                usd = e.usd if e.usd is not None else served.cost(int(e.tokens * INPUT_SHARE),
                                                                  int(e.tokens * (1 - INPUT_SHARE)))
                out.append((e.calls, e.tokens, usd))
            runs[p] = out
            b = f.providers.bucket(p, model)
            buckets.setdefault(b, _Bucket(f, b))

        bkt = {p: buckets[f.providers.bucket(p, model)] for p in candidates}
        n = len(idx)
        avg = {p: (sum(r[0] for r in runs[p]) / n, sum(r[1] for r in runs[p]) / n) if n else (1.0, 1.0)
               for p in candidates}

        groups: list[tuple[str, list[str]]] = [(p, [p]) for p in candidates]
        by_label: dict[str, list[str]] = defaultdict(list)
        for (m, p, _), label in pooled.items():
            if m == model and p in candidates:
                by_label[label].append(p)
        pool_groups = [(label, sorted(ps)) for label, ps in sorted(by_label.items()) if len(ps) > 1]
        groups += pool_groups

        scored: list[tuple[GroupScore, list[str]]] = []
        for label, members in groups:
            g = GroupScore(label=label, providers=members)
            spent = [bkt[p].name for p in members if bkt[p].exhausted]
            live = [p for p in members if not bkt[p].exhausted]
            if not live:
                g.excluded = (f"today's quota is already spent ({', '.join(spent)}); "
                              f"run again after the reset (00:00 UTC)")
                scored.append((g, []))
                continue
            cap = {p: _fit(*bkt[p].left(), *avg[p]) for p in live}
            order = _interleave(_shares(cap, n), n) if n else []
            extra: dict[str, list[tuple[float, float]]] = {p: [] for p in members}
            usd = 0.0
            for k, p in enumerate(order):
                calls, tokens, cost = runs[p][k]
                extra[p].append((calls, tokens))
                usd += cost
            g.runs = {p: len(extra[p]) for p in members}
            g.capacity = {p: _num(cap[p]) if p in cap else 0.0 for p in members}
            g.days = max((bkt[p].days(extra[p]) for p in live if extra[p]), default=1)
            g.usd = round(usd, 6)
            g.spare = _num(sum(_fit(*bkt[p].left(extra[p]), *avg[p]) for p in live))
            if deadline_days is not None and g.days > deadline_days:
                g.excluded = f"needs about {g.days} days > --deadline-days {deadline_days}"
            scored.append((g, order))

        notes = _calibration_notes(model, candidates, records, pooled)
        eligible = sorted(((g, o) for g, o in scored if g.excluded is None), key=lambda go: _rank(go[0], strategy))
        if not eligible:
            why = "; ".join(f"{g.label}: {g.excluded}" for g, _ in scored if len(g.providers) == 1)
            decisions.append(AllocationDecision(canonical_model=model, strategy=strategy, rule="refused", chosen=None,
                                                candidates=[g for g, _ in scored], calibration=notes, refused=why))
            refusals.append(f"{model}: {why}")
            continue
        best, order = eligible[0]
        if len(best.providers) > 1:
            rule = f"equivalent pool: runs split by remaining capacity (strategy {strategy})"
        elif len(candidates) == 1:
            rule = "the only provider serving this model"
        elif pool_groups:
            rule = f"one provider for every cell: strategy {strategy} ranks it above the equivalent pool"
        else:
            rule = f"no equivalent calibration in force: one provider for every cell, best by {strategy}"
        for k, i in enumerate(idx):
            assigned[i] = order[k]
            bkt[order[k]].items.append(runs[order[k]][k][:2])
        decisions.append(AllocationDecision(
            canonical_model=model, strategy=strategy, rule=rule, chosen=best.label,
            providers={p: c for p, c in best.runs.items() if c}, usd=best.usd,
            cells={cells[i]: best.label for i in idx},
            candidates=[g for g, _ in scored], calibration=notes))

    specs = [s.model_copy(update={"provider": assigned[i]}) for i, s in enumerate(demand) if i in assigned]
    total = round(sum(d.usd for d in decisions), 6)
    if budget_usd is not None and total > budget_usd:
        refusals.append(f"projected ${total:.2f} at list prices > --budget-usd {budget_usd}"
                        + (" (try --strategy cost)" if strategy != "cost" else ""))
    return Allocation(strategy=strategy, recipe_ids=recipe_ids, models=list(models), seeds=seeds, task_ids=task_ids,
                      faults=faults or [None], candidates=providers, deadline_days=deadline_days,
                      budget_usd=budget_usd, specs=specs, decisions=decisions, refusals=refusals, usd=total)


# ------------------------------------------------------------------ persistence and the decision log

def allocations_dir() -> Path:
    return settings.VAR / "allocations"


def save_allocation(alloc: Allocation) -> Path:
    """Freeze a runnable allocation (content-addressed: the same plan saved twice is one file)."""
    if not alloc.ok:
        raise ValueError(f"allocation refused: {'; '.join(alloc.refusals)}")
    path = allocations_dir() / f"{alloc.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(alloc.model_dump_json(indent=1))
    return path


def load_allocation(allocation_id: str) -> Allocation:
    path = allocations_dir() / f"{allocation_id}.json"
    if not path.is_file():
        known = sorted(p.stem for p in allocations_dir().glob("*.json")) if allocations_dir().is_dir() else []
        raise KeyError(f"no allocation {allocation_id!r}; saved: {', '.join(known) or 'none'}")
    return Allocation.model_validate_json(path.read_text())


def log_allocation(alloc: Allocation) -> list[Path]:
    """Every decision, refusals included, with what it was for: one file per model in var/decisions/, laid out
    like the selector's (`select.policy.log_decision`): {"decision": ..., "context": ...}."""
    from harness_factory.select.policy import decisions_dir

    context = {"kind": "allocation", "allocation": alloc.id if alloc.ok else None, "strategy": alloc.strategy,
               "recipes": alloc.recipe_ids, "seeds": alloc.seeds, "faults": alloc.faults,
               "candidates": alloc.candidates, "deadline_days": alloc.deadline_days, "budget_usd": alloc.budget_usd,
               "usd": alloc.usd, "refusals": alloc.refusals}
    out = []
    for d in alloc.decisions:
        path = decisions_dir() / f"{d.made_at.strftime('%Y%m%dT%H%M%S')}-{d.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"decision": json.loads(d.model_dump_json()), "context": context}, indent=1))
        out.append(path)
    return out


def preflight(f, projection, deadline_days: int | None = None, budget_usd: float | None = None) -> list[str]:
    """What `hfx smoke` refuses before a campaign (MOD-05), for a frozen allocation about to run."""
    problems = []
    for p in projection:
        if f.ledger.exhausted(p.bucket):
            problems.append(f"{p.bucket}: today's quota is already spent (ledger: {settings.QUOTA_STATE.name}); "
                            f"run again with --resume after the reset (00:00 UTC)")
        elif deadline_days is not None and p.days > deadline_days:
            problems.append(f"{p.bucket}: needs about {p.days} days > --deadline-days {deadline_days}")
    total = sum(p.usd for p in projection)
    if budget_usd is not None and total > budget_usd:
        problems.append(f"projected ${total:.2f} at list prices > --budget-usd {budget_usd}")
    return problems
