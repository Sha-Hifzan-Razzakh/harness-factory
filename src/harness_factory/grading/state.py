"""StateDiffGrader (B8): scores the task oracle's weighted checks against the after-state.

Which comparison to use:
  sql_rows            exact, ordered rows: use when the query has an ORDER BY that fixes the order and the values
                      are integers or text (floats compare exactly, so 0.1 + 0.2 != 0.3)
  sql_rows_unordered  the same rows in any order, duplicates counted (a multiset): a query without ORDER BY, or
                      ties the task doesn't rank. Values still compare exactly
  number_close        one number (a SQL scalar, a number in a file, or the final answer) within abs_tol / rel_tol:
                      anything computed in floating point (sums, averages, percentages, rounding)
  output_exact        the final answer is exactly the expected text after trimming (and, if asked, collapsing
                      whitespace / ignoring case); output_regex when several phrasings are right
  json_schema         a file is JSON of the right shape (types, required keys, ranges), not one fixed document;
                      file_equals when there is exactly one right document
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from fnmatch import fnmatch

from harness_factory.grading.base import CheckResult, Grade, GradeContext, register_grader
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
      
def output_matches(pattern: str, output: str) -> bool:
    """How an output_regex check reads the final answer: re.search with MULTILINE, so ^ and $ match at every line.
    An answer pattern that must see the whole reply (no other candidate anywhere) anchors on \\A, not ^."""
    return re.search(pattern, output, re.MULTILINE) is not None

def _query(ctx: GradeContext, sql: str) -> list[list]:
    with sqlite3.connect(f"file:{ctx.db_path}?mode=ro", uri=True) as conn:
        return [list(r) for r in conn.execute(sql)]


def _output(ctx: GradeContext) -> str:
    return ctx.result.output if isinstance(ctx.result.output, str) else str(ctx.result.output or "")


_GROUPED = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")


def _parse_number(text: str) -> float | None:
    """A number as written: 1866.88, -3, 1e-3, or with thousands commas (1,866.88). None if it isn't one."""
    t = text.strip()
    if _GROUPED.fullmatch(t):
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def _rows_unordered(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    try:
        rows = _query(ctx, c.query or "")
    except sqlite3.Error as e:
        return False, f"query failed: {e}"
    got = Counter(tuple(r) for r in rows)
    want = Counter(tuple(r) for r in (c.rows or []))
    if got == want:
        return True, f"rows match ({len(rows)}, any order)"
    missing, extra = list((want - got).elements()), list((got - want).elements())
    return False, f"missing {[list(r) for r in missing[:5]]}, unexpected {[list(r) for r in extra[:5]]}"


def _number_close(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    if c.query is not None:
        try:
            rows = _query(ctx, c.query)
        except sqlite3.Error as e:
            return False, f"query failed: {e}"
        if len(rows) != 1 or len(rows[0]) != 1:
            return False, f"query must return one value, got {rows[:3]}"
        raw = rows[0][0]
        if raw is None:
            return False, "query returned NULL"
        actual = float(raw) if isinstance(raw, (int, float)) else _parse_number(str(raw))
        where = "query"
    else:
        if c.path is not None:
            p = (ctx.workspace / c.path).resolve()
            if not p.is_file():
                return False, f"missing file {c.path}"
            text, where = p.read_text(errors="replace"), c.path
        else:
            text, where = _output(ctx), "answer"
        if c.pattern is not None:
            m = re.search(c.pattern, text, re.MULTILINE)
            if m is None:
                return False, f"no number matching {c.pattern!r} in {where}"
            text = m.group(1) if m.re.groups else m.group(0)
        actual = _parse_number(text)
        if actual is None:
            return False, f"not a number in {where}: {text.strip()[:80]!r}"
    if actual is None or math.isnan(actual):
        return False, f"not a number ({where})"
    abs_tol = c.abs_tol or 0.0
    rel_tol = c.rel_tol if c.rel_tol is not None else (1e-9 if c.abs_tol is None else 0.0)
    ok = math.isclose(actual, c.value, rel_tol=rel_tol, abs_tol=abs_tol)
    return ok, f"{actual!r} {'~' if ok else 'is not within tolerance of'} {c.value!r} (abs {abs_tol}, rel {rel_tol})"


def _json_schema(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    from jsonschema import validators
    p = (ctx.workspace / (c.path or "")).resolve()
    if not p.is_file():
        return False, f"missing file {c.path}"
    try:
        doc = json.loads(p.read_text(errors="replace"))
    except ValueError as e:
        return False, f"invalid JSON: {e}"
    validator = validators.validator_for(c.json_schema)(c.json_schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if not errors:
        return True, "matches schema"
    shown = "; ".join(f"/{'/'.join(str(x) for x in e.absolute_path)}: {e.message}" for e in errors[:3])
    return False, f"{len(errors)} schema error(s): {shown[:300]}"


def normalize_answer(text: str, options: list[str] | None) -> str:
    t = text.replace("\r\n", "\n").strip()
    if "whitespace" in (options or []):
        t = " ".join(t.split())
    if "case" in (options or []):
        t = t.casefold()
    return t


def _output_exact(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    out = _output(ctx)
    ok = normalize_answer(out, c.normalize) == normalize_answer(c.text or "", c.normalize)
    return ok, "equal" if ok else f"output {out[:120]!r}"


@register_grader

class StateDiffGrader:
    id = "state_diff"
    version = "1"
    family = "outcome"
    combine = "product"

    def _check(self, c: WeightedCheck, ctx: GradeContext) -> tuple[bool, str]:
        if c.kind == "sql_rows_unordered":
            return _rows_unordered(ctx, c)
        if c.kind == "number_close":
            return _number_close(ctx, c)
        if c.kind == "json_schema":
            return _json_schema(ctx, c)
        if c.kind == "output_exact":
            return _output_exact(ctx, c)
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
            ok = output_matches(c.pattern or "", out)
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
        return False, f"unknown check kind {c.kind}"

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
