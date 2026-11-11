"""ToolExecutor (B3): every tool call goes through here.

Order per call: name resolves -> tool is allowed -> args parse and validate -> permission policy ->
MCP call with timeout (through the session pool) -> result shrunk if large. Every step is traced.

A LocalTool is a harness-side tool (it runs in the factory process, e.g. retrieval, ADR 0017). It takes the same
path, only its last step calls a Python function instead of an MCP server, so it can't escape the allowance, the
policy, faults, the span or the budget.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio

from harness_factory.env.faults import PASS_THROUGH, FaultInjector
from harness_factory.env.mcp.servers import USAGE_META, AgentTool, SideEffect, ToolCatalogFile, ToolSpec
from harness_factory.hashing import content_hash
from harness_factory.mcp.client import ToolResult
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.recipes.model import PolicySet
from harness_factory.shapes.result import Violation
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind
from harness_factory.substrate.spill import SPILL_REF, SpillStore

MAX_INLINE_CHARS = 16_000
PREVIEW_CHARS = 500          # span previews; the full text is in the run's transcript file
ARGS_CHARS = 2_000


def model_name(tool_ref: str) -> str:
    return tool_ref.replace(".", "__")


def tool_ref(model_tool_name: str) -> str:
    return model_tool_name.replace("__", ".", 1)


@dataclass
class LocalTool:
    """A tool the harness serves itself, in the factory process. Its spec (side effect, risk, timeout) is checked
    exactly like a declared MCP tool's; `call` gets the validated arguments and returns a ToolResult (an input the
    tool refuses is `is_error=True`; anything it raises is a factory crash, as for a local MCP server)."""
    spec: ToolSpec
    description: str
    input_schema: dict[str, Any]
    call: Callable[[dict[str, Any]], Awaitable[ToolResult]]


class ToolExecutor:
    def __init__(self, pool: MCPSessionPool, declared: ToolCatalogFile, allowed: set[str], policy: PolicySet,
                 budget: BudgetGuard, tracer: Tracer, run_key: str, faults: FaultInjector | None = None,
                 agent_calls: dict[str, int] | None = None, local: dict[str, LocalTool] | None = None,
                 spill: SpillStore | None = None) -> None:
        """agent_calls: calls made so far to each agent tool. Shared with delegated helpers, so an agent tool's
        max_calls is per run, not per helper. local: harness-side tools by ref; a ref still has to be in `allowed`
        to be offered or called. spill: where oversized outputs go (ADR 0015); None = cut them."""
        self.spill = spill
        self.faults = faults
        self.local = local or {}
        self.agent_calls = agent_calls if agent_calls is not None else {}
        self.pool = pool
        self.declared = declared
        self.allowed = allowed
        self.policy = policy
        self.budget = budget
        self.tracer = tracer
        self.run_key = run_key
        self.violations: list[Violation] = []
        self._counter = 0

    def narrowed(self, restrict: Callable[[str], bool] | None) -> set[str]:
        """Allowed tools, optionally narrowed (e.g. to one workflow stage). Never widens."""
        return {r for r in self.allowed if restrict is None or restrict(r)}

    def tools_for_model(self, restrict: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
        assert self.pool.catalog is not None
        out = []
        for ref in sorted(self.narrowed(restrict)):
            t = self.local.get(ref) or self.pool.catalog.tools.get(ref)
            if t is None:
                continue
            out.append({"type": "function", "function": {"name": model_name(ref), "description": t.description,
                                                         "parameters": t.input_schema}})
        return out

    def idempotency_key(self, ref: str, args: dict[str, Any]) -> str:
        self._counter += 1
        return content_hash([self.run_key, ref, args, self._counter])

    async def execute(self, name: str, raw_args: str | dict, restrict: Callable[[str], bool] | None = None,
                      scope: str | None = None) -> ToolResult:
        """restrict/scope narrow the allowance for this call (a V3 stage passes its own tool filter and id)."""
        ref = tool_ref(name)
        with self.tracer.span(SpanKind.tool_call, ref, **({"scope": scope} if scope else {})) as span:
            if ref == SPILL_REF and self.spill is not None and self.spill.active:     # harness-side (ADR 0015)
                result = self.spill.read(raw_args, span)
            elif ref in self.allowed and restrict is not None and not restrict(ref):
                span.error = ErrorKind.TOOL_NOT_ALLOWED.value
                self.violations.append(Violation(kind="tool_not_allowed", detail=f"{ref} (outside {scope})"))
                result = ToolResult(text=f"Tool {ref} is not available in this stage.", is_error=True)
            else:
                result = await self._execute(ref, raw_args, span)
            if result.is_error:
                span.status = "error"
                span.error = span.error or ErrorKind.TOOL_ERROR.value
            span.attributes["result_chars"] = len(result.text)
            span.attributes["result_preview"] = result.text[:PREVIEW_CHARS]
            self.tracer.tool_io(span, raw_args, result.text)       # OpenTelemetry only (Span.otel), larger cap
        self.budget.charge(span)
        return result

    async def _execute(self, ref: str, raw_args, span) -> ToolResult:
        if ref not in self.allowed:
            span.error = ErrorKind.TOOL_NOT_ALLOWED.value
            self.violations.append(Violation(kind="tool_not_allowed", detail=ref))
            return ToolResult(text=f"Tool {ref} is not available for this task.", is_error=True)
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (ValueError, TypeError) as e:
            span.error = ErrorKind.TOOL_ARGS.value
            return ToolResult(text=f"Invalid arguments for {ref}: {e}", is_error=True)
        local = self.local.get(ref)
        schema = local.input_schema if local else self.pool.catalog.tools[ref].input_schema if self.pool.catalog else {}
        missing = [k for k in schema.get("required", []) if k not in args]
        if missing:
            span.error = ErrorKind.TOOL_ARGS.value
            return ToolResult(text=f"Missing required arguments for {ref}: {', '.join(missing)}", is_error=True)
        span.attributes["args_hash"] = content_hash(args, 12)
        span.attributes["args"] = json.dumps(args, default=str)[:ARGS_CHARS]
        spec = local.spec if local else self.declared.spec(ref)
        side_effect, risk = spec.effect_for(args)          # a call can be stronger than the tool (env/mcp/effects.py)
        span.attributes["side_effect"] = side_effect.value
        if spec.observes:
            span.attributes["observes"] = True
        if side_effect != spec.side_effect:
            span.attributes.update(escalated=True, risk=risk)
        mode = self.policy.permission_mode.get(side_effect.value, "DENY")
        span.attributes["permission"] = mode
        if mode != "AUTO":
            # ASK routes to the ApprovalGate from P3; until then it is treated as a denial.
            span.error = ErrorKind.POLICY.value
            self.violations.append(Violation(kind="policy_denied", detail=f"{ref} ({side_effect.value})"))
            what = ref if side_effect == spec.side_effect else f"this {ref} call"
            return ToolResult(text=f"Permission denied: {what} is {side_effect.value} and needs approval.",
                              is_error=True)
        if spec.agent is not None and self.agent_calls.get(ref, 0) >= spec.agent.max_calls:
            span.error = ErrorKind.TOOL_ERROR.value          # the harness's choice: counts as invalid in Q
            return ToolResult(text=f"{ref} limit reached: at most {spec.agent.max_calls} calls per run.",
                              is_error=True)
        if side_effect != SideEffect.READ:
            span.attributes["idempotency_key"] = self.idempotency_key(ref, args)
        # Faults fire only on calls that passed every check above, so a refused call is never a "fault".
        injected = self.faults.fire(ref, reads=side_effect == SideEffect.READ) if self.faults else None
        fault = injected.kind if injected else None
        if injected is not None:
            span.attributes["fault"] = fault                 # environment, not the model: excluded from Q
            if injected.delay_s is not None:
                span.attributes["fault_delay_s"] = injected.delay_s
        if fault in ("error", "timeout", "rate_limit"):
            span.error = ErrorKind.TOOL_ERROR.value
            return ToolResult(text=FaultInjector.message(fault, ref), is_error=True)
        if fault == "empty":
            return ToolResult(text="")
        if spec.agent is not None:
            self.agent_calls[ref] = self.agent_calls.get(ref, 0) + 1
            span.attributes["agent_call"] = self.agent_calls[ref]
        try:
            with anyio.fail_after(spec.timeout_s):
                if injected is not None and injected.delay_s:
                    # A slow service: the run's wall clock keeps running (this is not infra wait, so it is never
                    # excluded from wall_time_s), and a delay that reaches timeout_s is a real timeout.
                    await asyncio.sleep(injected.delay_s)
                result = await (local.call(args) if local else self.pool.call(ref, args))
        except TimeoutError:
            span.error = ErrorKind.TOOL_TIMEOUT.value
            return ToolResult(text=f"{ref} timed out after {spec.timeout_s}s", is_error=True)
        except Exception as e:
            if local or not self.declared.server(ref.split(".")[0]).remote:
                raise
            span.error = ErrorKind.TOOL_ERROR.value          # a remote server failing in transit: environment,
            span.attributes["infra"] = type(e).__name__      # not the model, so excluded from Q like a fault
            return ToolResult(text=f"{ref} is unavailable right now ({type(e).__name__}).", is_error=True)
        if result.infra:                                     # e.g. a replay with no recording of this call:
            span.error = ErrorKind.TOOL_ERROR.value          # the environment, excluded from Q
            span.attributes["infra"] = result.infra
        elif spec.agent is not None:
            result = self._meter(ref, spec.agent, result, span)
        if fault == "garbled":                               # it really ran; the answer came back cut off
            result = result.model_copy(update={"text": FaultInjector.garble(result.text), "structured": None})
        elif fault in PASS_THROUGH:
            reshaped = None
            if fault == "malformed" and not (result.is_error or result.infra):
                reshaped = self.faults.malform(injected, result.text)
            if reshaped is not None:                         # it ran and succeeded; the answer came back misshapen
                result = result.model_copy(update={"text": reshaped[0], "structured": None})
                span.attributes["malformed"] = reshaped[1]
            elif result.is_error or result.infra or fault == "malformed":
                # The call's own outcome stands (e.g. its own error for the model's arguments, or nothing to
                # reshape): recorded for analysis, but not as the environment's doing, so Q counts the error.
                span.attributes["fault_inert"] = span.attributes.pop("fault")
        if self.spill is not None:
            return result.model_copy(update={"text": self.spill.shrink(result.text, span)})
        if len(result.text) > MAX_INLINE_CHARS:
            span.attributes["truncated_from"] = len(result.text)
            result = result.model_copy(update={"text": result.text[:MAX_INLINE_CHARS] + "\n[truncated]"})
        return result

    @staticmethod
    def _meter(ref: str, agent: AgentTool, result: ToolResult, span) -> ToolResult:
        """An agent tool's usage goes on its span: the tokens count toward the run's token budget and cost
        (BudgetGuard.charge, CostLedger.add), the usd toward C. An answer without usage would be free work, so it is
        withheld and marked as the environment's failure; an error without usage (a refusal before any work, e.g. a
        ToolError) charges nothing."""
        usage = (result.meta or {}).get(USAGE_META)
        try:
            tin, tout = int(usage["tokens_in"]), int(usage["tokens_out"])
            if tin < 0 or tout < 0:
                raise ValueError("negative token count")
            usd = float(usage["usd"]) if usage.get("usd") is not None else agent.usd(tin, tout)
        except (TypeError, KeyError, ValueError):
            if result.is_error:
                return result
            span.error = ErrorKind.TOOL_ERROR.value
            span.attributes["infra"] = "agent_usage_missing"
            return ToolResult(text=f"{ref} answered without reporting its usage, so its answer can't be used.",
                              is_error=True, infra="agent_usage_missing")
        span.tokens_in, span.tokens_out = tin, tout
        span.attributes.update(agent=True, usd=round(usd, 6), agent_model=str(usage.get("model") or "unknown"))
        return result
