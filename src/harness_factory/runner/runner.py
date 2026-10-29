"""Runner (B7): executes runs, grades them, writes traces and results.

run_one        one task x recipe x model x provider x seed -> EffectivenessRecord
run_reference  replays a task's reference plan directly against the MCP servers (no model) and grades it
smoke          every task x every model on one recipe: the P0 gate
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import harness_factory.shapes.builtin  # noqa: F401
from harness_factory import settings
from harness_factory.env.mcp.servers import ToolCatalogFile, load_tool_catalog
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.env.state import StateInspector
from harness_factory.grading.base import Grade, GradeContext, GradingPipeline, default_pipeline
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.mlops.tracker import ExperimentTracker
from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog, load_provider_catalog
from harness_factory.providers.quota import QuotaLedger
from harness_factory.provision.provisioner import provision
from harness_factory.recipes.model import HarnessRecipe, load_recipes
from harness_factory.results.model import EffectivenessRecord, FamilyScores, ScoreWeights
from harness_factory.results.store import ResultsStore
from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.base import ShapeRegistry
from harness_factory.shapes.result import RunResult, RunStatus
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.gateway import Backend
from harness_factory.tasks.model import Task
from harness_factory.tasks.reference import load_reference
from harness_factory.tasks.registry import TaskRegistry


@dataclass
class Factory:
    """Everything a run reads from the catalog, loaded once."""
    tasks: TaskRegistry
    recipes: dict[str, HarnessRecipe]
    declared: ToolCatalogFile
    providers: ProviderCatalog
    ledger: QuotaLedger
    store: ResultsStore | None = None
    tracker: ExperimentTracker = field(default_factory=ExperimentTracker)
    graders: GradingPipeline = field(default_factory=default_pipeline)

    @staticmethod
    def load(store: bool = True, results_url: str | None = None) -> Factory:
        providers = load_provider_catalog(settings.PROVIDERS_FILE)
        return Factory(tasks=TaskRegistry(), recipes=load_recipes(settings.RECIPES_DIR),
                       declared=load_tool_catalog(settings.TOOLS_FILE), providers=providers,
                       ledger=QuotaLedger(providers), store=ResultsStore(results_url) if store else None)


def _cost(spans, providers: ProviderCatalog, provider_id: str, wall_s: float) -> CostLedger:
    led = CostLedger()
    for s in spans:
        led.add(s, float(s.attributes.get("usd", 0.0)))
    led.wall_s = round(wall_s, 3)
    return led


def _finalize_status(result: RunResult, grades: list[Grade], threshold: float) -> RunStatus:
    if not result.finished:
        return result.status
    state = next((g for g in grades if g.grader_id == "state_diff"), None)
    critical = any(v.critical for g in grades for v in g.violations) or any(v.critical for v in result.violations)
    if state is None or critical:
        return RunStatus.FAIL
    if state.score >= threshold:
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL if state.score > 0 else RunStatus.FAIL


async def run_one(f: Factory, task: Task, recipe: HarnessRecipe, canonical_model: str, provider_id: str,
                  seed: int = 0, backend: Backend | None = None) -> EffectivenessRecord:
    t0 = time.monotonic()
    async with provision(task, recipe, canonical_model, provider_id, declared=f.declared, providers=f.providers,
                         ledger=f.ledger, seed=seed, backend=backend) as p:
        inst = p.instance
        shape = ShapeRegistry.get(recipe.variant)
        k = inst.run_key
        with inst.tracer.span(SpanKind.run, k.short(), task=k.task_version, recipe=recipe.id, recipe_hash=k.recipe_hash,
                              model=k.canonical_model, provider=k.provider, seed=k.seed,
                              variant=recipe.variant.value, task_class=p.task_class,
                              prompt_hash=inst.snapshot["prompt_hash"]) as root:
            try:
                result = await shape.run(inst)
            except HarnessError as e:
                result = RunResult(run_key=inst.run_key, status=RunStatus.ERROR,
                                   error=f"{e.kind.value}: {e.message}", violations=list(inst.tools.violations))
            after = p.inspector.capture()
            diff = StateInspector.diff(p.before, after)
            with inst.tracer.span(SpanKind.grade, "pipeline"):
                grades = f.graders.run(GradeContext(task=task, result=result, before=p.before, after=after, diff=diff,
                                                    workspace=p.sandbox.workspace, db_path=p.sandbox.db_path,
                                                    spans=list(inst.tracer.spans),
                                                    max_steps=recipe.config.max_steps))
            result.status = _finalize_status(result, grades, task.oracle.pass_threshold)
            root.attributes["status"] = result.status.value
        result.trajectory = list(inst.tracer.spans)
        result.cost = _cost(inst.tracer.spans, f.providers, provider_id, time.monotonic() - t0)
        trace_path = inst.tracer.write_jsonl()

        by_id = {g.grader_id: g for g in grades}
        violations = list(result.violations) + [v for g in grades for v in g.violations]
        scores = FamilyScores(
            outcome=by_id["state_diff"].score if "state_diff" in by_id and result.finished else 0.0,
            trajectory=by_id["trajectory"].score if "trajectory" in by_id else 1.0,
            efficiency=0.0,   # normalized against the class median at aggregation time (P2)
            safety=len(violations),
        )
        weights = ScoreWeights.for_profile(p.weight_profile)
        critical = any(v.critical for v in violations)
        rec = EffectivenessRecord(
            run_key=inst.run_key, task_class=p.task_class, recipe_id=recipe.id, status=result.status,
            scores=scores, composite=round(weights.composite(scores, critical), 6), cost=result.cost,
            violations=violations,
            grades=[{**g.model_dump(), "snapshot": inst.snapshot} if g.grader_id == "state_diff" else g.model_dump()
                    for g in grades] + ([{"grader_id": "run_error", "detail": result.error}] if result.error else []),
            trace_path=str(trace_path),
        )
    if f.store:
        f.store.add(rec)
    f.tracker.log(rec, recipe)
    return rec


async def run_reference(f: Factory, task: Task) -> Grade:
    """TSK-04: the reference plan must pass the task's own oracle."""
    ref = load_reference(task)
    servers = sorted({step["tool"].split(".")[0] for step in ref.plan}) or ["fs"]
    with LocalSandbox(label=f"ref-{task.id}") as sb:
        sb.reset(task)
        insp = StateInspector(sb.workspace, sb.db_path)
        before = insp.capture()
        tracer = Tracer(f"reference|{task.key()}")
        async with MCPSessionPool(f.declared, servers, sb.env()) as pool:
            for step in ref.plan:
                with tracer.span(SpanKind.tool_call, step["tool"]) as span:
                    res = await pool.call(step["tool"], step["args"])
                    if res.is_error:
                        span.status = "error"
                        raise RuntimeError(f"{task.key()} reference step {step['tool']} failed: {res.text}")
        after = insp.capture()
        dummy = RunResult(run_key=RunKey(task_version=task.key(), task_hash=task.content_hash(), recipe_hash="ref",
                                         canonical_model="reference", provider="none"),
                          output=ref.final, finished=True)
        ctx = GradeContext(task=task, result=dummy, before=before, after=after, diff=StateInspector.diff(before, after),
                           workspace=sb.workspace, db_path=sb.db_path, spans=tracer.spans, max_steps=10_000)
        return f.graders.graders["state_diff"].grade(ctx)


async def smoke(f: Factory, recipe_ids: list[str] | str, provider_id: str, models: list[str] | None = None,
                seeds: int = 1) -> list[EffectivenessRecord]:
    """Every task x recipe x model x seed. Several recipes make this a like-for-like shape comparison."""
    recipe_ids = [recipe_ids] if isinstance(recipe_ids, str) else recipe_ids
    models = models or [m.canonical for m in f.providers.get(provider_id).models]
    out = []
    for task in f.tasks.all():
        for rid in recipe_ids:
            for m in models:
                for seed in range(seeds):
                    out.append(await run_one(f, task, f.recipes[rid], m, provider_id, seed=seed))
    return out
