"""Policies, decision logs and the held-out evaluation (SEL-05, SEL-06).

A Policy is the selector's decision for every task class, for one model on one provider, frozen and
content-addressed (var/policy/<id>.json), like a cost baseline: provisioning against a frozen policy keeps
choosing the same recipes while new results arrive. Every decision used to provision a run is logged with its
evidence (var/decisions/).

Held-out evaluation (SEL-06) plans one run per held-out task per strategy and compares mean E:
  selector     the per-class decision, from evidence that never includes the held-out tasks
  global       the single recipe with the best mean E over all evidence
  rules        the cold-start default for the class (rules only, no measurements)
  hand-picked  selector.yaml's hand_picked recipe
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from harness_factory import settings
from harness_factory.hashing import content_hash
from harness_factory.results.cost import CostBaseline
from harness_factory.results.store import current_task_rows
from harness_factory.select.selector import (
    Decision,
    SelectorConfig,
    _current_cells,
    current_task_hashes,
    decide,
    hard_rules,
    load_selector_config,
    task_classes,
)
from harness_factory.tasks.model import Split

STRATEGIES = ("selector", "global", "rules", "hand-picked")


def policy_dir() -> Path:
    return settings.VAR / "policy"


def decisions_dir() -> Path:
    return settings.VAR / "decisions"


class Policy(BaseModel):
    canonical_model: str
    provider: str
    baseline_id: str
    config_hash: str
    decisions: dict[str, Decision]          # task class -> decision
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash({"model": self.canonical_model, "provider": self.provider,
                             "decisions": {k: d.id for k, d in sorted(self.decisions.items())}}, 12)


def build_policy(factory, rows: list[dict], baseline: CostBaseline, canonical_model: str, provider: str,
                 config: SelectorConfig | None = None, note: str = "") -> Policy:
    config = config or load_selector_config()
    cells = _current_cells(factory, rows, baseline, canonical_model, provider)
    decisions = {c: decide(c, canonical_model, provider, factory=factory, rows=rows, baseline=baseline,
                           config=config, cells=cells) for c in task_classes(factory)}
    return Policy(canonical_model=canonical_model, provider=provider, baseline_id=baseline.id,
                  config_hash=config.config_hash(), decisions=decisions, note=note)


def save_policy(policy: Policy) -> Path:
    path = policy_dir() / f"{policy.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():                   # content-addressed: saving the same policy twice is a no-op
        path.write_text(policy.model_dump_json(indent=1))
    return path


def load_policy(policy_id: str) -> Policy:
    path = policy_dir() / f"{policy_id}.json"
    if not path.is_file():
        known = sorted(p.stem for p in policy_dir().glob("*.json")) if policy_dir().is_dir() else []
        raise KeyError(f"no policy {policy_id!r}; saved: {', '.join(known) or 'none'}")
    return Policy.model_validate_json(path.read_text())


def log_decision(decision: Decision, **context) -> Path:
    """SEL-05: the decision, the rule and every number it used, plus what it was used for."""
    stamp = decision.made_at.strftime("%Y%m%dT%H%M%S")
    path = decisions_dir() / f"{stamp}-{decision.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"decision": json.loads(decision.model_dump_json()), "context": context}, indent=1))
    return path


# ------------------------------------------------------------------ SEL-06: held-out evaluation

class EvalPlan(BaseModel):
    canonical_model: str
    provider: str
    split: str
    baseline_id: str
    picks: dict[str, dict[str, str]]        # strategy -> task id -> recipe id
    notes: list[str] = Field(default_factory=list)
    decisions: dict[str, Decision] = Field(default_factory=dict)   # task id -> selector decision

    def runs(self) -> list[tuple[str, str]]:
        """Unique (task, recipe) pairs: strategies that pick the same recipe share one run."""
        return sorted({(t, r) for picks in self.picks.values() for t, r in picks.items()})


def evidence_rows(rows: list[dict], exclude_task_ids: set[str]) -> list[dict]:
    """Rows that may inform the choice: never a run of a task being evaluated."""
    return [r for r in rows if r["task_version"].split("@")[0] not in exclude_task_ids]


def plan_evaluation(factory, rows: list[dict], canonical_model: str, provider: str, *,
                    split: Split = Split.HELD_OUT, config: SelectorConfig | None = None) -> EvalPlan:
    from harness_factory.provision.provisioner import task_class

    config = config or load_selector_config()
    targets = factory.tasks.by_split(split)
    if not targets:
        raise ValueError(f"no {split.value} tasks to evaluate on")
    evidence = current_task_rows(evidence_rows(rows, {t.id for t in targets}), current_task_hashes(factory))
    baseline = CostBaseline.from_rows(evidence, note=f"evidence for {split.value} evaluation")
    cells = _current_cells(factory, evidence, baseline, canonical_model, provider)

    # Best global recipe: highest mean E over all its current evidence, pooled across classes. A cell whose runs
    # are all INCOMPLETE / ERROR has n = 0: no evidence, so it takes no part (and can't divide by zero).
    pooled: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for (_, rid), c in cells.items():
        if c.e.n:
            pooled[rid].append((c.e.mean, c.e.n))
    global_best = max(pooled, key=lambda r: (sum(m * n for m, n in pooled[r]) / sum(n for _, n in pooled[r]), r),
                      default=config.hand_picked)

    picks: dict[str, dict[str, str]] = {s: {} for s in STRATEGIES}
    notes, decisions = [], {}
    for t in targets:
        cls = task_class(t, factory.declared)
        d = decide(cls, canonical_model, provider, factory=factory, rows=evidence, baseline=baseline, config=config,
                   task=t, cells=cells)
        decisions[t.id] = d
        picks["selector"][t.id] = d.chosen[0]          # a close call's runner-up is not part of the comparison
        picks["rules"][t.id] = config.default_for(cls)
        for strategy, rid in (("global", global_best), ("hand-picked", config.hand_picked)):
            if hard_rules(factory.recipes[rid], cls, canonical_model, provider, factory, config, t):
                notes.append(f"{strategy}: {rid} fails a hard rule for {t.id} ({cls}); used the class default")
                rid = config.default_for(cls)
            picks[strategy][t.id] = rid
    return EvalPlan(canonical_model=canonical_model, provider=provider, split=split.value, baseline_id=baseline.id,
                    picks=picks, notes=notes, decisions=decisions)


class StrategyScore(BaseModel):
    strategy: str
    tasks: int
    scored: int
    mean_e: float | None
    passed: int


def score_evaluation(plan: EvalPlan, factory, rows: list[dict], baseline: CostBaseline,
                     run_keys: dict[tuple[str, str], str]) -> list[StrategyScore]:
    """Mean cost-adjusted E per strategy over the held-out tasks, from the newest result of each planned run.
    Every strategy is scored against the same baseline. A run without a scored result counts as missing."""
    from harness_factory.results.store import UNSCORED

    by_key = {r["run_key"]: r for r in rows}
    out = []
    for strategy, picks in plan.picks.items():
        es, passed = [], 0
        for task_id, rid in picks.items():
            row = by_key.get(run_keys[(task_id, rid)])
            if row is None or row["status"] in UNSCORED:
                continue
            es.append(baseline.adjusted(row))
            passed += row["status"] == "SUCCESS"
        out.append(StrategyScore(strategy=strategy, tasks=len(picks), scored=len(es),
                                 mean_e=round(sum(es) / len(es), 6) if es else None, passed=passed))
    return out
