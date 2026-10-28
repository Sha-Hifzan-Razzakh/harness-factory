"""BudgetGuard (B3): any limit hit ends the run as BUDGET_EXCEEDED."""

from __future__ import annotations

import time

from harness_factory.observability.span import Span, SpanKind
from harness_factory.recipes.model import RuntimeConfig


class BudgetGuard:
    def __init__(self, limits: RuntimeConfig) -> None:
        self.limits = limits
        self.used_tokens = 0
        self.used_tool_calls = 0
        self.used_steps = 0
        self.started_at = time.monotonic()

    def charge(self, span: Span) -> None:
        if span.kind == SpanKind.model_call:
            self.used_steps += 1
            self.used_tokens += span.tokens_in + span.tokens_out
        elif span.kind == SpanKind.tool_call:
            self.used_tool_calls += 1

    def exceeded_by(self) -> str | None:
        if self.used_steps >= self.limits.max_steps:
            return f"max_steps {self.limits.max_steps}"
        if self.used_tool_calls >= self.limits.max_tool_calls:
            return f"max_tool_calls {self.limits.max_tool_calls}"
        if self.used_tokens >= self.limits.max_tokens:
            return f"max_tokens {self.limits.max_tokens}"
        if time.monotonic() - self.started_at >= self.limits.wall_time_s:
            return f"wall_time_s {self.limits.wall_time_s}"
        return None

    def remaining(self) -> dict[str, int]:
        return {
            "steps": self.limits.max_steps - self.used_steps,
            "tool_calls": self.limits.max_tool_calls - self.used_tool_calls,
            "tokens": self.limits.max_tokens - self.used_tokens,
        }
