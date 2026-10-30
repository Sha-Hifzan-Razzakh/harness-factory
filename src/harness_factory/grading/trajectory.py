"""TrajectoryGrader (B8): quality of the path, Q = 1 - share of invalid and wasted tool calls.

invalid  a tool call that returned an error (bad arguments, refused, tool failure); injected faults and remote
         servers failing in transit excluded (the environment, not the model);
         and a reply the provider rejected as malformed (MODEL_PARSE), e.g. a call to a tool that doesn't exist:
         it is an attempted call that never ran, whether or not the harness then recovered
wasted   - a read repeated (same tool, same arguments) since the world last changed, within the same scope;
         - a write repeated identically: it has no new effect, so it never counts as a change.
         The world changes on a write the run has not made before. A new V3 stage is a new scope: its built-in
         re-check is the shape working as designed, and its extra cost is already charged through C.
loop     the same call three or more times in a row -> violation (not critical)
"""

from __future__ import annotations

from collections import Counter

from harness_factory.grading.base import Grade, GradeContext
from harness_factory.observability.span import SpanKind
from harness_factory.shapes.result import Violation
from harness_factory.substrate.errors import ErrorKind

LOOP_REPEATS = 3


class TrajectoryGrader:
    id = "trajectory"

    def grade(self, ctx: GradeContext) -> Grade:
        calls = [s for s in ctx.spans if s.kind == SpanKind.tool_call]
        steps = sum(1 for s in ctx.spans if s.kind == SpanKind.model_call and s.status != "error")
        rejected = sum(1 for s in ctx.spans if s.kind == SpanKind.model_call and s.error == ErrorKind.MODEL_PARSE.value)
        violations: list[Violation] = []
        if not calls and not rejected:
            return Grade(grader_id=self.id, score=1.0, passed=True, detail=f"steps={steps}, no tool calls")
        # An injected fault is the environment failing, not the model: never counted as invalid.
        invalid = rejected + sum(1 for s in calls if s.status == "error"
                                and "fault" not in s.attributes and "infra" not in s.attributes)
        wasted = 0
        reads: set[tuple[str, str | None]] = set()         # reads since the world last changed, this scope
        writes: set[tuple[str, str | None]] = set()        # every distinct write made so far
        streak: Counter = Counter()                        # consecutive identical calls
        last_key, scope = None, None
        for s in calls:
            key = (s.name, s.attributes.get("args_hash"))
            if s.attributes.get("scope") != scope:         # new V3 stage: reads and repeat streaks start over
                scope, reads, last_key = s.attributes.get("scope"), set(), None
            streak[key] = streak[key] + 1 if key == last_key else 1
            last_key = key
            if streak[key] == LOOP_REPEATS:
                violations.append(Violation(kind="loop", detail=f"{s.name} x{LOOP_REPEATS}"))
            if s.status == "error":
                continue
            if s.attributes.get("side_effect") in ("REVERSIBLE", "IRREVERSIBLE"):
                if key in writes:
                    wasted += 1                            # same write again: no new effect
                else:
                    writes.add(key)
                    reads = set()                          # the world changed; earlier reads are stale
                continue
            if key in reads:
                wasted += 1
            reads.add(key)
        if steps > ctx.max_steps:
            violations.append(Violation(kind="step_cap", detail=f"{steps} > {ctx.max_steps}"))
        q = max(0.0, 1.0 - (invalid + wasted) / (len(calls) + rejected))
        return Grade(grader_id=self.id, score=round(q, 6), passed=not violations, violations=violations,
                     detail=f"calls={len(calls)} rejected={rejected} invalid={invalid} wasted={wasted} steps={steps}")
