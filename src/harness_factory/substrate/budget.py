"""BudgetGuard (B3): any limit hit ends the run as BUDGET_EXCEEDED."""

from __future__ import annotations

import time
from collections.abc import Callable

from harness_factory.observability.span import Span, SpanKind
from harness_factory.recipes.model import RuntimeConfig


class BudgetGuard:
    def __init__(self, limits: RuntimeConfig, excluded_s: Callable[[], float] = lambda: 0.0) -> None:
        """excluded_s: seconds not charged to the wall-time budget (quota waits, retry backoff)."""
        self.limits = limits
        self.excluded_s = excluded_s
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
            self.used_tokens += span.tokens_in + span.tokens_out      # an agent tool's own model calls (EXT-10)

    def exceeded_by(self, before: str = "model_call") -> str | None:
        """Which limit stops the next action, or None. `before` is "model_call" or "tool_call".

        max_steps counts model calls, so it gates only the next model call: the tool calls a step asked for
        still run. (Otherwise a step-capped run, e.g. V1 with max_steps 1, could never act on its last step.)
        """
        if before == "model_call" and self.used_steps >= self.limits.max_steps:
            return f"max_steps {self.limits.max_steps}"
        if self.used_tool_calls >= self.limits.max_tool_calls:
            return f"max_tool_calls {self.limits.max_tool_calls}"
        if self.used_tokens >= self.limits.max_tokens:
            return f"max_tokens {self.limits.max_tokens}"
        if self.elapsed_s() >= self.limits.wall_time_s:
            return f"wall_time_s {self.limits.wall_time_s}"
        return None

    def start(self) -> None:
        """Start the wall clock when the shape starts: provisioning (sandbox, MCP, Docker) is not the harness."""
        self.started_at = time.monotonic()

    def elapsed_s(self) -> float:
        return max(0.0, time.monotonic() - self.started_at - self.excluded_s())

    def remaining(self) -> dict[str, int]:
        """What is left for new work: the limits minus what was used and what running helpers hold (reserve)."""
        steps, tool_calls, tokens = self._reserved
        return {
            "steps": self.limits.max_steps - self.used_steps - steps,
            "tool_calls": self.limits.max_tool_calls - self.used_tool_calls - tool_calls,
            "tokens": self.limits.max_tokens - self.used_tokens - tokens,
        }

    # Budget held by helpers still running (steps, tool calls, tokens): a delegate's whole carved budget is held from
    # its start until its usage is charged back, so helpers running at the same time (V5 workers) are carved from
    # what the others don't hold and together can never exceed the run's limits. An immutable default, so the class
    # holds no shared mutable state.
    _reserved: tuple[int, int, int] = (0, 0, 0)

    def reserve(self, child: RuntimeConfig, extra_tool_calls: int = 0) -> None:
        s, t, k = self._reserved
        self._reserved = (s + child.max_steps, t + child.max_tool_calls + extra_tool_calls, k + child.max_tokens)

    def release(self, child: RuntimeConfig, extra_tool_calls: int = 0) -> None:
        s, t, k = self._reserved
        self._reserved = (s - child.max_steps, t - child.max_tool_calls - extra_tool_calls, k - child.max_tokens)
