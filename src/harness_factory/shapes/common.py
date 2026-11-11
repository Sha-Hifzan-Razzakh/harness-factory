"""Pieces every shape shares: one model turn, one batch of tool calls, and how a stop reason becomes a status.

Keeping these in one place means shapes differ only in control flow, which is the thing being compared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from harness_factory.recipes.model import ModelSlot
from harness_factory.shapes import batch, compaction, delegate
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult, RunStatus, Violation
from harness_factory.substrate.context import estimate_tokens, window_for
from harness_factory.substrate.errors import INFRA_KINDS, ErrorKind, HarnessError
from harness_factory.substrate.spill import SPILL_NAME
from harness_factory.substrate.spill import tool_spec as spill_spec

DONE = "done"


RECOVERY_NOTE = ("Your last reply was rejected and not run: {error}\n"
                 "Use only these tools: {tools}. Continue the task.")


@dataclass
class Turn:
    """One model turn. `added` are the messages to append, in order: any recovery notes, then the reply (absent
    when the turn stopped). `calls` counts model calls made, a recovered turn makes more than one.
    `history` is set when the turn compacted the conversation (ADR 0015): it replaces the messages the turn was
    given, so shapes take the new conversation from `apply()` rather than appending `added` themselves."""
    added: list[dict[str, Any]] = field(default_factory=list)
    stop: str | None = None
    error: str | None = None
    calls: int = 0
    history: list[dict[str, Any]] | None = None

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The conversation after this turn."""
        return (self.history if self.history is not None else messages) + self.added

    @property
    def message(self) -> dict[str, Any] | None:
        return self.added[-1] if self.added and self.added[-1].get("role") == "assistant" else None


async def model_turn(inst: HarnessInstance, slot: ModelSlot, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]], response_format: dict[str, Any] | None = None) -> Turn:
    """One model call under the budget.

    With `config.recover_malformed`, a reply the provider rejects as malformed (MODEL_PARSE, e.g. a call to a tool
    that doesn't exist) is not the end: the model is told why, with the real tool names, and asked again. The
    rejected reply still costs a step, and the trajectory grader counts it as an invalid call, so recovering is
    never free: it can only beat stopping, not a clean run."""
    turn = Turn()
    tools = with_spill(inst, tools)
    window = window_for(inst.gateway.catalog, inst.gateway.provider.id, slot.canonical_model, slot.max_output_tokens)
    compacted = False
    while True:
        limit = inst.budget.exceeded_by()
        if limit:
            turn.stop = f"budget:{limit}"
            return turn
        prompt = turn.apply(messages)
        est = estimate_tokens(prompt, tools)               # ADR 0015: before every call, offline
        spec = inst.recipe.config.compaction
        if spec is not None and window is not None and not compacted and est > window.trigger(spec.at):
            compacted = True                               # at most one summary per turn
            history = turn.history if turn.history is not None else messages
            c = await compaction.compact(inst, slot, history, spec, window, est, tools)
            turn.calls += c.calls
            if c.stop:
                turn.stop, turn.error = c.stop, c.error
                return turn
            if c.history is not None:
                turn.history = c.history
                continue                                   # the summary used a step: check the budget again
        if window is not None and est > window.limit:      # a harness stop, before spending the call
            turn.stop = f"error:{ErrorKind.BUDGET.value}"
            turn.error = (f"context_overflow: the prompt is estimated at {est} tokens, over {window.limit} "
                          f"({window.context_len} context - {window.reply} reply)")
            return turn
        try:
            comp = await inst.gateway.complete(slot, prompt, tools, attributes={
                "est_tokens_in": est, **({"context_len": window.context_len} if window else {})},
                **({"response_format": response_format} if response_format else {}))
        except HarnessError as e:
            spent = getattr(e, "completion", None)        # a reply cut off mid tool call: a real call, never replayed
            if spent is not None:
                inst.budget.used_tokens += spent.tokens_in + spent.tokens_out
            if e.kind == ErrorKind.MODEL_PARSE and inst.recoveries < (inst.recipe.config.recover_malformed or 0):
                inst.recoveries += 1
                inst.budget.used_steps += 1
                turn.calls += 1
                names = ", ".join(t["function"]["name"] for t in tools) or "none"
                turn.added.append({"role": "user", "content": RECOVERY_NOTE.format(error=e.message[:300], tools=names)})
                continue
            if spent is not None:
                inst.budget.used_steps += 1
                turn.calls += 1
            turn.stop, turn.error = f"error:{e.kind.value}", e.message
            return turn
        inst.budget.used_steps += 1
        inst.budget.used_tokens += comp.tokens_in + comp.tokens_out
        turn.calls += 1
        turn.added.append(comp.message)
        return turn


def with_spill(inst: HarnessInstance, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the spill reader (ADR 0015) to a turn that offers tools, once this run has spilled an output. Per turn,
    not in offered_tools(): V2 and V4 compute their tool list once, and a spill happens mid-run."""
    spill = inst.tools.spill
    if not tools or spill is None or not spill.active or any(t["function"]["name"] == SPILL_NAME for t in tools):
        return tools
    return [*tools, spill_spec()]


def offered_tools(inst: HarnessInstance, restrict: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """The tools a model is offered: the allowed tools (optionally narrowed, e.g. to a V3 stage), plus the
    delegate tool when the recipe has a delegation and the limits allow one more level. Every shape uses this, so a
    recipe dimension that adds a harness-side tool reaches every shape the same way. The allowed tools include the
    run's LocalTools (retrieval, ADR 0017), which ToolExecutor offers and executes like MCP tools."""
    spec = delegate.tool_spec(inst, restrict)
    return inst.tools.tools_for_model(restrict) + ([spec] if spec else [])


async def run_tool_calls(inst: HarnessInstance, message: dict[str, Any],
                         restrict: Callable[[str], bool] | None = None,
                         scope: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Execute the tool calls of one assistant message, in order. Returns (tool messages, stop reason).
    With `config.parallel_tool_calls`, a batch of READ calls only runs concurrently (shapes/batch.py)."""
    if inst.recipe.config.parallel_tool_calls and batch.all_reads(inst, message, restrict):
        return await batch.run_reads(inst, message, restrict, scope)
    out: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        limit = inst.budget.exceeded_by(before="tool_call")
        if limit:
            return out, f"budget:{limit}"
        name, args = call["function"]["name"], call["function"]["arguments"]
        where = f"{inst.scope}/{scope}" if inst.scope and scope else (scope or inst.scope)
        stop = None
        if name == delegate.DELEGATE_NAME and inst.delegate is not None:
            text, stop = await delegate.run_delegate(inst, args, restrict=restrict, scope=where)
        else:
            text = (await inst.tools.execute(name, args, restrict=restrict, scope=where)).text
        out.append({"role": "tool", "tool_call_id": call["id"], "content": text})
        if stop:                    # a helper ended on infrastructure or a setup failure: so does this run
            return out, stop
    return out, None


# Harness-attributable early stops: the run is still graded on the environment, and the reason is recorded as a
# (non-critical) violation. Overspending already shows up in Q (wasted calls) and C (cost), so zeroing the
# outcome as well would double-count it, and would be unfair between shapes: a V3 stage cap ends a stage
# "normally" where a V2 step cap ends the run.
HARNESS_STOPS = {
    f"error:{ErrorKind.BUDGET.value}": "context_overflow",
    f"error:{ErrorKind.MODEL_PARSE.value}": "malformed_output",
}


def finish(inst: HarnessInstance, messages: list[dict[str, Any]], stop: str | None, output: Any,
           error_text: str | None = None) -> RunResult:
    """Map how the shape ended to a RunResult. Grading then sets SUCCESS / PARTIAL / FAIL for every scored run."""
    stop = stop or DONE
    if error_text is None:
        error_text = inst.stop_error
    result = RunResult(run_key=inst.run_key, messages=messages, output=output)
    result.violations = list(inst.tools.violations)
    if stop == DONE:
        result.finished = result.scored = True
        result.status = RunStatus.FAIL
    elif stop.startswith("budget:") or stop in HARNESS_STOPS:
        kind = "budget_exceeded" if stop.startswith("budget:") else HARNESS_STOPS[stop]
        result.scored = True
        result.status = RunStatus.BUDGET_EXCEEDED
        result.error = f"{stop}: {error_text}" if error_text else stop
        result.violations.append(Violation(kind=kind, detail=result.error[:300]))
    elif stop.removeprefix("error:") in {k.value for k in INFRA_KINDS}:
        result.status, result.error = RunStatus.INCOMPLETE, f"{stop}: {error_text}"     # infrastructure
    else:
        result.status, result.error = RunStatus.ERROR, f"{stop}: {error_text}"          # setup / factory
    result.ended_at = datetime.now(UTC)
    return result
