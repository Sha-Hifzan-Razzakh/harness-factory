"""Delegate tool (EXT-09, EXT-11): a harness hands a sub-task to a child recipe.

The child runs in the same sandbox (same MCP sessions, same files and database) through the same model gateway,
so its tokens, cost and quota land in the parent run. It is a harness-side tool, not an MCP server: it needs the
gateway and the quota ledger, which never enter the sandbox.

Limits, all from the root recipe and shared by the whole tree:
  - at most `max_calls` delegate calls per run, nested at most `max_depth` deep (the child of the last level is
    not offered the tool);
  - a recipe already on the call chain is refused (load_recipes() already refuses cycles);
  - the child's grant is a subset of its parent's: its tools are its own slice intersected with the parent's
    allowed tools, and per side effect the stricter of the two permission modes applies;
  - the child's budget is its own config, capped by what the parent has left; what it uses is then charged to the
    parent, so delegating never buys extra steps, tool calls or tokens.

A child stopping early is not a parent overrun: it is recorded as `delegate_stopped` and the parent carries on.
A child that ends on infrastructure (INCOMPLETE) or a setup failure (ERROR) ends the parent run the same way: the
tool batch stops with the child's stop reason, so the run is never graded on work the outage cut short.
The child's conversation is kept in the parent's snapshot (and so in the run's transcript file).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness_factory.observability.span import SpanKind
from harness_factory.recipes.model import HarnessRecipe, PolicySet, RuntimeConfig
from harness_factory.shapes.base import HarnessInstance, ShapeRegistry
from harness_factory.shapes.result import RunResult, RunStatus, Violation
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind
from harness_factory.substrate.prompts import PromptAssembler
from harness_factory.substrate.tools import PREVIEW_CHARS, ToolExecutor, model_name
from harness_factory.tasks.model import Task

DELEGATE_REF = "delegate.run"
DELEGATE_NAME = model_name(DELEGATE_REF)
DESCRIPTION = ("Hand a self-contained sub-task to a helper agent. It works in the same workspace with the same tools "
               "and returns its final answer. It sees only what you write here, so describe the sub-task fully.")
SCHEMA = {"type": "object", "required": ["task"],
          "properties": {"task": {"type": "string", "description": "The sub-task, with everything the helper needs"}}}

# A child's harness stop is not the parent's overrun (runner.OVERRUN_KINDS): renamed so it never halves the outcome.
CHILD_STOPS = {"budget_exceeded", "stage_cap", "step_cap", "context_overflow", "malformed_output"}
_STRICTNESS = ["AUTO", "ASK", "DENY"]


@dataclass
class DelegateLimits:
    """Shared by every level of one run."""
    max_calls: int
    max_depth: int
    calls: int = 0


@dataclass
class DelegateContext:
    limits: DelegateLimits
    depth: int                     # 0 = the run's own recipe
    chain: tuple[str, ...]         # recipe ids from the root down to this level
    child: HarnessRecipe           # the recipe this level delegates to, model filled in
    task: Task                     # the run's task (seed, allowance); a sub-task replaces only its instruction
    assembler: PromptAssembler


def context_for(recipe: HarnessRecipe, task: Task, assembler: PromptAssembler,
                limits: DelegateLimits | None = None, depth: int = 0,
                chain: tuple[str, ...] = ()) -> DelegateContext | None:
    """The delegate context for a level running `recipe`, or None when it has no (resolved) delegation."""
    if recipe.delegation is None or recipe.delegation.child is None:
        return None
    limits = limits or DelegateLimits(recipe.delegation.max_calls, recipe.delegation.max_depth)
    slot = recipe.model_slots["executor"]
    child = recipe.delegation.child.with_model(slot.canonical_model or "")
    for s in child.model_slots.values():
        s.seed = slot.seed if s.seed is None else s.seed
    return DelegateContext(limits=limits, depth=depth, chain=(*chain, recipe.id), child=child, task=task,
                           assembler=assembler)


def descendants(recipe: HarnessRecipe) -> list[HarnessRecipe]:
    out, cur = [], recipe
    while cur.delegation is not None and cur.delegation.child is not None and len(out) < cur.delegation.max_depth:
        cur = cur.delegation.child
        out.append(cur)
    return out


def available(inst: HarnessInstance, restrict: Callable[[str], bool] | None = None) -> bool:
    d = inst.delegate
    return d is not None and d.depth < d.limits.max_depth and (restrict is None or restrict(DELEGATE_REF))


def tool_spec(inst: HarnessInstance, restrict: Callable[[str], bool] | None = None) -> dict[str, Any] | None:
    if not available(inst, restrict):
        return None
    return {"type": "function", "function": {"name": DELEGATE_NAME, "description": DESCRIPTION, "parameters": SCHEMA}}


def stricter(parent: PolicySet, child: PolicySet) -> PolicySet:
    modes = {k: max(parent.permission_mode.get(k, "DENY"), child.permission_mode.get(k, "DENY"), key=_STRICTNESS.index)
             for k in set(parent.permission_mode) | set(child.permission_mode)}
    return child.model_copy(update={"permission_mode": modes})


def _caps(child: RuntimeConfig, parent: BudgetGuard) -> dict[str, float]:
    left = parent.remaining()
    return {"max_steps": min(child.max_steps, left["steps"]),
            "max_tool_calls": min(child.max_tool_calls, left["tool_calls"] - 1),
            "max_tokens": min(child.max_tokens, left["tokens"]),
            "wall_time_s": min(child.wall_time_s, parent.limits.wall_time_s - parent.elapsed_s())}


def shortfall(child: RuntimeConfig, parent: BudgetGuard) -> str | None:
    """The parent limit that leaves no room for a helper ("max_steps 15"), or None if one fits."""
    for name, value in _caps(child, parent).items():
        if value <= 0:
            return f"{name} {getattr(parent.limits, name)}"
    return None


def capped(child: RuntimeConfig, parent: BudgetGuard) -> RuntimeConfig | None:
    """The child's limits, capped by what the parent has left (one tool call is the delegate call itself)."""
    if shortfall(child, parent):
        return None
    caps = _caps(child, parent)
    return child.model_copy(update={"max_steps": caps["max_steps"], "max_tool_calls": caps["max_tool_calls"],
                                    "max_tokens": caps["max_tokens"], "wall_time_s": int(max(1, caps["wall_time_s"]))})


def child_stop(result: RunResult) -> str:
    """The stop reason a child that ended INCOMPLETE or ERROR hands to its parent ("error:PROVIDER_ERROR")."""
    head = (result.error or "").split(": ", 1)[0]
    if head.startswith("error:"):
        return head
    kind = ErrorKind.PROVIDER_ERROR if result.status == RunStatus.INCOMPLETE else ErrorKind.CONFIG
    return f"error:{kind.value}"


async def run_delegate(inst: HarnessInstance, raw_args: str | dict, restrict: Callable[[str], bool] | None = None,
                       scope: str | None = None) -> tuple[str, str | None]:
    """Run one delegate call. Returns (the text the parent model sees, a stop reason for the parent run or None).
    The stop is set only when the child ended INCOMPLETE or ERROR; its detail is in `inst.stop_error`.
    Traced as a tool call, charged as one."""
    with inst.tracer.span(SpanKind.tool_call, DELEGATE_REF, **({"scope": scope} if scope else {})) as span:
        text, error, stop = await _delegate(inst, raw_args, restrict, span)
        if error:
            span.status, span.error = "error", error
        span.attributes["result_chars"] = len(text)
        span.attributes["result_preview"] = text[:PREVIEW_CHARS]
    inst.budget.charge(span)
    return text, stop


async def _delegate(inst: HarnessInstance, raw_args, restrict, span) -> tuple[str, str | None, str | None]:
    d = inst.delegate
    if not available(inst, restrict):
        inst.tools.violations.append(Violation(kind="tool_not_allowed", detail=f"{DELEGATE_REF} ({scope_of(span)})"))
        return f"Tool {DELEGATE_REF} is not available here.", ErrorKind.TOOL_NOT_ALLOWED.value, None
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        sub = str(args["task"]).strip()
        if not sub:
            raise ValueError("empty task")
    except (ValueError, TypeError, KeyError) as e:
        return f"Invalid arguments for {DELEGATE_REF}: give a non-empty 'task' ({e})", ErrorKind.TOOL_ARGS.value, None
    span.attributes["args"] = json.dumps({"task": sub})[:2000]
    if d.limits.calls >= d.limits.max_calls:
        return f"Delegate limit reached: at most {d.limits.max_calls} calls per run.", ErrorKind.TOOL_ERROR.value, None
    if d.child.id in d.chain:
        return f"Refused: {d.child.id} is already on the delegation chain.", ErrorKind.POLICY.value, None
    config = capped(d.child.config, inst.budget)
    if config is None:
        return "Refused: not enough budget left for a helper.", ErrorKind.BUDGET.value, None
    d.limits.calls += 1
    child = d.child.model_copy(update={"config": config})
    span.attributes.update(delegate_recipe=child.id, depth=d.depth + 1, call=d.limits.calls)

    allowed = {r for r in inst.tools.allowed if child.tools.allows(r) and r.split(".")[0] in child.tools.mcp_servers}
    budget = BudgetGuard(config, excluded_s=inst.budget.excluded_s)
    tools = ToolExecutor(inst.tools.pool, inst.tools.declared, allowed, stricter(inst.tools.policy, child.policies),
                         budget, inst.tracer, inst.tools.run_key, faults=inst.tools.faults,
                         agent_calls=inst.tools.agent_calls)
    task = d.task.model_copy(update={"instruction": sub})
    grand = context_for(child, d.task, d.assembler, d.limits, d.depth + 1, d.chain)
    names = sorted(model_name(r) for r in allowed) + ([DELEGATE_NAME] if grand and grand.depth < d.limits.max_depth else [])
    child_inst = HarnessInstance(
        run_key=inst.run_key, recipe=child, task=task, gateway=inst.gateway, tools=tools, budget=budget,
        tracer=inst.tracer, messages=d.assembler.assemble(child, task, names),
        stage_prompts=d.assembler.stage_prompts(child, task, names), scope=f"delegate:{d.limits.calls}",
        delegate=grand, prompts=d.assembler.registry)
    budget.start()
    with inst.tracer.span(SpanKind.handoff, f"delegate:{child.id}", depth=d.depth + 1):
        result = await ShapeRegistry.get(child.variant).run(child_inst)

    inst.budget.used_steps += budget.used_steps
    inst.budget.used_tokens += budget.used_tokens
    inst.budget.used_tool_calls += budget.used_tool_calls
    for v in result.violations:
        kind = "delegate_stopped" if v.kind in CHILD_STOPS else v.kind
        inst.tools.violations.append(Violation(kind=kind, detail=f"delegate {d.limits.calls}: {v.detail}"[:300],
                                               critical=v.critical))
    status = "finished" if result.finished else result.status.value     # a child is never graded on its own
    inst.snapshot.setdefault("delegations", []).append(
        {"call": d.limits.calls, "recipe": child.id, "depth": d.depth + 1, "task": sub, "status": status,
         "error": result.error, "output": result.output, "messages": result.messages})
    span.attributes.update(child_status=status, child_steps=budget.used_steps,
                           child_tool_calls=budget.used_tool_calls, child_tokens=budget.used_tokens)
    answer = result.output if isinstance(result.output, str) else ""
    if result.finished:
        return f"Helper finished. Its answer: {answer}", None, None
    if result.status == RunStatus.BUDGET_EXCEEDED:
        return f"Helper stopped early ({result.error}). Check its work before relying on it.", None, None
    # INCOMPLETE (infrastructure) or ERROR (setup, crash): the parent run ends the same way (never graded).
    inst.stop_error = f"delegate {d.limits.calls}: {result.error}"[:500]
    stop = child_stop(result)
    return f"Helper failed: {result.error}", stop.removeprefix("error:"), stop


def scope_of(span) -> str:
    return span.attributes.get("scope") or "this run"
