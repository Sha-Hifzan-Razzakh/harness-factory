"""CostLedger (B7): cost of one run. Free calls are still priced at a reference rate."""

from __future__ import annotations

from pydantic import BaseModel

from harness_factory.observability.span import Span, SpanKind


class CostLedger(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    wall_s: float = 0.0
    usd: float = 0.0
    price_basis: str = "provider list price"

    def add(self, span: Span, usd: float = 0.0) -> None:
        if span.kind == SpanKind.model_call:
            self.model_calls += 1
            self.tokens_in += span.tokens_in
            self.tokens_out += span.tokens_out
            self.usd += usd
        elif span.kind == SpanKind.tool_call:
            self.tool_calls += 1
