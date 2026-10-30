"""Pieces every shape shares: one model turn, one batch of tool calls, and how a stop reason becomes a status.

Keeping these in one place means shapes differ only in control flow, which is the thing being compared.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from harness_factory.recipes.model import ModelSlot
from harness_factory.shapes.base import HarnessInstance
from harness_factory.shapes.result import RunResult, RunStatus
from harness_factory.substrate.errors import ErrorKind, HarnessError

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


def finish(inst: HarnessInstance, messages: list[dict[str, Any]], stop: str | None, output: Any,
           error_text: str | None = None) -> RunResult:
    """Map how the shape ended to a RunResult. Grading later upgrades a finished run to SUCCESS or PARTIAL."""
    stop = stop or DONE
    result = RunResult(run_key=inst.run_key, messages=messages, output=output)
    if stop == DONE:
        result.finished = True
        result.status = RunStatus.FAIL
    elif stop.startswith("budget:"):
        result.status, result.error = RunStatus.BUDGET_EXCEEDED, stop
    elif stop == f"error:{ErrorKind.QUOTA_EXHAUSTED.value}":
        result.status, result.error = RunStatus.INCOMPLETE, stop
    else:
        result.status, result.error = RunStatus.ERROR, f"{stop}: {error_text}"
    result.violations = list(inst.tools.violations)
    result.ended_at = datetime.now(UTC)
    return result
