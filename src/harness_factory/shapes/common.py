"""Pieces every shape shares: one model turn, one batch of tool calls, and how a stop reason becomes a status.

Keeping these in one place means shapes differ only in control flow, which is the thing being compared.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from harness_factory.recipes.model import ModelSlot
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult, RunStatus, Violation
from harness_factory.substrate.errors import INFRA_KINDS, ErrorKind, HarnessError

DONE = "done"


async def model_turn(inst: HarnessInstance, slot: ModelSlot, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """One model call under the budget. Returns (assistant message, stop reason, error text)."""
    limit = inst.budget.exceeded_by()
    if limit:
        return None, f"budget:{limit}", None
    try:
        comp = await inst.gateway.complete(slot, messages, tools)
    except HarnessError as e:
        return None, f"error:{e.kind.value}", e.message
    inst.budget.used_steps += 1
    inst.budget.used_tokens += comp.tokens_in + comp.tokens_out
    return comp.message, None, None


async def run_tool_calls(inst: HarnessInstance, message: dict[str, Any],
                         restrict: Callable[[str], bool] | None = None,
                         scope: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Execute the tool calls of one assistant message, in order. Returns (tool messages, stop reason)."""
    out: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        limit = inst.budget.exceeded_by(before="tool_call")
        if limit:
            return out, f"budget:{limit}"
        result = await inst.tools.execute(call["function"]["name"], call["function"]["arguments"],
                                          restrict=restrict, scope=scope)
        out.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
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
