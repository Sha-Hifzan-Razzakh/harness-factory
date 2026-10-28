"""Provisioner (B5 entry point): recipe + task + model + provider -> one live harness, for one run.

P0 provisions an explicit recipe. The Selector (P4) will choose the recipe from the task profile;
until then task_class() supplies the class key every result is filed under.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from fnmatch import fnmatch

from harness_factory import settings
from harness_factory.env.mcp.servers import SideEffect, ToolCatalogFile
from harness_factory.env.sandbox import LocalSandbox, make_sandbox
from harness_factory.env.state import EnvState, StateInspector
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import HarnessRecipe
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.base import HarnessInstance
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import Backend, ModelGateway
from harness_factory.substrate.prompts import PromptAssembler
from harness_factory.substrate.tools import ToolExecutor, model_name
from harness_factory.tasks.model import Task
from harness_factory.tasks.reference import PlanBackend, load_reference

_SE_ORDER = [SideEffect.READ, SideEffect.REVERSIBLE, SideEffect.IRREVERSIBLE]


def task_tools(task: Task, declared: ToolCatalogFile) -> list[str]:
    return [r for r in declared.all_refs() if any(fnmatch(r, p) for p in task.allowed_tools)]


def max_side_effect(task: Task, declared: ToolCatalogFile) -> SideEffect:
    effects = [declared.spec(r).side_effect for r in task_tools(task, declared)] or [SideEffect.READ]
    return max(effects, key=_SE_ORDER.index)


def task_class(task: Task, declared: ToolCatalogFile) -> str:
    """Class key = depth x side effects x risk (PRF-02)."""
    return f"{task.expected_depth.value}|{max_side_effect(task, declared).value}|{task.risk}"


def weight_profile(task: Task, declared: ToolCatalogFile) -> str:
    return max_side_effect(task, declared).value.lower()


def allowed_tools(recipe: HarnessRecipe, task: Task, declared: ToolCatalogFile) -> set[str]:
    """What the model may call: recipe slice AND task allowance, on servers the recipe opens."""
    return {r for r in task_tools(task, declared)
            if recipe.tools.allows(r) and r.split(".")[0] in recipe.tools.mcp_servers}


def backend_for(provider_kind: str, canonical_model: str, task: Task) -> Backend | None:
    if provider_kind != "scripted":
        return None
    style = canonical_model.split("/", 1)[-1]
    return PlanBackend(load_reference(task), style)


async def _start_sandbox(task: Task) -> LocalSandbox:
    """Create and seed the sandbox in a worker thread. Threads cannot be cancelled, so if the run is cancelled
    meanwhile (Ctrl-C), wait for the thread to finish and tear down what it made rather than leak it."""
    def make_and_reset() -> LocalSandbox:
        sb = make_sandbox(task.id)
        try:
            sb.reset(task)
        except BaseException:
            sb.teardown()
            raise
        return sb

    job = asyncio.ensure_future(asyncio.to_thread(make_and_reset))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        try:
            sb = await job
        except Exception:  # noqa: BLE001  (creation failed: nothing to clean up)
            raise asyncio.CancelledError from None
        await asyncio.to_thread(sb.teardown)
        raise


@dataclass
class Provisioned:
    instance: HarnessInstance
    sandbox: LocalSandbox
    inspector: StateInspector
    before: EnvState
    task_class: str
    weight_profile: str


@asynccontextmanager
async def provision(task: Task, recipe: HarnessRecipe, canonical_model: str, provider_id: str, *,
                    declared: ToolCatalogFile, providers: ProviderCatalog, ledger: QuotaLedger,
                    seed: int = 0, backend: Backend | None = None,
                    assembler: PromptAssembler | None = None) -> AsyncIterator[Provisioned]:
    recipe = recipe.with_model(canonical_model)
    issues = validate_recipe(recipe, declared, providers=providers, provider_id=provider_id)
    if issues:
        raise HarnessError(ErrorKind.PROVISION, "; ".join(f"{i.rule}: {i.detail}" for i in issues))
    recipe_hash = recipe.content_hash()                  # before the seed is applied: seed is a RunKey axis
    for slot in recipe.model_slots.values():
        slot.seed = seed if slot.seed is None else slot.seed

    served = providers.served(provider_id, canonical_model)
    run_key = RunKey(task_version=task.key(), task_hash=task.content_hash(), recipe_hash=recipe_hash,
                     canonical_model=canonical_model, provider=provider_id, seed=seed,
                     env_hash=declared.env_hash(recipe.tools.mcp_servers, served.provider_model_id))
    tracer = Tracer(run_key.as_str())
    budget = BudgetGuard(recipe.config)
    provider = providers.get(provider_id)
    gateway = ModelGateway(providers, ledger, tracer, provider_id,
                           backend=backend or backend_for(provider.kind, canonical_model, task),
                           retries=recipe.config.retries, backoff_s=recipe.config.backoff_s,
                           timeout_s=recipe.config.call_timeout_s or settings.CALL_TIMEOUT_S)
    budget.excluded_s = lambda: gateway.infra_wait_s

    # Sandbox set-up and tear-down can block (Docker run/rm, image builds): keep them off the event loop so one
    # run's container start never stalls the other workers.
    sandbox = await _start_sandbox(task)
    try:
        inspector = StateInspector(sandbox.workspace, sandbox.db_path)
        before = inspector.capture()
        allowed = allowed_tools(recipe, task, declared)
        async with MCPSessionPool(declared, recipe.tools.mcp_servers, sandbox.env(), sandbox.launch) as pool:
            tools = ToolExecutor(pool, declared, allowed, recipe.policies, budget, tracer, run_key.as_str())
            assembler = assembler or PromptAssembler()
            messages = assembler.assemble(recipe, task, sorted(model_name(r) for r in allowed))
            stage_prompts = assembler.stage_prompts(recipe, task, sorted(model_name(r) for r in allowed))
            instance = HarnessInstance(
                run_key=run_key, recipe=recipe, task=task, gateway=gateway, tools=tools, budget=budget,
                tracer=tracer, messages=messages, stage_prompts=stage_prompts,
                snapshot={"prompt_hash": PromptAssembler.prompt_hash(
                              messages + [{"stage": k, "content": v} for k, v in stage_prompts.items()]),
                          "tool_catalog_hash": pool.catalog.catalog_hash() if pool.catalog else None,
                          "mcp_transports": pool.transports(), "allowed_tools": sorted(allowed),
                          **sandbox.describe()},
            )
            yield Provisioned(instance=instance, sandbox=sandbox, inspector=inspector, before=before,
                              task_class=task_class(task, declared), weight_profile=weight_profile(task, declared))
    finally:
        await asyncio.to_thread(sandbox.teardown)
