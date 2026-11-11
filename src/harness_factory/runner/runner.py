"""Runner (B7): executes runs, grades them, writes traces and results.

run_one        one task x recipe x model x provider x seed -> EffectivenessRecord
run_reference  replays a task's reference plan directly against the MCP servers (no model) and grades it
smoke          every task x every model on one recipe: the P0 gate
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from harness_factory.grading.base import (
    Grade,
    GradeContext,
    GradingPipeline,
    combine_families,
    default_pipeline,
)
from harness_factory.grading.judge import RubricJudge, apply_judge_failure, judge_usage
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.mlops.tracker import ExperimentTracker
from harness_factory.observability import openinference
from harness_factory.observability.span import Span, SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog, load_provider_catalog
from harness_factory.providers.quota import QuotaLedger
from harness_factory.provision.provisioner import provision
from harness_factory.recipes.model import HarnessRecipe, load_recipes
from harness_factory.results.model import EffectivenessRecord, FamilyScores, ScoreWeights
from harness_factory.results.store import UNSCORED, ResultsStore
from harness_factory.retrieval import tool as retrieval
from harness_factory.runner.endstate import save_endstate
from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey
from harness_factory.runners.base import harness_for
from harness_factory.shapes.base import Harness, HarnessInstance
from harness_factory.shapes.checkpoint import Checkpoint, CheckpointConfig, PauseRule, RunPaused, open_saver
from harness_factory.shapes.result import RunResult, RunStatus, Violation
from harness_factory.substrate.errors import HarnessError
from harness_factory.substrate.gateway import Backend
from harness_factory.substrate.tools import ARGS_CHARS
from harness_factory.tasks import perturb
from harness_factory.tasks.model import Task
from harness_factory.tasks.reference import load_reference
from harness_factory.tasks.registry import TaskRegistry

log = logging.getLogger(__name__)


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
    judge: RubricJudge | None = None            # rubric checks (ADR 0014); None = ungraded, weight 0
    checkpoint: CheckpointConfig | None = None    # LangGraph checkpointing of V2-V5 runs (opt-in); None = off

    @staticmethod
    def load(store: bool = True, results_url: str | None = None, quota_state: Path | None = None) -> Factory:
        """quota_state: where provider usage is persisted (default settings.QUOTA_STATE). Tests pass a temp path."""
        providers = load_provider_catalog(settings.PROVIDERS_FILE)
        ledger = QuotaLedger(providers, state_path=quota_state or settings.QUOTA_STATE)
        results = ResultsStore(results_url) if store else None
        backend = settings.checkpoint_backend()
        return Factory(tasks=TaskRegistry(), recipes=load_recipes(settings.RECIPES_DIR),
                       declared=load_tool_catalog(settings.TOOLS_FILE), providers=providers, ledger=ledger,
                       store=results, faults=load_fault_profiles(settings.FAULTS_DIR),
                       judge=RubricJudge.from_catalog(providers, ledger,
                                                      results.calibrations if results is not None else None),
                       checkpoint=CheckpointConfig(backend, settings.checkpoint_url(backend)) if backend else None)

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
OVERRUN_KINDS = frozenset({"budget_exceeded", "stage_cap", "step_cap", "context_overflow", "malformed_output"})


@asynccontextmanager
async def checkpointing(config: CheckpointConfig | None, inst: HarnessInstance,
                        pause: PauseRule | None = None) -> AsyncIterator[Checkpoint | None]:
    """Open the run's checkpoint saver and give the instance its LangGraph thread (a no-op when off). The saver stays
    open for the whole run, so a paused run resumes against the same checkpoints; it is closed before the sandbox
    is torn down, also on cancel."""
    if config is None:
        if pause is not None:
            raise ValueError("a pause rule needs checkpointing (HF_CHECKPOINT=sqlite|postgres)")
        yield None
        return
    async with open_saver(config) as saver:
        inst.checkpoint = Checkpoint(saver=saver, thread_id=Checkpoint.thread_for(inst.run_key.short()),
                                     backend=config.backend, pause=pause)
        try:
            yield inst.checkpoint
        finally:
            inst.checkpoint = None


async def run_shape(shape: Harness, inst: HarnessInstance) -> RunResult:
    """Run the shape; a run paused between two nodes is resumed from its checkpoint, in this same provision.

    The resume is the same run: same RunKey, the same budget object (so what was used before the pause stays used
    and no allowance is reset), the same tracer (one trace, plus a `resume` handoff span no grader or cost counts),
    the same tool violations, snapshot and sandbox. Only the graph state comes back from the checkpoint."""
    cp = inst.checkpoint
    while True:
        try:
            return await shape.run(inst)
        except RunPaused as e:
            if cp is None:              # only a checkpointed graph pauses
                raise
            cp.resumes += 1
            cp.resuming = True
            with inst.tracer.span(SpanKind.handoff, "resume", thread_id=e.thread_id, after_nodes=e.nodes,
                                  next=",".join(e.next_nodes), resume=cp.resumes):
                pass
        finally:
            if cp is not None:
                inst.snapshot["checkpoint"] = cp.describe()


def overran(result: RunResult) -> bool:
    return any(v.kind in OVERRUN_KINDS for v in result.violations)


def _finalize_status(result: RunResult, grades: list[Grade], threshold: float) -> RunStatus:
    """Every scored run gets its status from the environment (the outcome family: state_diff, plus any grader
    registered to feed it). Unscored runs keep INCOMPLETE / ERROR."""
    if not result.scored:
        return result.status
    outcome = combine_families(grades).outcome
    critical = any(v.critical for g in grades for v in g.violations) or any(v.critical for v in result.violations)
    if outcome is None or critical:
        return RunStatus.FAIL
    if outcome >= threshold and not overran(result):
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL if outcome > 0 else RunStatus.FAIL


def run_scores(result: RunResult, grades: list[Grade]) -> tuple[FamilyScores, list[Violation], bool]:
    """FamilyScores, every violation, and whether one is critical: O and Q from the graders feeding them."""
    fam = combine_families(grades)
    violations = list(result.violations) + [v for g in grades for v in g.violations]
    critical = any(v.critical for v in violations)
    # A critical violation (a guard broken, a forbidden write) fails the run outright: its outcome is 0, not
    # the share of checks it passed, so it can't lift the cell's robustness R (an average of outcomes).
    scores = FamilyScores(
        outcome=(fam.outcome * (OVERRUN_OUTCOME_FACTOR if overran(result) else 1.0)
                 if fam.outcome is not None and result.scored and not critical else 0.0),
        trajectory=fam.trajectory if fam.trajectory is not None else 1.0,
        efficiency=0.0,   # normalized against the class median at aggregation time (P2)
        safety=len(violations),
    )
    return scores, violations, critical


def _annotate_root(root: Span, inst, task: Task, recipe: HarnessRecipe, result: RunResult, grades: list[Grade],
                   ended_by: str, campaign_id: str | None) -> None:
    """The root span as Phoenix shows it: the prompt, the final reply, the run's metadata (Span.otel, never persisted).
    Everything comes from what the run already computed."""
    if not inst.tracer.exporting():
        return
    k = inst.run_key
    first = {m.get("role"): m.get("content") for m in reversed(inst.messages)}     # the first of each role
    stop = ended_by + (f" ({result.error})" if result.error else "")
    final = result.output if isinstance(result.output, str) and result.output.strip() else \
        f"(no final message: ended by {stop})"
    counts = {kind: sum(s.kind == kind for s in inst.tracer.spans) for kind in (SpanKind.model_call, SpanKind.tool_call)}
    limit = settings.trace_max_chars()
    metadata = {"task": k.task_version, "recipe": recipe.id, "recipe_hash": k.recipe_hash, "model": k.canonical_model,
                "provider": k.provider, "seed": k.seed, "status": result.status.value,
                "outcome": combine_families(grades).outcome, "ended_by": stop,
                "model_calls": counts[SpanKind.model_call], "tool_calls": counts[SpanKind.tool_call],
                "system_prompt": openinference.cap(str(first.get("system") or ""), limit)}
    root.otel_name = f"{recipe.id} · {task.id}"
    root.otel.update(openinference.run_root(prompt=str(first.get("user") or task.instruction), output=final,
                                            metadata=metadata, agent=recipe.id, session=campaign_id, limit=limit))


async def run_one(f: Factory, task: Task, recipe: HarnessRecipe, canonical_model: str, provider_id: str,
                  seed: int = 0, backend: Backend | None = None, flush: bool = True,
                  fault: str | None = None, pause: PauseRule | None = None,
                  campaign_id: str | None = None) -> EffectivenessRecord:
    """flush: push buffered OTLP spans after the run. Campaigns pass False and flush once at the end.
    campaign_id: groups a campaign's runs in Phoenix (`session.id` on the root span); observability only.
    fault: a fault profile id (catalog/faults) to inject tool failures; None = a clean run.
    pause: with checkpointing on (f.checkpoint), pause the run between two nodes when this says so; it is resumed
    from the checkpoint and must end exactly like an uninterrupted run (shapes/checkpoint.py)."""
    f.graders.check(task)          # a grader typo fails here, before any quota is spent (and on every resume)
    t0 = time.monotonic()
    async with provision(task, recipe, canonical_model, provider_id, declared=f.declared, providers=f.providers,
                         ledger=f.ledger, seed=seed, backend=backend, fault=f.fault(fault)) as p, \
            checkpointing(f.checkpoint, p.instance, pause):
        inst = p.instance
        shape = harness_for(recipe)          # the recipe's framework runner (ADR 0016), or its native shape
        k = inst.run_key
        with inst.tracer.span(SpanKind.run, k.short(), task=k.task_version, recipe=recipe.id, recipe_hash=k.recipe_hash,
                              model=k.canonical_model, provider=k.provider, seed=k.seed,
                              variant=recipe.variant.value, runtime=recipe.runtime or "native",
                              task_class=p.task_class,
                              prompt_hash=inst.snapshot["prompt_hash"]) as root:
            inst.budget.start()
            try:
                result = await run_shape(shape, inst)
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
            remote, unreadable = await p.export_tenants()
            if unreadable and result.scored:
                # The end state of a remote test tenant is part of the environment: without it the run can't be
                # graded, and it isn't the harness's doing.
                result.status, result.scored = RunStatus.INCOMPLETE, False
                result.error = f"infra: {unreadable}"
            if remote:
                inst.snapshot["tenant_state"] = remote
            after = p.inspector.capture()
            diff = StateInspector.diff(p.before, after)
            ctx = GradeContext(task=task, result=result, before=p.before, after=after, diff=diff,
                               workspace=p.sandbox.workspace, db_path=p.sandbox.db_path,
                               spans=list(inst.tracer.spans), max_steps=recipe.config.max_steps, remote=remote,
                               sandbox=p.sandbox)
            judge_s = 0.0
            with inst.tracer.span(SpanKind.grade, "pipeline"):
                if f.judge is not None and task.oracle.rubric_checks():
                    # The judge's calls are on its own tracer and its time is not the run's: neither is in C.
                    t_judge = time.monotonic()
                    ctx.judgements = await f.judge.judge_checks(ctx)
                    judge_s = time.monotonic() - t_judge
                # In a worker thread: a tests_pass check runs pytest for up to minutes, and the other runs of the
                # campaign share this event loop.
                grades = await asyncio.to_thread(f.graders.run, ctx)
            pre_judge = apply_judge_failure(result, ctx.judgements)
            ended_by = result.status.value
            result.status = _finalize_status(result, grades, task.oracle.pass_threshold)
            root.attributes["status"] = result.status.value
            _annotate_root(root, inst, task, recipe, result, grades, ended_by, campaign_id)
        result.trajectory = list(inst.tracer.spans)
        result.cost = _cost(inst.tracer.spans, f.providers, provider_id, time.monotonic() - t0 - judge_s)
        trace_path = inst.tracer.write_jsonl()
        transcript_path = write_transcript(trace_path, inst, result)
        endstate_path = await asyncio.to_thread(save_endstate, trace_path, ctx, task_class=p.task_class,
                                                weight_profile=p.weight_profile,
                                                **({"pre_judge": pre_judge} if pre_judge else {}))

        scores, violations, critical = run_scores(result, grades)
        weights = ScoreWeights.for_profile(p.weight_profile)
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
            endstate_path=endstate_path,
            judge_usage=judge_usage(ctx.judgements, wall_s=judge_s),
            tools_hash=f.declared.env_hash(recipe.tools.mcp_servers, "*"),
            served_id=f.providers.served(provider_id, canonical_model).provider_model_id,
        )
    if f.store:
        f.store.add(rec)
    await _mirror(f, rec, recipe, inst.tracer.otel_trace_id)
    if flush:
        await asyncio.to_thread(Tracer.flush)
    return rec


async def _mirror(f: Factory, rec: EffectivenessRecord, recipe: HarnessRecipe,
                  otel_trace_id: str | None = None) -> None:
    """Mirror a stored run to MLflow. The results store is the source of truth, so a mirror failure is a warning,
    never a run or campaign error. The MLflow client is synchronous network I/O: it runs in a thread so a slow
    server can't stall the event loop (and the wall time of every other run on it)."""
    if not f.tracker.enabled:
        return
    try:
        # The exported trace's id goes on the MLflow run, when spans were exported (OpenTelemetry on).
        extra = {"otel_trace_id": otel_trace_id} if otel_trace_id else {}
        await asyncio.to_thread(f.tracker.log, rec, recipe, **extra)
    except Exception as e:  # noqa: BLE001  any client or server failure: the run itself is already stored
        f.tracker.failures = getattr(f.tracker, "failures", 0) + 1
        log.warning("MLflow mirror failed for %s: %s: %s", rec.run_key.short(), type(e).__name__, str(e)[:300])


def _reference_index(task: Task, plan: list[dict]):
    """A retrieval step in a reference plan (ADR 0017) runs on the task's corpus with REFERENCE_CONFIG."""
    if not any(step["tool"] == retrieval.RETRIEVE_REF for step in plan):
        return None
    return retrieval.build_index(task, retrieval.REFERENCE_CONFIG)


def _check_retrieved(task: Task, plan: list[dict], i: int, text: str) -> None:
    """A reference retrieval step must find every corpus file the plan reads before its next retrieval step."""
    found = {p["source"] for p in json.loads(text)["passages"]}
    reads = []
    for step in plan[i + 1:]:
        if step["tool"] == retrieval.RETRIEVE_REF:
            break
        path = step["args"].get("path", "") if step["tool"] == "fs.read_file" else ""
        if path.startswith(f"{retrieval.CORPUS_PREFIX}/"):
            reads.append(path)
    missing = [p for p in reads if p not in found]
    if missing:
        raise RuntimeError(f"{task.key()} reference retrieval {plan[i]['args']} does not return {missing} "
                           f"(got {sorted(found)})")


async def run_reference(f: Factory, task: Task) -> Grade:
    """TSK-04: the reference plan must pass the task's own oracle. Rubric checks are judged too, and a judged
    rubric check the reference fails fails the reference (even at weight 0): its rubric or its text is wrong."""
    ref = load_reference(task)
    servers = sorted({step["tool"].split(".")[0] for step in ref.plan} - {retrieval.RETRIEVE_SERVER}) or ["fs"]
    index = await asyncio.to_thread(_reference_index, task, ref.plan)
    with make_sandbox(label=f"ref-{task.id}") as sb:
        sb.reset(task)
        insp = StateInspector(sb.workspace, sb.db_path)
        before = insp.capture()
        tracer = Tracer(f"reference|{task.key()}")
        outputs: list[tuple[str, int]] = []
        async with MCPSessionPool(f.declared, servers, sb.env(), sb.launch) as pool:
            for i, step in enumerate(ref.plan):
                with tracer.span(SpanKind.tool_call, step["tool"]) as span:
                    span.attributes["args"] = json.dumps(step["args"], default=str)[:ARGS_CHARS]   # as ToolExecutor
                    if step["tool"] == retrieval.RETRIEVE_REF:
                        res = await retrieval.local_tool(index, []).call(step["args"])
                        if not res.is_error:
                            _check_retrieved(task, ref.plan, i, res.text)
                    else:
                        res = await pool.call(step["tool"], step["args"])
                    if res.is_error:
                        span.status = "error"
                        raise RuntimeError(f"{task.key()} reference step {step['tool']} failed: {res.text}")
                    tracer.tool_io(span, step["args"], res.text)
                    outputs.append((step["tool"], len(res.text)))
        perturb.check_reference_outputs(task, outputs)        # an oversized_output variant's call is past the cap
        after = insp.capture()
        dummy = RunResult(run_key=RunKey(task_version=task.key(), task_hash=task.content_hash(), recipe_hash="ref",
                                         canonical_model="reference", provider="none"),
                          output=ref.final, finished=True, scored=True)
        ctx = GradeContext(task=task, result=dummy, before=before, after=after, diff=StateInspector.diff(before, after),
                           workspace=sb.workspace, db_path=sb.db_path, spans=tracer.spans, max_steps=10_000,
                           sandbox=sb)
        if f.judge is not None and task.oracle.rubric_checks():
            ctx.judgements = await f.judge.judge_checks(ctx)
        grade = await asyncio.to_thread(f.graders.graders["state_diff"].grade, ctx)
        rejected = [v.check_id for v in ctx.judgements.values() if v.available and not v.passed]
        if rejected:
            grade.passed = False
            grade.detail = f"the judge fails the reference on {', '.join(rejected)}; " + grade.detail
        return grade


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
         faults: list[str | None] | None = None, tags: list[str] | None = None,
         exclude_tags: list[str] | None = None, unreachable_ok: list[str] | None = None) -> list[RunSpec]:
    """Every task x recipe x model x fault profile x seed, in a fixed order (results come back in this order).
    faults: fault profile ids to add next to the clean runs, e.g. [None, "transient"] (R needs both).
    tags: only tasks carrying at least one of these (e.g. ["batch-4"]); exclude_tags: leave out tasks carrying any
    (the CI shape gate leaves out `long` tasks, whose references exceed V3's stage caps and V4/V5's plan length).
    A recipe that can't call every tool a task allows is not planned on it (`provisioner.reaches`, the rule the
    selector prunes by; `skipped_pairs` lists them): v2-base never runs a task that needs the git, biz or docs server.
    unreachable_ok: recipes planned on every selected task all the same, because their reduced tool set is the point
    of the comparison (a no-engine baseline, an engine-only variant). The selector's pruning is unchanged."""
    recipe_ids = [recipe_ids] if isinstance(recipe_ids, str) else recipe_ids
    models = models or [m.canonical for m in f.providers.get(provider_id).models]
    tasks = plan_tasks(f, task_ids, tags, exclude_tags)
    faults = faults or [None]
    for fid in faults:
        f.fault(fid)                                         # unknown profile: fail before anything runs
 

    for t in tasks:
        f.graders.check(t)                                   # unknown grader: likewise
    skip = {(t, r) for t, r, _ in skipped_pairs(f, recipe_ids, tasks, unreachable_ok)}
    # A recipe that can't run on a model at all (A3: its reply doesn't fit, or asks for more output than the model
    # returns) is left out for that model, with the reason (`models_left_out`); the recipe is never adjusted.
    no_model = {(r, m) for r, m, _ in models_left_out(f, recipe_ids, provider_id, models)}
    # A variant that binds its own faults (tool_failure, slow_tool; TSK-04) is run clean only: a campaign fault
    # profile on it is refused (provision), so the plan leaves those runs out instead of failing them.
    return [RunSpec(task_id=t.id, recipe_id=r, canonical_model=m, provider=provider_id, seed=s,
                    fault=None if fid in (None, NO_FAULTS) else fid)
            for t in tasks for r in recipe_ids if (t.id, r) not in skip
            for m in models if (r, m) not in no_model
            for fid in faults if fid in (None, NO_FAULTS) or t.bound_faults() is None
            for s in range(seeds)]


def models_left_out(f: Factory, recipe_ids: list[str], provider_id: str,
                    models: list[str]) -> list[tuple[str, str, list[str]]]:
    """(recipe id, model, reasons) for each recipe x model pair that can't run (`validate.pair_issues`, #122)."""
    from harness_factory.recipes.validate import pair_issues
    out = []
    for r in recipe_ids:
        if r not in f.recipes:
            continue
        for m in models:
            issues = pair_issues(f.recipes[r], f.providers, provider_id, m)
            if issues:
                out.append((r, m, [f"{i.rule}: {i.detail}" for i in issues]))
    return out


def describe_models_left_out(pairs: list[tuple[str, str, list[str]]]) -> list[str]:
    return [f"{r} on {m}: {'; '.join(reasons)}" for r, m, reasons in pairs]


def plan_tasks(f: Factory, task_ids: list[str] | None = None, tags: list[str] | None = None,
               exclude_tags: list[str] | None = None) -> list[Task]:
    """The tasks a plan covers: `task_ids` (or every runnable task), narrowed by tags."""
    tasks = [f.tasks.get(t) for t in task_ids] if task_ids else f.tasks.all()
    if tags:
        tasks = [t for t in tasks if set(tags) & set(t.tags)]
    if exclude_tags:
        tasks = [t for t in tasks if not set(exclude_tags) & set(t.tags)]
    if (tags or exclude_tags) and not tasks:
        raise ValueError(f"no runnable task left after tags={tags} exclude_tags={exclude_tags}")
    return tasks


def skipped_pairs(f: Factory, recipe_ids: list[str], tasks: list[Task],
                  unreachable_ok: list[str] | None = None) -> list[tuple[str, str, list[str]]]:
    """(task id, recipe id, missing tools) for each pair a plan leaves out: the recipe can't call every tool the task
    allows, and it isn't in `unreachable_ok`."""
    from harness_factory.provision.provisioner import missing_task_tools
    ok = set(unreachable_ok or [])
    out = []
    for t in tasks:
        for r in recipe_ids:
            if r in f.recipes and r not in ok and (missing := missing_task_tools(f.recipes[r], t, f.declared)):
                out.append((t.id, r, missing))
    return out


def reduced_pairs(f: Factory, recipe_ids: list[str], tasks: list[Task],
                  unreachable_ok: list[str]) -> list[tuple[str, str, list[str]]]:
    """(task id, recipe id, missing tools) for each pair planned only because its recipe is in `unreachable_ok`."""
    return skipped_pairs(f, [r for r in recipe_ids if r in set(unreachable_ok)], tasks)


def describe_pairs(pairs: list[tuple[str, str, list[str]]], limit: int = 6) -> list[str]:
    """One line per recipe: on which tasks, and which tools it can't call (grouped: the same few tools repeat)."""
    by_recipe: dict[str, list[tuple[str, list[str]]]] = {}
    for t, r, missing in pairs:
        by_recipe.setdefault(r, []).append((t, missing))
    lines = []
    for r, items in by_recipe.items():
        tasks = [t for t, _ in items]
        tools = sorted({m for _, ms in items for m in ms})
        lines.append(f"{r} on {len(tasks)} task(s) ({', '.join(tasks[:limit])}{', ...' if len(tasks) > limit else ''})"
                     f": can't call {', '.join(tools[:limit])}{', ...' if len(tools) > limit else ''}")
    return lines


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
    campaign_id = f"campaign-{uuid.uuid4().hex[:12]}"   # groups the runs in Phoenix (session.id); observability only

    async def one(spec: RunSpec) -> EffectivenessRecord | CampaignError:
        async with gate:
            try:
                return await run_one(f, f.tasks.get(spec.task_id), f.recipes[spec.recipe_id], spec.canonical_model,
                                     spec.provider, seed=spec.seed, flush=False, fault=spec.fault,
                                     campaign_id=campaign_id)
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
                seeds: int = 1, concurrency: int = 1, task_ids: list[str] | None = None) -> list[EffectivenessRecord]:
    """Every task (or task_ids) x recipe x model x seed. Several recipes make this a like-for-like shape
    comparison. Raises on the first failed run (a gate must not pass with runs missing)."""
    result = await run_campaign(f, plan(f, recipe_ids, provider_id, models, seeds, task_ids=task_ids), concurrency)
    if result.errors:
        e = result.errors[0]
        raise RuntimeError(f"{len(result.errors)} run(s) failed; first: {e.spec.model_dump()} -> {e.error}")
    return result.records
