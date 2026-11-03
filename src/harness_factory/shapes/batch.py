"""Parallel tool calls (`config.parallel_tool_calls`): the calls of one assistant message run concurrently, but only
when every one of them only reads.

A batch is concurrent only when each call is an allowed MCP tool, its arguments parse, and its side effect for these
arguments is READ (the declared one, or the escalated one when the tool declares an escalation). Anything else (a
write, a delegate call, an agent tool, which spends tokens the next call's budget check must see, a tool outside the
allowance or a call that doesn't parse) makes the whole batch run one call after another in the model's order,
exactly as without the flag. So the world a batch sees never depends on which call finished first.

What stays the same as the sequential loop:
  - budgets: the calls the sequential loop would have run are the calls started; a batch that would hit
    `max_tool_calls` part-way starts only the calls before the limit and ends with the same budget stop;
  - faults: each call passes its checks and asks the FaultInjector in the model's order before any call awaits (an
    asyncio task runs until its first await, and tasks start in the order they are created), so `nth` rules hit the
    same calls;
  - results go back in the model's order, whatever order they finished in;
  - a call that raises raises after its siblings finished (no orphaned calls), the first one in the model's order.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from harness_factory.env.mcp.servers import SideEffect
from harness_factory.shapes import delegate
from harness_factory.shapes.base import HarnessInstance
from harness_factory.substrate.tools import tool_ref


def _side_effect(spec, args: dict[str, Any]) -> SideEffect:
    """The call's side effect: the declared one, or the escalated one for these arguments when the catalog declares
    per-call escalation (`ToolSpec.effect_for`)."""
    effect_for = getattr(spec, "effect_for", None)
    return effect_for(args)[0] if effect_for is not None else spec.side_effect


def _reads(inst: HarnessInstance, call: dict[str, Any], restrict: Callable[[str], bool] | None) -> bool:
    name = call["function"]["name"]
    if name == delegate.DELEGATE_NAME:
        return False
    ref = tool_ref(name)
    if ref not in inst.tools.narrowed(restrict):
        return False
    try:
        raw = call["function"]["arguments"]
        args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        spec = inst.tools.declared.spec(ref)
    except (ValueError, TypeError, KeyError):
        return False
    if not isinstance(args, dict) or spec.agent is not None:
        return False
    return _side_effect(spec, args) == SideEffect.READ


def all_reads(inst: HarnessInstance, message: dict[str, Any], restrict: Callable[[str], bool] | None = None) -> bool:
    """True when the message's calls (two or more) may run concurrently: every one of them only reads."""
    calls = message.get("tool_calls") or []
    return len(calls) > 1 and all(_reads(inst, c, restrict) for c in calls)


async def run_reads(inst: HarnessInstance, message: dict[str, Any], restrict: Callable[[str], bool] | None = None,
                    scope: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Run a batch of READ calls concurrently. Same contract as `common.run_tool_calls`."""
    calls = message.get("tool_calls") or []
    where = f"{inst.scope}/{scope}" if inst.scope and scope else (scope or inst.scope)
    started, stop = [], None
    for i, call in enumerate(calls):
        # What the sequential loop checks before call i, with the i calls before it already charged.
        b = inst.budget
        if b.used_tool_calls + i >= b.limits.max_tool_calls:
            stop = f"budget:max_tool_calls {b.limits.max_tool_calls}"
        else:
            limit = b.exceeded_by(before="tool_call")
            stop = f"budget:{limit}" if limit else None
        if stop:
            break
        started.append(call)
    results = await asyncio.gather(
        *(inst.tools.execute(c["function"]["name"], c["function"]["arguments"], restrict=restrict, scope=where)
          for c in started), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    out = [{"role": "tool", "tool_call_id": c["id"], "content": r.text} for c, r in zip(started, results, strict=True)]
    return out, stop
