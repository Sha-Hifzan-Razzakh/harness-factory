"""TrajectoryGrader (B8): quality of the path, Q = 1 - share of invalid and wasted tool calls.

invalid  a tool call that returned an error
wasted   an exact repeat (same tool, same arguments) of an earlier call
loop     the same call three or more times -> violation (not critical)
"""

from __future__ import annotations

from collections import Counter

from harness_factory.grading.base import Grade, GradeContext
from harness_factory.observability.span import SpanKind
from harness_factory.shapes.result import Violation

LOOP_REPEATS = 3


class TrajectoryGrader:
    id = "trajectory"

    def grade(self, ctx: GradeContext) -> Grade:
        calls = [s for s in ctx.spans if s.kind == SpanKind.tool_call]
        steps = sum(1 for s in ctx.spans if s.kind == SpanKind.model_call)
        violations: list[Violation] = []
        if not calls:
            return Grade(grader_id=self.id, score=1.0, passed=True, detail=f"steps={steps}, no tool calls")
        invalid = sum(1 for s in calls if s.status == "error")
        keys = Counter((s.name, s.attributes.get("args_hash")) for s in calls if s.status != "error")
        wasted = sum(n - 1 for n in keys.values() if n > 1)
        for (name, _), n in keys.items():
            if n >= LOOP_REPEATS:
                violations.append(Violation(kind="loop", detail=f"{name} x{n}"))
        if steps > ctx.max_steps:
            violations.append(Violation(kind="step_cap", detail=f"{steps} > {ctx.max_steps}"))
        q = max(0.0, 1.0 - (invalid + wasted) / len(calls))
        return Grade(grader_id=self.id, score=round(q, 6), passed=not violations, violations=violations,
                     detail=f"calls={len(calls)} invalid={invalid} wasted={wasted} steps={steps}")
