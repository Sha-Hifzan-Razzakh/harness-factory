"""Selector (SEL-01..05): which recipe to use for a task class, model and provider.

    decide(class) = prune with hard rules (SEL-01)
                  -> rank the survivors by measured, cost-adjusted effectiveness E with a 95% interval (SEL-02)
                  -> nothing measured well enough: the class's rule-based default (SEL-03)
                  -> the top two overlap within their intervals: both are chosen, to be run (SEL-04)
                  -> the Decision records the rule and every number it used (SEL-05)

Evidence is only what still describes the recipe as it is: a cell counts only if its recipe hash and
environment hash are the ones the recipe has now. Results from an older version of a recipe, or from before a
tools.yaml or providers.yaml change, never decide for the current one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from harness_factory import settings
from harness_factory.env.mcp.servers import RISK_TO_TIER, TIER_ORDER
from harness_factory.hashing import content_hash
from harness_factory.recipes.model import Variant
from harness_factory.results.cost import CostBaseline
from harness_factory.results.store import aggregate_rows, current_task_rows

SELECTOR_FILE = settings.CATALOG / "selector.yaml"


class DefaultRule(BaseModel):
    class_: str = Field(alias="class")
    recipe: str


class SelectorConfig(BaseModel):
    min_runs: int = Field(3, ge=1)
    candidates: list[str] | None = None
    hand_picked: str = "v2-base"
    defaults: list[DefaultRule]
    depth_shapes: dict[str, list[Variant]]

    def default_for(self, task_class: str, exclude: set[str] = frozenset()) -> str:
        """The first matching default not in `exclude` (recipes a hard rule pruned)."""
        for rule in self.defaults:
            if fnmatch(task_class, rule.class_) and rule.recipe not in exclude:
                return rule.recipe
        raise ValueError(f"no default recipe matches class {task_class!r} (add a '*' rule)")

    def config_hash(self) -> str:
        return content_hash(self.model_dump(mode="json", by_alias=True), 12)


def load_selector_config(path: Path | None = None) -> SelectorConfig:
    return SelectorConfig.model_validate(yaml.safe_load((path or SELECTOR_FILE).read_text()))


class Candidate(BaseModel):
    recipe_id: str
    n: int
    mean_e: float
    ci95: tuple[float, float]
    pass_rate: float
    mean_c: float
    robustness: float | None = None


class Pruned(BaseModel):
    recipe_id: str
    rule: str
    detail: str


class Decision(BaseModel):
    """One selection, with everything it was based on (SEL-05)."""
    task_class: str
    canonical_model: str
    provider: str
    rule: Literal["ranked", "close_call", "cold_start"]
    chosen: list[str]                       # one recipe, or the top two on a close call
    ranked: list[Candidate] = Field(default_factory=list)
    insufficient: list[Candidate] = Field(default_factory=list)   # measured, but fewer than min_runs
    pruned: list[Pruned] = Field(default_factory=list)
    baseline_id: str
    config_hash: str
    evidence_runs: int                      # scored clean runs behind the ranked and insufficient cells
    made_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return content_hash(self.model_dump(mode="json", exclude={"made_at"}), 12)

    def summary(self) -> str:
        if self.rule == "cold_start":
            measured = f"; {len(self.insufficient)} measured below min_runs" if self.insufficient else ""
            return f"{self.task_class}: {self.chosen[0]} (cold start: rule-based default{measured})"
        top = self.ranked[0]
        head = f"{self.task_class}: {' + '.join(self.chosen)}"
        if self.rule == "close_call":
            return f"{head} (close call, run both: E {top.mean_e:.3f} vs {self.ranked[1].mean_e:.3f}, intervals overlap)"
        return f"{head} (E {top.mean_e:.3f}, 95% CI {top.ci95[0]:.3f}-{top.ci95[1]:.3f}, n={top.n})"


def hard_rules(recipe, task_class: str, canonical_model: str, provider: str, factory, config: SelectorConfig,
               task=None) -> Pruned | None:
    """SEL-01: tier covers the class's risk, the shape fits the step depth, the model can do what the shape
    needs, the recipe is valid here, and (given a task) it reaches every tool the task allows."""
    from harness_factory.provision.provisioner import allowed_tools
    from harness_factory.recipes.validate import validate_recipe

    depth, _, risk = task_class.split("|")
    need = RISK_TO_TIER[risk]
    if TIER_ORDER.index(recipe.tier) < TIER_ORDER.index(need):
        return Pruned(recipe_id=recipe.id, rule="tier_covers_risk", detail=f"tier {recipe.tier} < {need} for {risk}")
    if recipe.variant not in config.depth_shapes.get(depth, []):
        return Pruned(recipe_id=recipe.id, rule="shape_fits_depth", detail=f"{recipe.variant.value} for {depth}")
    issues = validate_recipe(recipe.with_model(canonical_model), factory.declared, providers=factory.providers,
                             provider_id=provider)
    if issues:
        return Pruned(recipe_id=recipe.id, rule=issues[0].rule, detail=issues[0].detail)
    if task is not None:                     # the task's own allowance; granted extras (search, skills) are optional
        direct = {r for r in factory.declared.all_refs() if any(fnmatch(r, p) for p in task.allowed_tools)}
        missing = sorted(direct - allowed_tools(recipe, task, factory.declared))
        if missing:
            return Pruned(recipe_id=recipe.id, rule="reaches_task_tools", detail=f"cannot call {', '.join(missing)}")
    return None


def current_task_hashes(factory) -> dict[str, str]:
    """task_version -> the content hash of that task as the catalog defines it now (held-out tasks included)."""
    return {t.key(): t.content_hash() for t in factory.tasks.all(include_held_out=True)}


def _current_cells(factory, rows: list[dict], baseline: CostBaseline, canonical_model: str, provider: str):
    """Cells keyed by (task_class, recipe_id), only where recipe, environment and task hashes are current: rows of
    an edited task definition (same id and version, different content) are a different experiment."""
    served = factory.providers.served(provider, canonical_model)
    current = {}
    for rid, r in factory.recipes.items():
        current[rid] = (r.content_hash(), factory.declared.env_hash(r.tools.mcp_servers, served.provider_model_id))
    rows = current_task_rows(rows, current_task_hashes(factory))
    out = {}
    for c in aggregate_rows([r for r in rows if r["canonical_model"] == canonical_model
                             and r["provider"] == provider], baseline):
        if c.recipe_id in current and (c.recipe_hash, c.env_hash) == current[c.recipe_id]:
            out[(c.task_class, c.recipe_id)] = c
    return out


def decide(task_class: str, canonical_model: str, provider: str, *, factory, rows: list[dict],
           baseline: CostBaseline, config: SelectorConfig | None = None, task=None, cells=None) -> Decision:
    config = config or load_selector_config()
    cells = cells if cells is not None else _current_cells(factory, rows, baseline, canonical_model, provider)
    ids = config.candidates or sorted(factory.recipes)
    pruned, ranked, insufficient = [], [], []
    for rid in ids:
        recipe = factory.recipes.get(rid)
        if recipe is None:
            pruned.append(Pruned(recipe_id=rid, rule="recipe_exists", detail="not in the catalog"))
            continue
        why = hard_rules(recipe, task_class, canonical_model, provider, factory, config, task)
        if why:
            pruned.append(why)
            continue
        cell = cells.get((task_class, rid))
        if cell is None or cell.e.n == 0:
            continue
        cand = Candidate(recipe_id=rid, n=cell.e.n, mean_e=round(cell.e.mean, 6),
                         ci95=(round(cell.e.ci95[0], 6), round(cell.e.ci95[1], 6)), pass_rate=cell.e.pass_rate,
                         mean_c=round(cell.mean_c, 6), robustness=cell.robustness)
        (ranked if cell.e.n >= config.min_runs else insufficient).append(cand)
    ranked.sort(key=lambda c: (-c.mean_e, -c.pass_rate, c.mean_c, c.recipe_id))
    evidence = sum(c.n for c in ranked + insufficient)
    common = dict(task_class=task_class, canonical_model=canonical_model, provider=provider, ranked=ranked,
                  insufficient=insufficient, pruned=pruned, baseline_id=baseline.id, config_hash=config.config_hash(),
                  evidence_runs=evidence)
    if not ranked:
        # The first default that survives the hard rules; failing that, the first surviving candidate (e.g. a
        # model without tool calling leaves only V1).
        out = {p.recipe_id for p in pruned}
        try:
            default = config.default_for(task_class, exclude=out)
        except ValueError:
            alive = [r for r in ids if r not in out and r in factory.recipes]
            if not alive:
                raise ValueError(f"every recipe fails a hard rule for {task_class} on {canonical_model}") from None
            default = alive[0]
        return Decision(rule="cold_start", chosen=[default], **common)
    if len(ranked) > 1 and ranked[0].ci95[0] <= ranked[1].ci95[1]:
        return Decision(rule="close_call", chosen=[ranked[0].recipe_id, ranked[1].recipe_id], **common)
    return Decision(rule="ranked", chosen=[ranked[0].recipe_id], **common)


def task_classes(factory, include_held_out: bool = True) -> list[str]:
    from harness_factory.provision.provisioner import task_class
    return sorted({task_class(t, factory.declared) for t in factory.tasks.all(include_held_out=include_held_out)})
