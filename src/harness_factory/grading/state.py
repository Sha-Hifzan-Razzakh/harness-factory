"""StateDiffGrader (B8): scores the task oracle's weighted checks against the after-state."""

from __future__ import annotations

import json
import re
import sqlite3
from fnmatch import fnmatch

from harness_factory.env.mcp.impl import proc
from harness_factory.grading.base import CheckResult, Grade, GradeContext
from harness_factory.observability.span import SpanKind
from harness_factory.shapes.result import Violation
from harness_factory.tasks.model import WeightedCheck


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").rstrip("\n")


_MISSING = object()


def _pointer(doc, pointer: str):
    """RFC 6901 JSON pointer lookup; _MISSING when the path isn't there."""
    if not pointer:
        return doc
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, dict) and key in doc:
            doc = doc[key]
        elif isinstance(doc, list) and key.isdigit() and int(key) < len(doc):
            doc = doc[int(key)]
        else:
            return _MISSING
    return doc


def _same(actual, expected: str) -> bool:
    if isinstance(actual, str) and actual == expected:
        return True
    try:
        return json.loads(expected) == actual
    except ValueError:
        return False


class StateDiffGrader:
    id = "state_diff"

    def _check(self, c: WeightedCheck, ctx: GradeContext) -> tuple[bool, str]:
        if c.kind in ("file_equals", "file_contains", "file_regex", "file_absent", "json_valid"):
            p = (ctx.workspace / (c.path or "")).resolve()
            if c.kind == "file_absent":
                return (not p.exists(), "absent" if not p.exists() else "file exists")
            if not p.is_file():
                return False, f"missing file {c.path}"
            body = p.read_text(errors="replace")
            if c.kind == "json_valid":
                try:
                    json.loads(body)
                except ValueError as e:
                    return False, f"invalid JSON: {e}"
                return True, "valid JSON"
            if c.kind == "file_regex":
                found = re.search(c.pattern or "", body, re.MULTILINE) is not None
                ok = (not found) if c.negate else found
                return ok, ("matched" if found else "no match") + (" (negated)" if c.negate else "")
            if c.kind == "file_equals":
                ok = _norm(body) == _norm(c.text or "")
                return ok, "equal" if ok else f"content differs: {body[:120]!r}"
            found = (c.text or "") in body
            ok = (not found) if c.negate else found
            return ok, ("found" if found else "not found") + (" (negated)" if c.negate else "")
        if c.kind == "sql_rows":
            try:
                with sqlite3.connect(f"file:{ctx.db_path}?mode=ro", uri=True) as conn:
                    rows = [list(r) for r in conn.execute(c.query or "")]
            except sqlite3.Error as e:
                return False, f"query failed: {e}"
            ok = rows == [list(r) for r in (c.rows or [])]
            return ok, "rows match" if ok else f"got {rows[:5]}"
        if c.kind == "output_regex":
            out = ctx.result.output if isinstance(ctx.result.output, str) else str(ctx.result.output or "")
            ok = re.search(c.pattern or "", out, re.MULTILINE) is not None
            return ok, "matched" if ok else f"output {out[:120]!r}"
        if c.kind == "tool_called":
            called = [s.name for s in ctx.spans if s.kind == SpanKind.tool_call and s.status != "error"]
            ok = any(fnmatch(n, c.pattern or "") for n in called)
            return ok, "grounded" if ok else f"no successful call matching {c.pattern}"
        if c.kind == "remote_equals":
            server, _, pointer = (c.path or "").partition(":")
            if server not in ctx.remote:
                return False, f"no test tenant state for {server!r}"
            actual = _pointer(ctx.remote[server], pointer)
            found = actual is not _MISSING and _same(actual, c.text or "")
            ok = (not found) if c.negate else found
            shown = "missing" if actual is _MISSING else json.dumps(actual, default=str)[:120]
            return ok, ("equal" if found else f"got {shown}") + (" (negated)" if c.negate else "")
        if c.kind == "tests_pass":
            return self._tests_pass(c, ctx)
        if c.kind == "git_log_contains":
            return self._git_log(c, ctx)
        return False, f"unknown check kind {c.kind}"

    @staticmethod
    def _tests_pass(c: WeightedCheck, ctx: GradeContext) -> tuple[bool, str]:
        """Run pytest on the end state, inside the sandbox (ADR 0012). Passes on exit code 0 only: failures,
        collection errors, no tests found (5) and the time limit all fail."""
        if ctx.sandbox is None:
            return False, "no sandbox to run the tests in"
        answer = ctx.sandbox.probe({"op": "pytest", "targets": [] if c.text else [c.path or "."], "k": c.pattern,
                                    "hidden": c.text, "timeout_s": proc.GRADE_TIMEOUT_S}, proc.GRADE_TIMEOUT_S)
        counts = ", ".join(f"{n} {k}" for k, n in (answer.get("counts") or {}).items() if n) or "no summary"
        if answer["timed_out"]:
            return False, f"tests timed out after {proc.GRADE_TIMEOUT_S:g} s"
        ok = answer["exit_code"] == 0
        return ok, f"exit {answer['exit_code']}: {counts}" + ("" if ok else f"; {answer['output'][-300:]!r}")

    @staticmethod
    def _git_log(c: WeightedCheck, ctx: GradeContext) -> tuple[bool, str]:
        """A commit on `ref` after the seed commit whose message matches `pattern` (and touches `path`)."""
        seed = getattr(ctx.sandbox, "git_seed", None)
        if ctx.sandbox is None or seed is None:
            return False, "no seeded git repository"
        ref = c.ref or "HEAD"
        commits = ctx.sandbox.probe({"op": "git_commits", "ref": ref, "since": seed}, proc.GIT_TIMEOUT_S)["commits"]
        if commits is None:
            found, shown = False, f"no ref {ref!r}"
        else:
            prefix = (c.path or "").rstrip("/")
            hits = [m for m in commits if re.search(c.pattern or "", m["message"], re.MULTILINE)
                    and (not prefix or any(p == prefix or p.startswith(prefix + "/") for p in m["paths"]))]
            found = bool(hits)
            shown = f"{len(commits)} new commit(s) on {ref}, {len(hits)} matching"
        ok = (not found) if c.negate else found
        return ok, shown + (" (negated)" if c.negate else "")

    def grade(self, ctx: GradeContext) -> Grade:
        checks, violations = [], []
        for c in ctx.task.oracle.checks:
            ok, detail = self._check(c, ctx)
            checks.append(CheckResult(id=c.id, passed=ok, weight=c.weight, critical=c.critical, detail=detail))
            if c.critical and not ok:
                violations.append(Violation(kind="critical_check_failed", critical=True, detail=c.id))
        score = round(sum(ch.weight for ch in checks if ch.passed), 6)
        return Grade(grader_id=self.id, score=score, passed=score >= ctx.task.oracle.pass_threshold and not violations,
                     checks=checks, violations=violations, detail=ctx.diff.summary())
