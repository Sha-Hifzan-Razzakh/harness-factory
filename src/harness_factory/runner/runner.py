"""Runner (B7): executes runs, grades them, writes traces and results.

run_one        one task x recipe x model x provider x seed -> EffectivenessRecord
run_reference  replays a task's reference plan directly against the MCP servers (no model) and grades it
smoke          every task x every model on one recipe: the P0 gate
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

import harness_factory.shapes.builtin  # noqa: F401
from harness_factory import settings
from harness_factory.env.faults import NO_FAULTS, FaultConfig, load_fault_profiles
from harness_factory.env.mcp.servers import ToolCatalogFile, load_tool_catalog
from harness_factory.env.sandbox import make_sandbox
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
from harness_factory.results.store import UNSCORED, ResultsStore
from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.base import ShapeRegistry
from harness_factory.shapes.result import RunResult, RunStatus, Violation
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
    faults: dict[str, FaultConfig] = field(default_factory=dict)
    tracker: ExperimentTracker = field(default_factory=ExperimentTracker)
    graders: GradingPipeline = field(default_factory=default_pipeline)

    @staticmethod
    def load(store: bool = True, results_url: str | None = None, quota_state: Path | None = None) -> Factory:
        """quota_state: where provider usage is persisted (default settings.QUOTA_STATE). Tests pass a temp path."""
        providers = load_provider_catalog(settings.PROVIDERS_FILE)
        return Factory(tasks=TaskRegistry(), recipes=load_recipes(settings.RECIPES_DIR),
                       declared=load_tool_catalog(settings.TOOLS_FILE), providers=providers,
                       ledger=QuotaLedger(providers, state_path=quota_state or settings.QUOTA_STATE),
                       store=ResultsStore(results_url) if store else None,
                       faults=load_fault_profiles(settings.FAULTS_DIR))

    def fault(self, fault_id: str | None) -> FaultConfig | None:
        if fault_id in (None, NO_FAULTS):
            return None
        if fault_id not in self.faults:
            raise KeyError(f"unknown fault profile {fault_id!r}; known: {sorted(self.faults)}")
        return self.faults[fault_id]


def _cost(spans, providers: ProviderCatalog, provider_id: str, wall_s: float) -> CostLedger:
    led = CostLedger()
    for s in spans:
        led.add(s, float(s.attributes.get("usd", 0.0)))
    led.wall_s = round(wall_s, 3)
    return led


def write_transcript(trace_path: Path, inst, result: RunResult) -> Path:
    """Everything a person needs to debug a run, next to its trace: the full conversation (prompts, stage
    instructions, model replies, tool calls with arguments, tool results), the final answer and how it ended."""
    path = trace_path.with_suffix(".transcript.json")
    path.write_text(json.dumps({
        "run_key": inst.run_key.as_str(), "status": result.status.value, "error": result.error,
        "output": result.output, "snapshot": inst.snapshot, "messages": result.messages,
    }, indent=1, default=str))
    return path


# A run that did not stop by itself (budget, V3 stage cap, context overflow, malformed output) is graded on the
# environment, but cannot be a clean success: its outcome is scaled by this factor and its status is at most
# PARTIAL. Without it, a harness that solves early and then keeps calling until a cap scores like a clean run
# (Q only catches exact repeats) and, as a "success", would inflate the cost median for everyone.
OVERRUN_OUTCOME_FACTOR = 0.5
OVERRUN_KINDS = frozenset({"budget_exceeded", "stage_cap", "context_overflow", "malformed_output"})


def overran(result: RunResult) -> bool:
    return any(v.kind in OVERRUN_KINDS for v in result.violations)


def _finalize_status(result: RunResult, grades: list[Grade], threshold: float) -> RunStatus:
    """Every scored run gets its status from the environment. Unscored runs keep INCOMPLETE / ERROR."""
    if not result.scored:
        return result.status
    state = next((g for g in grades if g.grader_id == "state_diff"), None)
    critical = any(v.critical for g in grades for v in g.violations) or any(v.critical for v in result.violations)
    if state is None or critical:
        return RunStatus.FAIL
    if state.score >= threshold and not overran(result):
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL if state.score > 0 else RunStatus.FAIL


async def run_one(f: Factory, task: Task, recipe: HarnessRecipe, canonical_model: str, provider_id: str,
                  seed: int = 0, backend: Backend | None = None, flush: bool = True,
                  fault: str | None = None) -> EffectivenessRecord:
    """flush: push buffered OTLP spans after the run. Campaigns pass False and flush once at the end.
    fault: a fault profile id (catalog/faults) to inject tool failures; None = a clean run."""
    t0 = time.monotonic()
    async with provision(task, recipe, canonical_model, provider_id, declared=f.declared, providers=f.providers,
                         ledger=f.ledger, seed=seed, backend=backend, fault=f.fault(fault)) as p:
        inst = p.instance
        shape = ShapeRegistry.get(recipe.variant)
        k = inst.run_key
        with inst.tracer.span(SpanKind.run, k.short(), task=k.task_version, recipe=recipe.id, recipe_hash=k.recipe_hash,
                              model=k.canonical_model, provider=k.provider, seed=k.seed,
                              variant=recipe.variant.value, task_class=p.task_class,
                              prompt_hash=inst.snapshot["prompt_hash"]) as root:
            inst.budget.start()
            try:
                result = await shape.run(inst)
            except HarnessError as e:
                result = RunResult(run_key=inst.run_key, status=RunStatus.ERROR,
                                   error=f"{e.kind.value}: {e.message}", violations=list(inst.tools.violations))
            except GraphRecursionError as e:  # the shape ran past its own step bound: the harness's doing
                result = RunResult(run_key=inst.run_key, status=RunStatus.BUDGET_EXCEEDED, scored=True,
                                   error=f"recursion limit: {str(e)[:300]}",
                                   violations=[*inst.tools.violations,
                                               Violation(kind="budget_exceeded", detail="graph recursion limit")])
            except Exception as e:  # noqa: BLE001  (a crash still gets graded, traced and recorded)
                result = RunResult(run_key=inst.run_key, status=RunStatus.ERROR,
                                   error=f"crash: {type(e).__name__}: {str(e)[:500]}",
                                   violations=list(inst.tools.violations))
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
        transcript_path = write_transcript(trace_path, inst, result)

        by_id = {g.grader_id: g for g in grades}
        violations = list(result.violations) + [v for g in grades for v in g.violations]
        scores = FamilyScores(
            outcome=(by_id["state_diff"].score * (OVERRUN_OUTCOME_FACTOR if overran(result) else 1.0)
                     if "state_diff" in by_id and result.scored else 0.0),
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
            output=result.output if isinstance(result.output, str) else None,
            messages=result.messages,
            transcript_path=str(transcript_path),
            fault=fault,
        )
    if f.store:
        f.store.add(rec)
    f.tracker.log(rec, recipe)
    if flush:
        await asyncio.to_thread(Tracer.flush)
    return rec


async def run_reference(f: Factory, task: Task) -> Grade:
    """TSK-04: the reference plan must pass the task's own oracle."""
    ref = load_reference(task)
    servers = sorted({step["tool"].split(".")[0] for step in ref.plan}) or ["fs"]
    with make_sandbox(label=f"ref-{task.id}") as sb:
        sb.reset(task)
        insp = StateInspector(sb.workspace, sb.db_path)
        before = insp.capture()
        tracer = Tracer(f"reference|{task.key()}")
        async with MCPSessionPool(f.declared, servers, sb.env(), sb.launch) as pool:
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


class RunSpec(BaseModel):
    """One planned run of a campaign: task x recipe x model x provider x seed x fault profile."""
    task_id: str
    recipe_id: str
    canonical_model: str
    provider: str
    seed: int = 0
    fault: str | None = None                   # fault profile id; None = clean


class CampaignError(BaseModel):
    spec: RunSpec
    error: str


class CampaignResult(BaseModel):
    records: list[EffectivenessRecord]          # in plan order (runs made by this call)
    errors: list[CampaignError]
    concurrency: int
    wall_s: float
    quota_wait_s: dict[str, float]
    skipped: list[RunSpec] = []                 # resume: already have a scored result in the store
    reruns: int = 0                             # resume: had only an INCOMPLETE / ERROR result, ran again


class PlanStatus(BaseModel):
    """What a campaign plan looks like against the store: done (scored), retry (only INCOMPLETE/ERROR), new."""
    done: list[RunSpec]
    retry: list[RunSpec]
    new: list[RunSpec]


def plan_status(f: Factory, specs: list[RunSpec]) -> PlanStatus:
    """Classify planned runs by their predicted RunKey (same function the provisioner uses)."""
    from harness_factory.provision.provisioner import run_key_for

    rows = f.store.rows() if f.store else []
    scored = {r["run_key"] for r in rows if r["status"] not in UNSCORED}
    seen = {r["run_key"] for r in rows}
    done, retry, new = [], [], []
    for s in specs:
        key = run_key_for(f.tasks.get(s.task_id), f.recipes[s.recipe_id], s.canonical_model, s.provider, s.seed,
                          declared=f.declared, providers=f.providers, fault=f.fault(s.fault)).as_str()
        (done if key in scored else retry if key in seen else new).append(s)
    return PlanStatus(done=done, retry=retry, new=new)


def plan(f: Factory, recipe_ids: list[str] | str, provider_id: str, models: list[str] | None = None,
         seeds: int = 1, task_ids: list[str] | None = None,
         faults: list[str | None] | None = None) -> list[RunSpec]:
    """Every task x recipe x model x fault profile x seed, in a fixed order (results come back in this order).
    faults: fault profile ids to add next to the clean runs, e.g. [None, "transient"] (R needs both)."""
    recipe_ids = [recipe_ids] if isinstance(recipe_ids, str) else recipe_ids
    models = models or [m.canonical for m in f.providers.get(provider_id).models]
    tasks = [f.tasks.get(t) for t in task_ids] if task_ids else f.tasks.all()
    faults = faults or [None]
    for fid in faults:
        f.fault(fid)                                         # unknown profile: fail before anything runs
    return [RunSpec(task_id=t.id, recipe_id=r, canonical_model=m, provider=provider_id, seed=s,
                    fault=None if fid in (None, NO_FAULTS) else fid)
            for t in tasks for r in recipe_ids for m in models for fid in faults for s in range(seeds)]


async def run_campaign(f: Factory, specs: list[RunSpec], concurrency: int = 4,
                       resume: bool = False) -> CampaignResult:
    """Run specs with up to `concurrency` runs in flight. Workers share the Factory's QuotaLedger, so model
    calls are paced to each provider's limits however many workers there are. One failing run never stops
    the others; its error is reported with its spec.

    resume: skip specs whose RunKey already has a scored result; rerun those with only INCOMPLETE / ERROR.
    A rerun's row supersedes the old one (the store reads the newest row per RunKey)."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    skipped: list[RunSpec] = []
    reruns = 0
    if resume:
        status = plan_status(f, specs)
        skipped, reruns = status.done, len(status.retry)
        todo = {id(s) for s in status.retry + status.new}
        specs = [s for s in specs if id(s) in todo]
    gate = asyncio.Semaphore(concurrency)
    t0 = time.monotonic()
    waits_before = dict(f.ledger.waits)

    async def one(spec: RunSpec) -> EffectivenessRecord | CampaignError:
        async with gate:
            try:
                return await run_one(f, f.tasks.get(spec.task_id), f.recipes[spec.recipe_id], spec.canonical_model,
                                     spec.provider, seed=spec.seed, flush=False, fault=spec.fault)
            except Exception as e:  # noqa: BLE001  (reported per run with its spec; the campaign carries on)
                return CampaignError(spec=spec, error=f"{type(e).__name__}: {str(e)[:500]}")

    tasks = [asyncio.ensure_future(one(s)) for s in specs]
    try:
        outcomes = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        # Ctrl-C: gather has already cancelled every run, but returns before they finish tearing down (MCP
        # servers, sandboxes, containers). Wait for that cleanup, shielded and without cancelling anything again,
        # before leaving; otherwise asyncio.run cancels them a second time mid-teardown and leaks processes and
        # directories. Finished runs are already stored.
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        raise
    finally:
        await asyncio.to_thread(Tracer.flush)          # once per campaign, including an interrupted one
    return CampaignResult(
        records=[o for o in outcomes if isinstance(o, EffectivenessRecord)],
        errors=[o for o in outcomes if isinstance(o, CampaignError)],
        concurrency=concurrency, wall_s=round(time.monotonic() - t0, 3),
        quota_wait_s={p: round(w - waits_before.get(p, 0.0), 3) for p, w in f.ledger.waits.items()
                      if w > waits_before.get(p, 0.0)},
        skipped=skipped, reruns=reruns)


async def smoke(f: Factory, recipe_ids: list[str] | str, provider_id: str, models: list[str] | None = None,
                seeds: int = 1, concurrency: int = 1) -> list[EffectivenessRecord]:
    """Every task x recipe x model x seed. Several recipes make this a like-for-like shape comparison.
    Raises on the first failed run (a gate must not pass with runs missing)."""
    result = await run_campaign(f, plan(f, recipe_ids, provider_id, models, seeds), concurrency)
    if result.errors:
        e = result.errors[0]
        raise RuntimeError(f"{len(result.errors)} run(s) failed; first: {e.spec.model_dump()} -> {e.error}")
    return result.records
