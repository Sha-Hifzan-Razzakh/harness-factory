"""ToolExecutor (B3): every tool call goes through here.

Order per call: name resolves -> tool is allowed -> args parse and validate -> permission policy ->
MCP call with timeout (through the session pool) -> result shrunk if large. Every step is traced.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anyio

from harness_factory.env.mcp.servers import SideEffect, ToolCatalogFile
from harness_factory.hashing import content_hash
from harness_factory.mcp.client import ToolResult
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.recipes.model import PolicySet
from harness_factory.shapes.result import Violation
from harness_factory.substrate.budget import BudgetGuard
from harness_factory.substrate.errors import ErrorKind

MAX_INLINE_CHARS = 16_000
PREVIEW_CHARS = 500          # span previews; the full text is in the run's transcript file
ARGS_CHARS = 2_000


def model_name(tool_ref: str) -> str:
    return tool_ref.replace(".", "__")


def tool_ref(model_tool_name: str) -> str:
    return model_tool_name.replace("__", ".", 1)


class ToolExecutor:
    def __init__(self, pool: MCPSessionPool, declared: ToolCatalogFile, allowed: set[str], policy: PolicySet,
                 budget: BudgetGuard, tracer: Tracer, run_key: str) -> None:
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
            t = self.pool.catalog.tools.get(ref)
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
            if ref in self.allowed and restrict is not None and not restrict(ref):
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
        schema = self.pool.catalog.tools[ref].input_schema if self.pool.catalog else {}
        missing = [k for k in schema.get("required", []) if k not in args]
        if missing:
            span.error = ErrorKind.TOOL_ARGS.value
            return ToolResult(text=f"Missing required arguments for {ref}: {', '.join(missing)}", is_error=True)
        span.attributes["args_hash"] = content_hash(args, 12)
        span.attributes["args"] = json.dumps(args, default=str)[:ARGS_CHARS]
        spec = self.declared.spec(ref)
        span.attributes["side_effect"] = spec.side_effect.value
        mode = self.policy.permission_mode.get(spec.side_effect.value, "DENY")
        span.attributes["permission"] = mode
        if mode != "AUTO":
            # ASK routes to the ApprovalGate from P3; until then it is treated as a denial.
            span.error = ErrorKind.POLICY.value
            self.violations.append(Violation(kind="policy_denied", detail=f"{ref} ({spec.side_effect.value})"))
            return ToolResult(text=f"Permission denied: {ref} is {spec.side_effect.value} and needs approval.",
                              is_error=True)
        if spec.side_effect != SideEffect.READ:
            span.attributes["idempotency_key"] = self.idempotency_key(ref, args)
        try:
            with anyio.fail_after(spec.timeout_s):
                result = await self.pool.call(ref, args)
        except TimeoutError:
            span.error = ErrorKind.TOOL_TIMEOUT.value
            return ToolResult(text=f"{ref} timed out after {spec.timeout_s}s", is_error=True)
        if len(result.text) > MAX_INLINE_CHARS:
            span.attributes["truncated_from"] = len(result.text)
            result = result.model_copy(update={"text": result.text[:MAX_INLINE_CHARS] + "\n[truncated]"})
        return result
