"""Checks that depend on other checks (#109, lab C1): `requires` passes a check only when the checks it names
pass too, so reading the right source earns nothing without the deliverable."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from harness_factory.env.state import EnvState, StateDiff
from harness_factory.grading.base import GradeContext
from harness_factory.grading.state import StateDiffGrader
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult
from harness_factory.tasks.model import Oracle, WeightedCheck


def _key() -> RunKey:
    return RunKey(task_version="t@1", task_hash="h", recipe_hash="r", canonical_model="m", provider="p")


def _check(cid: str, weight: float, **kw) -> WeightedCheck:
    return WeightedCheck(id=cid, weight=weight, **kw)


SCHEMA = dict(kind="json_valid", path="audit.json")
GROUNDED = dict(kind="output_regex", pattern="doc-002")


def _grade(factory, tmp_path, checks: list[WeightedCheck], output: str = "", files: dict[str, str] | None = None):
    task = factory.tasks.get("b3-01").model_copy(update={"oracle": Oracle(checks=checks)})
    ws, db = tmp_path / "ws", tmp_path / "db.sqlite"
    ws.mkdir(parents=True, exist_ok=True)
    for name, body in (files or {}).items():
        (ws / name).write_text(body)
    sqlite3.connect(db).close()
    ctx = GradeContext(task=task, result=RunResult(run_key=_key(), output=output), before=EnvState(), after=EnvState(),
                       diff=StateDiff(), workspace=ws, db_path=db, spans=[], max_steps=10)
    return StateDiffGrader().grade(ctx)


def test_reading_the_source_earns_nothing_without_the_deliverable(factory, tmp_path):
    checks = [_check("schema", 0.9, **SCHEMA), _check("grounded", 0.1, requires=["schema"], **GROUNDED)]
    g = _grade(factory, tmp_path, checks, output="I read doc-002.")                 # the read, no audit file
    by_id = {c.id: c for c in g.checks}
    assert g.score == 0 and not by_id["grounded"].passed
    assert "requires schema" in by_id["grounded"].detail
    g = _grade(factory, tmp_path / "2", checks, output="I read doc-002.", files={"audit.json": "{}"})
    assert g.score == 1.0 and all(c.passed for c in g.checks)
    g = _grade(factory, tmp_path / "3", checks, output="nothing read", files={"audit.json": "{}"})
    assert g.score == 0.9                               # requires never adds credit: the check must pass itself


def test_requirements_chain_and_results_keep_the_oracle_order(factory, tmp_path):
    checks = [_check("c", 0.2, requires=["b"], **GROUNDED), _check("b", 0.3, requires=["a"], **GROUNDED),
              _check("a", 0.5, **SCHEMA)]
    g = _grade(factory, tmp_path, checks, output="doc-002")                           # a fails, so b and c do
    assert [c.id for c in g.checks] == ["c", "b", "a"] and g.score == 0
    assert "requires b" in g.checks[0].detail and "requires a" in g.checks[1].detail


def test_a_critical_check_failing_a_requirement_is_a_violation(factory, tmp_path):
    checks = [_check("schema", 1.0, **SCHEMA), _check("guard", 0, critical=True, requires=["schema"], **GROUNDED)]
    g = _grade(factory, tmp_path, checks, output="doc-002")
    assert [v.detail for v in g.violations] == ["guard"] and not g.passed      # schema is not critical


def test_an_oracle_without_requires_grades_as_before(factory, tmp_path):
    checks = [_check("schema", 0.9, **SCHEMA), _check("grounded", 0.1, **GROUNDED)]
    g = _grade(factory, tmp_path, checks, output="doc-002")
    assert g.score == 0.1 and g.checks[1].passed and not g.checks[0].passed
    assert "requires" not in g.checks[1].detail


@pytest.mark.parametrize("checks, message", [
    ([_check("a", 1.0, requires=["a"], **SCHEMA)], "requires itself"),
    ([_check("a", 1.0, requires=["zz"], **SCHEMA)], "unknown check 'zz'"),
    ([_check("a", 0.5, requires=["b"], **SCHEMA), _check("b", 0.5, requires=["a"], **GROUNDED)], "cycle: a -> b -> a"),
    ([_check("a", 0.5, requires=["b"], **SCHEMA), _check("b", 0.25, requires=["c"], **GROUNDED),
      _check("c", 0.25, requires=["a"], **GROUNDED)], "cycle"),
    ([_check("a", 0.5, **SCHEMA), _check("a", 0.5, requires=["a"], **GROUNDED)], "unique"),
])
def test_requires_must_resolve(checks, message):
    with pytest.raises(ValidationError, match=message):
        Oracle(checks=checks)


def test_requires_never_names_a_rubric_check(factory):
    rubric_check = next((c for t in factory.tasks.all(include_held_out=True) for c in t.oracle.checks
                         if c.kind == "rubric"), None)
    if rubric_check is None:
        pytest.skip("no rubric check in the catalog to borrow")
    rubric = rubric_check.model_copy(update={"id": "r", "weight": 0.5})
    with pytest.raises(ValidationError, match="requires rubric check 'r'"):
        Oracle(checks=[rubric, _check("a", 0.5, requires=["r"], **SCHEMA)])


def test_requires_is_hashed_only_when_set(factory):
    task = factory.tasks.get("b3-01")
    assert task.model_copy(update={"oracle": Oracle(checks=list(task.oracle.checks))}).content_hash() \
        == task.content_hash()
    checks = [c.model_copy(update={"requires": ["answer"]}) if c.id == "grounded" else c for c in task.oracle.checks]
    assert task.model_copy(update={"oracle": Oracle(checks=checks)}).content_hash() != task.content_hash()


def test_an_empty_requires_list_is_refused():
    with pytest.raises(ValidationError):
        _check("a", 1.0, requires=[], **SCHEMA)
