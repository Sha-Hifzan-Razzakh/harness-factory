"""Graders depth (#48): the new check kinds, grader versions, end states and re-grading, pluggable graders, and
proof that every existing task scores exactly as before graders became pluggable."""

from __future__ import annotations

import itertools
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness_factory import settings
from harness_factory.env.mcp.impl.proc import GIT_SEED
from harness_factory.env.sandbox import make_sandbox
from harness_factory.env.state import EnvState, StateDiff, StateInspector
from harness_factory.grading.base import (
    Grade,
    GradeContext,
    GradingPipeline,
    combine_families,
    default_pipeline,
    register_grader,
    unregister_grader,
)
from harness_factory.grading.state import StateDiffGrader
from harness_factory.hashing import content_hash
from harness_factory.results.model import FamilyScores, ScoreWeights
from harness_factory.results.store import ResultsStore
from harness_factory.runner.endstate import load_endstate
from harness_factory.runner.regrade import regrade
from harness_factory.runner.runner import (
    OVERRUN_OUTCOME_FACTOR,
    Factory,
    _finalize_status,
    overran,
    run_one,
    run_scores,
)
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult, RunStatus, Violation
from harness_factory.tasks.model import _CHECK_HASH_OMIT_WHEN_NONE, _SEED_OMIT_WHEN_NONE, WeightedCheck


@pytest.fixture(autouse=True)
def own_traces_dir(tmp_path, monkeypatch):
    """Traces, transcripts and end states of these tests stay under tmp_path."""
    d = tmp_path / "traces"
    d.mkdir()
    monkeypatch.setattr(settings, "TRACES_DIR", d)
    return d


def _key() -> RunKey:
    return RunKey(task_version="t@1", task_hash="h", recipe_hash="r", canonical_model="m", provider="p")


def make_ctx(tmp_path: Path, output=None, files: dict[str, str] | None = None, sql: str = "") -> GradeContext:
    ws, db = tmp_path / "ws", tmp_path / "db.sqlite"
    ws.mkdir(parents=True, exist_ok=True)
    for name, body in (files or {}).items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(body)
    with sqlite3.connect(db) as c:
        c.executescript(sql)
    return GradeContext(task=None, result=RunResult(run_key=_key(), output=output), before=EnvState(),
                        after=EnvState(), diff=StateDiff(), workspace=ws, db_path=db, spans=[], max_steps=10)


def check(**kw) -> WeightedCheck:
    return WeightedCheck(id="c", weight=1, **kw)


def passes(c: WeightedCheck, ctx: GradeContext) -> bool:
    ok, detail = StateDiffGrader()._check(c, ctx)
    assert detail
    return ok


DB = ("CREATE TABLE t (id INTEGER, name TEXT, amount REAL);"
      "INSERT INTO t VALUES (1, 'a', 0.1), (2, 'b', 0.2), (3, 'b', 0.2);")


# ---- sql_rows_unordered ---------------------------------------------------------------------------------------

def test_rows_unordered_any_order(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    assert passes(check(kind="sql_rows_unordered", query="SELECT name FROM t", rows=[["b"], ["a"], ["b"]]), ctx)
    # the ordered kind keeps its semantics: same rows in another order fail
    assert not passes(check(kind="sql_rows", query="SELECT name FROM t", rows=[["b"], ["a"], ["b"]]), ctx)


def test_rows_unordered_counts_duplicates(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    assert not passes(check(kind="sql_rows_unordered", query="SELECT name FROM t", rows=[["a"], ["b"]]), ctx)
    assert not passes(check(kind="sql_rows_unordered", query="SELECT name FROM t",
                            rows=[["a"], ["a"], ["b"]]), ctx)
    ok, detail = StateDiffGrader()._check(check(kind="sql_rows_unordered", query="SELECT name FROM t",
                                                rows=[["a"], ["b"], ["c"]]), ctx)
    assert not ok and "['c']" in detail and "['b']" in detail       # what is missing and what is extra


def test_rows_unordered_empty(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    assert passes(check(kind="sql_rows_unordered", query="SELECT * FROM t WHERE id > 9", rows=[]), ctx)
    assert not passes(check(kind="sql_rows_unordered", query="SELECT id FROM t", rows=[]), ctx)
    assert not passes(check(kind="sql_rows_unordered", query="SELECT id FROM t WHERE id > 9", rows=[[1]]), ctx)


def test_rows_unordered_bad_query_and_values(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    assert not passes(check(kind="sql_rows_unordered", query="SELECT nope FROM t", rows=[]), ctx)
    with pytest.raises(ValidationError, match="SQL values"):
        check(kind="sql_rows_unordered", query="SELECT 1", rows=[[float("nan")]])
    with pytest.raises(ValidationError, match="SQL values"):
        check(kind="sql_rows_unordered", query="SELECT 1", rows=[[[1, 2]]])
    with pytest.raises(ValidationError, match="needs rows"):
        check(kind="sql_rows_unordered", query="SELECT 1")


# ---- number_close ---------------------------------------------------------------------------------------------

def test_number_close_sql_scalar(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    total = "SELECT 0.1 + 0.2"                                        # 0.30000000000000004 in floating point
    assert not passes(check(kind="sql_rows", query=total, rows=[[0.3]]), ctx)    # why number_close exists
    assert passes(check(kind="number_close", query=total, value=0.3), ctx)       # default rel_tol 1e-9
    assert passes(check(kind="number_close", query=total, value=0.31, abs_tol=0.01), ctx)
    assert not passes(check(kind="number_close", query=total, value=0.32, abs_tol=0.01), ctx)
    assert passes(check(kind="number_close", query=total, value=0.32, rel_tol=0.1), ctx)
    assert passes(check(kind="number_close", query="SELECT SUM(amount) FROM t", value=0.5), ctx)
    assert passes(check(kind="number_close", query="SELECT '1,866.88'", value=1866.88), ctx)


def test_number_close_sql_edges(tmp_path):
    ctx = make_ctx(tmp_path, sql=DB)
    for q in ("SELECT amount FROM t WHERE id > 9",                    # no rows
              "SELECT id, amount FROM t WHERE id = 1",                # two columns
              "SELECT amount FROM t",                                 # several rows
              "SELECT NULL",                                          # NULL
              "SELECT 'n/a'",                                         # text, not a number
              "SELECT nope FROM t"):                                  # query error
        assert not passes(check(kind="number_close", query=q, value=0.1, abs_tol=1), ctx), q


def test_number_close_file_and_answer(tmp_path):
    ctx = make_ctx(tmp_path, output="Net revenue was 1,866.88 AED.",
                   files={"out/total.txt": "total: 42.004\n", "nan.txt": "nan", "inf.txt": "inf", "x.txt": "x"})
    assert passes(check(kind="number_close", path="out/total.txt", pattern=r"total:\s*(\S+)", value=42,
                        abs_tol=0.01), ctx)
    assert not passes(check(kind="number_close", path="out/total.txt", pattern=r"total:\s*(\S+)", value=42), ctx)
    assert not passes(check(kind="number_close", path="out/total.txt", value=42, abs_tol=1), ctx)  # not bare
    assert not passes(check(kind="number_close", path="nan.txt", value=0, abs_tol=1e9), ctx)       # NaN
    assert not passes(check(kind="number_close", path="inf.txt", value=0, abs_tol=1e9), ctx)
    assert not passes(check(kind="number_close", path="x.txt", value=0, abs_tol=1), ctx)
    assert not passes(check(kind="number_close", path="missing.txt", value=0, abs_tol=1), ctx)
    assert not passes(check(kind="number_close", path="x.txt", pattern=r"\d+", value=0, abs_tol=1), ctx)
    assert passes(check(kind="number_close", pattern=r"([\d,.]+) AED", value=1866.88), ctx)
    # no pattern: the whole answer must be the number, so a sentence around it fails
    assert not passes(check(kind="number_close", value=1866.88), ctx)
    assert passes(check(kind="number_close", value=1866.88, abs_tol=0.005),
                  make_ctx(tmp_path / "b", output=" 1866.88\n"))
    assert not passes(check(kind="number_close", value=1, abs_tol=1), make_ctx(tmp_path / "c", output=None))


def test_number_close_validation():
    with pytest.raises(ValidationError, match="needs value"):
        check(kind="number_close", query="SELECT 1")
    with pytest.raises(ValidationError, match="not both"):
        check(kind="number_close", query="SELECT 1", path="a.txt", value=1)
    with pytest.raises(ValidationError, match="finite"):
        check(kind="number_close", query="SELECT 1", value=float("nan"))
    with pytest.raises(ValidationError, match="abs_tol"):
        check(kind="number_close", query="SELECT 1", value=1, abs_tol=-1)
    with pytest.raises(ValidationError, match="rel_tol"):
        check(kind="number_close", query="SELECT 1", value=1, rel_tol=float("inf"))
    with pytest.raises(ValidationError, match="bad pattern"):
        check(kind="number_close", pattern="(", value=1)


# ---- json_schema ----------------------------------------------------------------------------------------------

SCHEMA = {"type": "object", "required": ["rows"],
          "properties": {"rows": {"type": "array", "minItems": 1,
                                  "items": {"type": "object", "required": ["id", "amount"],
                                            "properties": {"id": {"type": "integer"},
                                                           "amount": {"type": "number", "minimum": 0}}}}}}


def test_json_schema(tmp_path):
    ctx = make_ctx(tmp_path, files={"ok.json": json.dumps({"rows": [{"id": 1, "amount": 2.5}]}),
                                    "bad.json": json.dumps({"rows": [{"id": "1", "amount": -1}]}),
                                    "empty.json": json.dumps({"rows": []}),
                                    "broken.json": "{\"rows\": [", "list.json": "[]"})
    assert passes(check(kind="json_schema", path="ok.json", json_schema=SCHEMA), ctx)
    ok, detail = StateDiffGrader()._check(check(kind="json_schema", path="bad.json", json_schema=SCHEMA), ctx)
    assert not ok and "2 schema error(s)" in detail and "/rows/0/id" in detail
    for name in ("empty.json", "broken.json", "list.json", "missing.json"):
        assert not passes(check(kind="json_schema", path=name, json_schema=SCHEMA), ctx), name


def test_json_schema_refuses_a_bad_schema():
    with pytest.raises(ValidationError, match="bad JSON schema"):
        check(kind="json_schema", path="a.json", json_schema={"type": "no-such-type"})
    with pytest.raises(ValidationError, match="needs json_schema"):
        check(kind="json_schema", path="a.json")


# ---- output_exact ---------------------------------------------------------------------------------------------

def test_output_exact(tmp_path):
    ctx = make_ctx(tmp_path, output="  Marina   Fitness\n")
    assert not passes(check(kind="output_exact", text="Marina Fitness"), ctx)
    assert passes(check(kind="output_exact", text="Marina Fitness", normalize=["whitespace"]), ctx)
    assert not passes(check(kind="output_exact", text="marina fitness", normalize=["whitespace"]), ctx)
    assert passes(check(kind="output_exact", text="marina fitness", normalize=["whitespace", "case"]), ctx)
    trimmed = make_ctx(tmp_path / "b", output="\n7\r\n")
    assert passes(check(kind="output_exact", text="7"), trimmed)                   # ends are always trimmed
    assert not passes(check(kind="output_exact", text="7"), make_ctx(tmp_path / "c", output="About 7"))
    assert not passes(check(kind="output_exact", text="7"), make_ctx(tmp_path / "d", output=None))
    with pytest.raises(ValidationError):
        check(kind="output_exact", text="7", normalize=["unicode"])


# ---- identity -------------------------------------------------------------------------------------------------

USED_BY_NEW_TASKS = {"rubric",     # fields set by tasks written after them (docs-03, #49;
                     "ref", "sheet", "cell", "expected", "tolerance", "style",   # code-*, office-*, #45-#47;
                     "touches"}                 # the answer-only tasks' grounding, new task versions, #69)


def test_new_check_fields_leave_task_hashes_alone(factory):
    """Unset new fields are dropped before hashing, so every catalog task keeps the hash it had before them."""
    for task in factory.tasks.all(include_held_out=True):
        data = task.model_dump(mode="json", exclude={"status", "split", "tags"})
        for k in _SEED_OMIT_WHEN_NONE:             # the env-seed rules of the same formula (#45, #46), unchanged
            if data["env_seed"].get(k) is None:
                data["env_seed"].pop(k, None)
        if not task.env_seed.git:
            data["env_seed"].pop("git", None)
        else:
            data["git_seed"] = GIT_SEED
        for c in data["oracle"]["checks"]:
            for name in _CHECK_HASH_OMIT_WHEN_NONE:
                if c.get(name) is None or name not in USED_BY_NEW_TASKS:
                    assert c.pop(name) is None
        data["seed_hash"] = task.env_seed.data_hash(task.source_dir)
        assert task.content_hash() == content_hash(data), task.key()
    t = factory.tasks.get("sql-01").model_copy(deep=True)
    before = t.content_hash()
    t.oracle.checks[0].abs_tol = 0.5                    # a set field is part of the task's identity
    assert t.content_hash() != before


# ---- pluggable graders ----------------------------------------------------------------------------------------

def grade(gid, score, family, combine=None, passed=True, violations=()) -> Grade:
    return Grade(grader_id=gid, score=score, passed=passed, family=family,
                 combine=combine or ("count" if family == "safety" else "product"), violations=list(violations))


def legacy_scores(result: RunResult, grades: list[Grade], threshold: float) -> tuple[RunStatus, FamilyScores, bool]:
    """The scoring before graders were pluggable (runner.py at f5a593e), kept verbatim as the reference."""
    status = result.status
    if result.scored:
        state = next((g for g in grades if g.grader_id == "state_diff"), None)
        crit = any(v.critical for g in grades for v in g.violations) or any(v.critical for v in result.violations)
        if state is None or crit:
            status = RunStatus.FAIL
        elif state.score >= threshold and not overran(result):
            status = RunStatus.SUCCESS
        else:
            status = RunStatus.PARTIAL if state.score > 0 else RunStatus.FAIL
    by_id = {g.grader_id: g for g in grades}
    violations = list(result.violations) + [v for g in grades for v in g.violations]
    critical = any(v.critical for v in violations)
    scores = FamilyScores(
        outcome=(by_id["state_diff"].score * (OVERRUN_OUTCOME_FACTOR if overran(result) else 1.0)
                 if "state_diff" in by_id and result.scored and not critical else 0.0),
        trajectory=by_id["trajectory"].score if "trajectory" in by_id else 1.0,
        efficiency=0.0, safety=len(violations))
    return status, scores, critical


RESULTS = [  # how a run can end, as the graders see it
    dict(status=RunStatus.FAIL, scored=True, finished=True),
    dict(status=RunStatus.BUDGET_EXCEEDED, scored=True, violations=[Violation(kind="budget_exceeded")]),
    dict(status=RunStatus.FAIL, scored=True, violations=[Violation(kind="tool_not_allowed", critical=True)]),
    dict(status=RunStatus.FAIL, scored=True, violations=[Violation(kind="loop")]),
    dict(status=RunStatus.INCOMPLETE, scored=False),
    dict(status=RunStatus.ERROR, scored=False),
]


def assert_same_as_legacy(result: RunResult, grades: list[Grade], threshold: float, profile: str) -> None:
    old_status, old, old_crit = legacy_scores(result, grades, threshold)
    new, _, crit = run_scores(result, grades)
    assert _finalize_status(result, grades, threshold) == old_status
    assert (new, crit) == (old, old_crit)
    w = ScoreWeights.for_profile(profile)
    assert w.composite(new, crit) == w.composite(old, old_crit)


def _do_nothing_grades(f: Factory, task) -> list[Grade]:
    """The task's own graders on its untouched seed (no tool calls)."""
    with make_sandbox(label=f"same-{task.id}", kind="local") as sb:
        sb.reset(task)
        state = StateInspector(sb.workspace, sb.db_path).capture()
        ctx = GradeContext(task=task, result=RunResult(run_key=_key(), scored=True), before=state, after=state,
                           diff=StateDiff(), workspace=sb.workspace, db_path=sb.db_path, spans=[], max_steps=10)
        return f.graders.run(ctx)


def test_every_catalog_task_scores_as_before(factory):
    """For every task in the catalog: its real grades on the seed, then the same grades with state_diff and
    trajectory swept over partial / threshold / full scores, under every way a run can end. Status, family
    scores and E0 are identical to the hard-wired scoring."""
    from harness_factory.provision.provisioner import weight_profile
    for task in factory.tasks.all(include_held_out=True):
        base = _do_nothing_grades(factory, task)
        assert [g.grader_id for g in base] == task.grader_ids
        th = task.oracle.pass_threshold
        sweeps = itertools.product((0.0, 0.25, th, 1.0), (0.4, 1.0), (False, True))
        variants = [base] + [[g.model_copy(update={"score": s if g.grader_id == "state_diff" else q,
                                                   "violations": g.violations + ([Violation(kind="guard",
                                                                                            critical=True)]
                                                                                 if crit and g.grader_id ==
                                                                                 "state_diff" else [])})
                              for g in base] for s, q, crit in sweeps]
        for grades, kw in itertools.product(variants, RESULTS):
            assert_same_as_legacy(RunResult(run_key=_key(), **kw), grades, th,
                                  weight_profile(task, factory.declared))


async def test_real_runs_score_as_before(factory, p0_tasks):
    """Full scripted runs: the stored numbers equal the legacy scoring of the run's own grades."""
    from harness_factory.provision.provisioner import weight_profile
    for tid in p0_tasks:
        task = factory.tasks.get(tid)
        rec = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted")
        grades = [Grade(**{k: v for k, v in g.items() if k != "snapshot"}) for g in rec.grades
                  if g["grader_id"] != "run_error"]
        result = RunResult(run_key=rec.run_key, status=rec.status, scored=True,
                           violations=[v for v in rec.violations if not any(v in g.violations for g in grades)])
        status, old, crit = legacy_scores(result, grades, task.oracle.pass_threshold)
        assert (rec.status, rec.scores) == (status, old)
        assert rec.composite == round(ScoreWeights.for_profile(weight_profile(task, factory.declared))
                                      .composite(old, crit), 6)


class ToyGrader:
    """Feeds trajectory: half credit whenever the answer mentions 'maybe'."""
    id = "toy"
    version = "7"
    family = "trajectory"
    combine = "product"

    def grade(self, ctx: GradeContext) -> Grade:
        hedged = "maybe" in str(ctx.result.output or "")
        return Grade(grader_id=self.id, score=0.5 if hedged else 1.0, passed=not hedged)


@pytest.fixture
def toy():
    register_grader(ToyGrader)
    yield ToyGrader
    unregister_grader("toy")


async def test_registered_grader_feeds_trajectory(factory, toy):
    from harness_factory.substrate.gateway import ScriptedBackend
    factory.graders = default_pipeline()                     # the registry, now holding the toy grader
    assert factory.graders.versions() == {"state_diff": "1", "trajectory": "1", "toy": "7"}
    task = factory.tasks.get("fs-01")
    plain = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted")
    task.grader_ids = [*task.grader_ids, "toy"]
    script = [{"tool_calls": [{"name": "fs__read_file", "arguments": {"path": "notes.txt"}}]},
              {"content": "maybe done"}]
    hedged = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted",
                           backend=ScriptedBackend(script))
    traj = next(g for g in hedged.grades if g["grader_id"] == "trajectory")["score"]
    toy_grade = next(g for g in hedged.grades if g["grader_id"] == "toy")
    assert (toy_grade["score"], toy_grade["version"], toy_grade["family"]) == (0.5, "7", "trajectory")
    assert hedged.scores.trajectory == pytest.approx(traj * 0.5)
    assert plain.scores.trajectory == next(g for g in plain.grades if g["grader_id"] == "trajectory")["score"]


def test_family_rules():
    o = [grade("state_diff", 0.8, "outcome"), grade("extra", 0.5, "outcome")]
    assert combine_families(o).outcome == pytest.approx(0.4)
    m = [grade("state_diff", 0.8, "outcome", "min"), grade("extra", 0.5, "outcome", "min")]
    assert combine_families(m).outcome == 0.5
    assert combine_families([grade("note", 0.1, None)]) .model_dump() == {"outcome": None, "trajectory": None}
    result = RunResult(run_key=_key(), scored=True, status=RunStatus.FAIL)
    assert _finalize_status(result, [grade("state_diff", 1.0, "outcome"), grade("extra", 0.5, "outcome")],
                            1.0) == RunStatus.PARTIAL                       # status follows the outcome family
    assert _finalize_status(result, [grade("note", 1.0, None)], 1.0) == RunStatus.FAIL   # no outcome grader


class SafetyToy:
    id = "no_secrets"
    version = "1"
    family = "safety"
    combine = "critical"

    def grade(self, ctx):
        leaked = "sk-" in str(ctx.result.output or "")
        return Grade(grader_id=self.id, score=0.0 if leaked else 1.0, passed=not leaked)


class OutcomeMin(SafetyToy):
    id, family, combine = "outcome_min", "outcome", "min"


def test_pipeline_safety_and_declarations(tmp_path, factory):
    task = factory.tasks.get("fs-01").model_copy(deep=True)
    pipe = GradingPipeline({**default_pipeline().graders, "no_secrets": SafetyToy(), "outcome_min": OutcomeMin()})
    ctx = make_ctx(tmp_path, output="key sk-123")
    ctx.task = task
    task.grader_ids = ["state_diff", "no_secrets"]
    grades = pipe.run(ctx)
    assert grades[1].violations == [Violation(kind="grader_failed", critical=True, detail="no_secrets")]
    scores, _, critical = run_scores(ctx.result.model_copy(update={"scored": True}), grades)
    assert critical and scores.outcome == 0.0
    task.grader_ids = ["state_diff", "outcome_min"]          # product and min in one family: refused
    with pytest.raises(ValueError, match="share a combine rule"):
        pipe.run(ctx)

    class NoVersion(SafetyToy):
        version = ""

    class BadFamily(SafetyToy):
        family = "style"

    class BadRule(SafetyToy):
        family, combine = "trajectory", "mean"
    for bad, msg in ((NoVersion, "no version"), (BadFamily, "unknown family"), (BadRule, "combines by")):
        with pytest.raises(ValueError, match=msg):
            GradingPipeline({"x": bad()})
        with pytest.raises(ValueError, match=msg):
            register_grader(bad)


# ---- grader versions in the store ------------------------------------------------------------------------------

async def test_grader_versions_stored(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert {g["grader_id"]: g["version"] for g in rec.grades} == {"state_diff": "1", "trajectory": "1"}
    row = factory.store.rows()[-1]
    assert row["grader_versions"] == {"state_diff": "1", "trajectory": "1"}
    assert row["endstate_path"] and Path(row["endstate_path"]).is_file()


def test_store_migration_is_additive(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    ResultsStore(url)
    with sqlite3.connect(tmp_path / "old.db") as c:           # a store from before this change: revision 0001
        c.execute("ALTER TABLE runs DROP COLUMN grader_versions")
        c.execute("ALTER TABLE runs DROP COLUMN endstate_path")
        c.execute("DROP TABLE regrades")
        c.execute("UPDATE alembic_version SET version_num = '0001'")
    ResultsStore(url)
    with sqlite3.connect(tmp_path / "old.db") as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"grader_versions", "endstate_path"} <= cols and "regrades" in tables


# ---- end states and re-grading ---------------------------------------------------------------------------------

async def test_regrade_end_to_end(factory):
    task = factory.tasks.get("sql-01")
    rec = await run_one(factory, task, factory.recipes["v2-base"], "scripted/replay", "scripted")
    assert rec.status == RunStatus.SUCCESS
    row = factory.store.rows()[-1]

    same = (await regrade(factory, run_ids=[row["id"]]))[0]         # current graders, unchanged task: same grade
    assert same.after == same.before and not same.changed and not same.task_edited

    answer = next(c for c in task.oracle.checks if c.weight > 0)
    if answer.kind == "output_regex":
        answer.pattern = r"^no such answer$"
    else:
        answer.kind, answer.text = "output_exact", "no such answer"
    changed = (await regrade(factory, task="sql-01"))[0]
    assert changed.changed and changed.task_edited
    assert changed.after.outcome < changed.before.outcome and changed.after.status != "SUCCESS"

    kept = factory.store.run_by_id(row["id"])                 # the run's own row is untouched
    assert (kept["status"], kept["outcome"], kept["composite"]) == (row["status"], row["outcome"], row["composite"])
    stored = factory.store.regrade_rows(row["id"])
    assert [r["changed"] for r in stored] == [0, 1]
    assert stored[1]["grader_versions"] == {"state_diff": "1", "trajectory": "1"}
    assert stored[1]["outcome"] == changed.after.outcome and stored[1]["task_hash"] == task.content_hash()
    assert len(factory.store.rows()) == 1                      # aggregation still reads only the run


async def test_endstate_bundle(factory, monkeypatch):
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    bundle = Path(rec.endstate_path)
    assert bundle.name.endswith(".endstate.zip") and bundle.parent == settings.TRACES_DIR
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
    assert "run.json" in names and "state/task.sqlite" in names
    assert all("\\" not in n and not n.startswith("/") for n in names)      # POSIX names, portable to Windows
    ws, db, meta = load_endstate(bundle, Path(settings.SANDBOXES_DIR) / "x")
    assert meta["run_key"] == rec.run_key.as_str() and meta["status"] == rec.status.value
    assert {p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file()} >= set(meta["before"]["files"])
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0

    monkeypatch.setenv("HF_KEEP_ENDSTATE", "0")                # opt-out
    off = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    monkeypatch.setenv("HF_KEEP_ENDSTATE", "1")
    monkeypatch.setenv("HF_ENDSTATE_MAX_MB", "0.000001")      # over the cap
    big = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert off.endstate_path is None and big.endstate_path is None
    skipped = await regrade(factory, task="fs-01")
    assert [o.skipped for o in skipped] == ["no end state kept"]           # latest fs-01 row kept none
    assert factory.store.regrade_rows() == []


def test_bundle_names_cannot_escape(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("run.json", json.dumps({"format": 1}))
        z.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe name"):
        load_endstate(bad, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()
    with zipfile.ZipFile(tmp_path / "old.zip", "w") as z:
        z.writestr("run.json", json.dumps({"format": 99}))
    with pytest.raises(ValueError, match="format"):
        load_endstate(tmp_path / "old.zip", tmp_path / "dest2")


async def test_regrade_selection_errors(factory):
    with pytest.raises(ValueError, match="exactly one"):
        await regrade(factory)
    with pytest.raises(ValueError, match="exactly one"):
        await regrade(factory, task="fs-01", all_runs=True)
    with pytest.raises(KeyError):
        await regrade(factory, run_ids=[12345])
    assert await regrade(factory, all_runs=True) == []


async def test_regrade_task_without_database(factory):
    """A task with no SQL seed has an empty database file: kept as such, re-graded to the same result."""
    rec = await run_one(factory, factory.tasks.get("ops-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert rec.endstate_path is not None
    with zipfile.ZipFile(rec.endstate_path) as z:
        assert z.read("state/task.sqlite") == b""
    [o] = await regrade(factory, all_runs=True)
    assert o.after == o.before and o.after.status == rec.status.value
