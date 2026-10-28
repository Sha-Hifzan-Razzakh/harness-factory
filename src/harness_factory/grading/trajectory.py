"""TrajectoryGrader (B8): quality of the path, Q = 1 - share of invalid and wasted tool calls.

invalid  a tool call that returned an error (bad arguments, refused, tool failure)
wasted   an exact repeat (same tool, same arguments) of an earlier successful call with no side-effecting call
         in between. Re-reading after a write is verification, not waste, so it is never counted; this keeps
         inspect -> act -> verify shapes on the same footing as a plain loop.
loop     the same call three or more times in a row with nothing in between -> violation (not critical)
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
        wasted = 0
        seen: set[tuple[str, str | None]] = set()          # successful calls since the last side effect
        streak: Counter = Counter()                        # consecutive identical calls
        last_key = None
        for s in calls:
            key = (s.name, s.attributes.get("args_hash"))
            streak[key] = streak[key] + 1 if key == last_key else 1
            last_key = key
            if streak[key] == LOOP_REPEATS:
                violations.append(Violation(kind="loop", detail=f"{s.name} x{LOOP_REPEATS}"))
            if s.status == "error":
                continue
            if key in seen:
                wasted += 1
            seen.add(key)
            if s.attributes.get("side_effect") in ("REVERSIBLE", "IRREVERSIBLE"):
                seen = set()                                # the world changed; earlier reads are stale
        if steps > ctx.max_steps:
            violations.append(Violation(kind="step_cap", detail=f"{steps} > {ctx.max_steps}"))
        q = max(0.0, 1.0 - (invalid + wasted) / len(calls))
        return Grade(grader_id=self.id, score=round(q, 6), passed=not violations, violations=violations,
                     detail=f"calls={len(calls)} invalid={invalid} wasted={wasted} steps={steps}")
