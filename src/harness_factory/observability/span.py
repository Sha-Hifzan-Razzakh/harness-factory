"""Span and SpanKind (B8): one schema across every shape."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SpanKind(str, Enum):
    run = "run"
    model_call = "model_call"
    tool_call = "tool_call"
    retrieval = "retrieval"
    plan = "plan"
    replan = "replan"
    verify = "verify"
    approval = "approval"
    handoff = "handoff"
    skill_load = "skill_load"
    compaction = "compaction"
    memory_read = "memory_read"
    memory_write = "memory_write"
    mcp_request = "mcp_request"
    grade = "grade"


class Span(BaseModel):
    trace_id: str
    span_id: str
    parent_id: str | None = None
    kind: SpanKind
    name: str
    run_key: str
    start_ns: int
    end_ns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "ok"            # ok | error
    error: str | None = None      # ErrorKind value
    attributes: dict[str, Any] = Field(default_factory=dict)

    def duration_ms(self) -> float:
        return max(0, self.end_ns - self.start_ns) / 1e6
