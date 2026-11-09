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
  rubric              an open-ended deliverable (a summary, a note, a report) that no fixed comparison can grade:
                      a judge model scores it against the check's rubric (grading/judge.py, ADR 0014). Pair it
                      with deterministic checks: an uncalibrated judge carries weight 0
  json_lines          a report that lists findings line by line (check id, status, processed value, correction):
                      each expected line must be answered and right, every expected correction surfaced, and few
                      corrections the task doesn't expect. Line accuracy, correction recall and the false
                      correction rate each have a threshold; the check passes only when all three are met
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from fnmatch import fnmatch

from harness_factory.env.mcp import deps
from harness_factory.env.mcp.impl import proc
from harness_factory.grading.base import CheckResult, Grade, GradeContext, register_grader
from harness_factory.grading.touches import grounded_on_data
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


def _strict_eq(a, b) -> bool:
    """JSON equality where booleans are not numbers: Python's True == 1 and False == 0 would let an expected
    1 match true (and "0" match false). Integers and floats still compare by value (JSON has one number type)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_strict_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_strict_eq(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return False
    return type(a) is type(b) and a == b if isinstance(a, str) or isinstance(b, str) else a == b


def _same(actual, expected: str) -> bool:
    if isinstance(actual, str) and actual == expected:
        return True
    try:
        return _strict_eq(json.loads(expected), actual)
    except ValueError:
        return False


def _office_paragraphs(path):
    """(style, text) of a Word document's body paragraphs, then of the paragraphs in its table cells."""
    import docx
    d = docx.Document(str(path))
    out = [(p.style.name if p.style is not None else "", p.text) for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                out += [(p.style.name if p.style is not None else "", p.text) for p in cell.paragraphs]
    return out


def _xlsx_value(path, sheet: str, cell: str):
    """The cell's value as a reader of the saved file sees it: a formula counts as its last saved result."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    if sheet not in wb.sheetnames:
        return _MISSING, f"no sheet {sheet!r} (sheets: {', '.join(wb.sheetnames)})"
    return wb[sheet][cell.upper()].value, ""


def _cell_matches(actual, c: WeightedCheck) -> bool:
    expected = c.expected
    if expected is None:
        return actual is None or actual == ""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)):
            return False                         # "89.00" typed as text is not the number 89
        return abs(actual - expected) <= (c.tolerance if c.tolerance is not None else 1e-9)
    return isinstance(actual, str) and actual == expected

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


def _pointer(doc, pointer: str):
    """RFC 6901: the value at a JSON pointer, or KeyError."""
    for raw in pointer.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, list):
            if not key.isdigit() or int(key) >= len(doc):
                raise KeyError(pointer)
            doc = doc[int(key)]
        elif isinstance(doc, dict) and key in doc:
            doc = doc[key]
        else:
            raise KeyError(pointer)
    return doc


def _field(line: dict, name: str) -> str:
    v = line.get(name)
    return "" if v is None else str(v).strip()


def _json_lines(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    spec = c.lines
    p = (ctx.workspace / (c.path or "")).resolve()
    if not p.is_file():
        return False, f"missing file {c.path}"
    try:
        lines = _pointer(json.loads(p.read_text(errors="replace")), spec.pointer)
    except ValueError as e:
        return False, f"invalid JSON: {e}"
    except KeyError:
        return False, f"no {spec.pointer} in {c.path}"
    if not isinstance(lines, list):
        return False, f"{spec.pointer} is not a list"
    got = [x for x in lines if isinstance(x, dict)]
    used: set[int] = set()
    accurate, found, matched_corrections = 0, 0, set()
    wanted = [e for e in spec.expected if e.status == "CORRECTED"]
    for e in spec.expected:
        # The first unused agent line with the same check id whose attribute matches answers this expected line.
        i = next((i for i, x in enumerate(got) if i not in used
                  and _field(x, "check_id").casefold() == e.check_id.casefold()
                  and (e.attribute is None or re.search(e.attribute, _field(x, "attribute"), re.IGNORECASE))), None)
        if i is None:
            continue
        used.add(i)
        x = got[i]
        status = _field(x, "status").upper()
        right = (status == e.status
                 and (e.processed is None or re.search(e.processed, _field(x, "processed"), re.IGNORECASE))
                 and (e.correction_type is None or _field(x, "correction_type").upper() == e.correction_type.upper()))
        accurate += bool(right)
        if e.status == "CORRECTED" and status == "CORRECTED" and (
                e.correction_type is None or _field(x, "correction_type").upper() == e.correction_type.upper()):
            found += 1
            matched_corrections.add(i)
    made = [i for i, x in enumerate(got) if _field(x, "status").upper() == "CORRECTED"]
    false = [i for i in made if i not in matched_corrections]
    acc = accurate / len(spec.expected)
    recall = found / len(wanted) if wanted else 1.0
    false_rate = len(false) / len(made) if made else 0.0
    ok = acc >= spec.min_accuracy and recall >= spec.min_correction_recall and false_rate <= spec.max_false_corrections
    return ok, (f"lines {accurate}/{len(spec.expected)} accurate ({acc:.2f} vs >= {spec.min_accuracy}); corrections "
                f"{found}/{len(wanted)} ({recall:.2f} vs >= {spec.min_correction_recall}); false corrections "
                f"{len(false)}/{len(made)} ({false_rate:.2f} vs <= {spec.max_false_corrections})")


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


def _rubric(ctx: GradeContext, c: WeightedCheck) -> tuple[bool, str]:
    v = ctx.judgements.get(c.id)
    if v is None:
        return False, "ungraded: no judge verdict (no judge configured, or graded without one)"
    if not v.available:
        return False, f"ungraded: {v.error}"
    if v.error:                                   # missing or empty deliverable: failed without a judge call
        return False, v.error
    trust = "calibrated" if v.calibrated else "uncalibrated: weight 0"
    rel = ">=" if v.passed else "<"
    return bool(v.passed), f"rubric {v.score:.3f} {rel} {c.rubric.threshold} (judge {v.judge_hash[:12]}, {trust})"


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
        if c.kind == "rubric":
            return _rubric(ctx, c)
        if c.kind == "json_lines":
            return _json_lines(ctx, c)
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
        if c.kind in ("xlsx_cell", "docx_contains", "docx_paragraph_regex"):
            return self._office(c, ctx)
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
        if c.kind == "tool_called" and c.touches is not None:
            return grounded_on_data(c.pattern or "", c.touches, ctx.spans, ctx.workspace)
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
        if c.kind in ("biz_equals", "biz_count"):
            return _biz_check(c, ctx.after.biz)
        if c.kind in ("state_equals", "state_count"):
            name, _, pointer = (c.path or "").partition(":")
            return _json_state_check(c.kind == "state_equals", pointer, c, ctx.after.json_state(name),
                                     f"no {name} state (the task seeds none, or it was removed)")
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
    def _office(self, c: WeightedCheck, ctx: GradeContext) -> tuple[bool, str]:
        """Office files are read in the factory process with the docs extra's libraries (ADR 0013); the
        provisioner refuses a task with these checks when they are missing, so a run is never scored without them."""
        deps.require(deps.CHECK_LIBRARIES[c.kind], f"check kind {c.kind!r}")
        p = (ctx.workspace / (c.path or "")).resolve()
        if not p.is_relative_to(ctx.workspace.resolve()) or not p.is_file():
            return False, f"missing file {c.path}"
        try:
            if c.kind == "xlsx_cell":
                actual, problem = _xlsx_value(p, c.sheet or "", c.cell or "")
            else:
                paragraphs, problem = _office_paragraphs(p), ""
        except Exception as e:  # noqa: BLE001  (a file the run broke is a failed check, not a crash)
            return False, f"cannot read {c.path}: {type(e).__name__}: {str(e)[:120]}"
        if problem:
            return False, problem
        if c.kind == "xlsx_cell":
            found = _cell_matches(actual, c)
            ok = (not found) if c.negate else found
            return ok, ("equal" if found else f"{c.sheet}!{c.cell} is {actual!r}") + (" (negated)" if c.negate else "")
        if c.kind == "docx_contains":
            found = (c.text or "") in "\n".join(text for _, text in paragraphs)
        else:
            rx = re.compile(c.pattern or "")
            found = any(rx.search(text) and (c.style is None or style == c.style) for style, text in paragraphs)
        ok = (not found) if c.negate else found
        return ok, ("found" if found else "not found") + (" (negated)" if c.negate else "")

    def grade(self, ctx: GradeContext) -> Grade:
        checks, violations = [], []
        for c in ctx.task.oracle.checks:
            ok, detail = self._check(c, ctx)
            checks.append(CheckResult(id=c.id, passed=ok, weight=c.weight, critical=c.critical, detail=detail))
            if c.critical and not ok:
                violations.append(Violation(kind="critical_check_failed", critical=True, detail=c.id))
        _weigh_rubric_checks(ctx, checks)
        score = round(sum(ch.weight for ch in checks if ch.passed), 6)
        return Grade(grader_id=self.id, score=score, passed=score >= ctx.task.oracle.pass_threshold and not violations,
                     checks=checks, violations=violations, detail=ctx.diff.summary())


def _weigh_rubric_checks(ctx: GradeContext, checks: list[CheckResult]) -> None:
    """A rubric check carries its weight only on a calibrated judge's available grade (ADR 0014). Otherwise it
    carries 0, and the other checks' weights are scaled up to sum to 1 again: the outcome is then exactly the
    deterministic checks' share, so it never depends on an uncalibrated (or absent) judge. A task without rubric
    checks is untouched (same weights, same arithmetic)."""
    rubric = {c.id: c for c in ctx.task.oracle.rubric_checks()}
    if not rubric:
        return
    dropped = 0.0
    for ch in checks:
        if ch.id not in rubric:
            continue
        v = ctx.judgements.get(ch.id)
        ch.judge = v.model_dump(mode="json") if v is not None else None
        ch.ungraded = v is None or not v.available
        if v is None or not v.counts:
            ch.declared_weight, ch.weight = ch.weight, 0.0
            dropped += ch.declared_weight
    if dropped <= 0:
        return
    scale = 1.0 / (1.0 - dropped)                  # the oracle keeps some deterministic weight (Oracle validator)
    for ch in checks:
        if ch.id not in rubric and ch.weight > 0:
            ch.declared_weight, ch.weight = ch.weight, round(ch.weight * scale, 9)


# ------------------------------------------------------------------ business state (the biz server)

def _biz_eq(a, b) -> bool:
    """JSON equality, strict on type: booleans are not numbers (True == 1 in Python) and strings are not numbers.
    Integers and floats compare by value (JSON has one number type)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_biz_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_biz_eq(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, (dict, list, str)) or isinstance(b, (dict, list, str)):
        return type(a) is type(b) and a == b
    return a == b


def _biz_expected(text: str):
    """The expected value: JSON when it parses, else the text itself as a string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _biz_check(c: WeightedCheck, doc) -> tuple[bool, str]:
    return _json_state_check(c.kind == "biz_equals", c.path or "", c, doc,
                             "no business state (the task seeds none, or it was removed)")


def _json_state_check(equals: bool, pointer: str, c: WeightedCheck, doc, absent: str) -> tuple[bool, str]:
    """biz_equals / state_equals (equals) and biz_count / state_count on one JSON state document, strict on type."""
    if doc is None:
        return False, absent
    actual = _pointer(doc, pointer)
    shown = "missing" if actual is _MISSING else json.dumps(actual, default=str)[:120]
    if equals:
        found = actual is not _MISSING and _biz_eq(actual, _biz_expected(c.text or ""))
        ok = (not found) if c.negate else found
        return ok, ("equal" if found else f"got {shown}") + (" (negated)" if c.negate else "")
    if actual is _MISSING or not isinstance(actual, (dict, list)):
        return False, f"{c.path} is not a collection (got {shown})"
    records = list(actual.values()) if isinstance(actual, dict) else actual
    where = json.loads(c.query) if c.query else {}
    n = sum(1 for r in records if isinstance(r, dict) and all(k in r and _biz_eq(r[k], v) for k, v in where.items()))
    want = int(c.text or "0")
    return n == want, f"{n} matching" + ("" if n == want else f", expected {want}")
