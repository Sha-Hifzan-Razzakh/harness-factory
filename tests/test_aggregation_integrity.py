"""Aggregation integrity (review 2026-10-30, R-04 to R-07, O-01, L-11, L-12).

Each test pins one way the stored or aggregated numbers went wrong: free credit from robustness, an unscored rerun
hiding a scored result, unscored runs in the cost median, a guard-breaking run keeping its outcome, a crash in the
held-out evaluation, rows of an edited task still counting, and two different E's for the same rows."""

import math
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from harness_factory.results.cost import CostBaseline
from harness_factory.results.model import FamilyScores, ScoreWeights
from harness_factory.results.report import build_report
from harness_factory.results.store import ResultsStore, aggregate_rows, runs
from harness_factory.runner.runner import run_one
from harness_factory.substrate.gateway import ScriptedBackend

UNSCORED = ("INCOMPLETE", "ERROR")


def row(task="t1@1", seed=0, status="SUCCESS", outcome=1.0, traj=1.0, tokens=1000, fault="none", recipe="rA",
        rh="h1", th="th", cls="KNOWN_PATH|READ|R0", model="m", provider="p"):
    w = ScoreWeights.for_profile("read")
    comp = 0.0 if status in UNSCORED else w.composite(FamilyScores(outcome=outcome, trajectory=traj), False)
    return dict(run_key=f"{task}|{th}|{rh}|{model}|{provider}|{seed}|{fault}|env", task_version=task,
                task_class=cls, recipe_id=recipe, recipe_hash=rh, canonical_model=model, provider=provider,
                seed=seed, status=status, outcome=outcome, trajectory=traj, composite=comp, tokens_in=tokens,
                tokens_out=0, model_calls=1, tool_calls=1, wall_s=1.0, usd=0.0, violations=[], grades=[])


def store_with(tmp_path, *rows) -> ResultsStore:
    st = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    with st.engine.begin() as c:
        for r in rows:
            c.execute(insert(runs).values(run_short="x", created_at=datetime.now(UTC), **r))
    return st


# ---------------------------------------------------------------- R-04: robustness counts as R x O

def test_robustness_gives_no_credit_to_a_run_that_did_nothing():
    w = ScoreWeights.for_profile("read")
    assert w.composite(FamilyScores(outcome=0.0, trajectory=1.0, robustness=1.0), False) == 0.0
    assert w.composite(FamilyScores(outcome=1.0, trajectory=1.0, robustness=1.0), False) == pytest.approx(1.0)
    # half the outcome, full R: R is credited only for the half achieved
    assert w.composite(FamilyScores(outcome=0.5, trajectory=1.0, robustness=1.0), False) == pytest.approx(0.5)


def test_a_failed_run_stays_at_zero_once_its_cell_has_r():
    rows = [row(seed=0), row(seed=1, status="FAIL", outcome=0.0),
            row(seed=0, fault="fx", status="PARTIAL", outcome=0.5)]
    from harness_factory.results.store import e0_by_run
    [cell] = aggregate_rows(rows, CostBaseline.from_rows(rows))
    assert cell.robustness == pytest.approx(1.0)                 # faulted 0.5 / clean 0.5, capped
    e0 = e0_by_run(rows)
    assert e0[rows[1]["run_key"]] == 0.0                          # was 0.20 before the fix
    assert e0[rows[0]["run_key"]] == pytest.approx(1.0)
    assert cell.mean_e0 == pytest.approx(0.5)


# ---------------------------------------------------------------- R-05: an unscored rerun never hides a result

def test_an_incomplete_rerun_keeps_the_earlier_scored_result(tmp_path):
    st = store_with(tmp_path, row(seed=0), row(seed=0, status="INCOMPLETE", outcome=0.0, tokens=0))
    assert st.scored_keys() == {row(seed=0)["run_key"]}
    [kept] = st.rows()
    assert kept["status"] == "SUCCESS"
    _, [cell] = st.aggregate()
    assert cell.e.n == 1 and cell.excluded == 0


def test_a_scored_rerun_still_replaces_the_earlier_result(tmp_path):
    st = store_with(tmp_path, row(seed=0), row(seed=0, status="ERROR", outcome=0.0),
                    row(seed=0, status="FAIL", outcome=0.0))
    [kept] = st.rows()
    assert kept["status"] == "FAIL"                               # newest scored row wins


def test_a_key_with_only_unscored_rows_keeps_its_newest(tmp_path):
    st = store_with(tmp_path, row(seed=0, status="ERROR", outcome=0.0),
                    row(seed=0, status="INCOMPLETE", outcome=0.0))
    [kept] = st.rows()
    assert kept["status"] == "INCOMPLETE" and st.scored_keys() == set()    # resume retries it


# ---------------------------------------------------------------- R-06: unsolved-task median, scored runs only

def test_the_median_of_an_unsolved_task_ignores_unscored_runs():
    rows = [row(task="t2@1", seed=0, status="PARTIAL", outcome=0.5, tokens=5000),
            row(task="t2@1", seed=1, status="PARTIAL", outcome=0.5, tokens=6000),
            row(task="t2@1", seed=2, status="INCOMPLETE", outcome=0.0, tokens=0),
            row(task="t2@1", seed=3, status="ERROR", outcome=0.0, tokens=0),
            row(task="t2@1", seed=4, status="ERROR", outcome=0.0, tokens=100)]
    b = CostBaseline.from_rows(rows)
    assert b.medians == {"p|t2@1": 5500.0} and b.counts == {"p|t2@1": 2}


def test_a_task_with_only_unscored_runs_has_no_median():
    b = CostBaseline.from_rows([row(task="t3@1", status="INCOMPLETE", outcome=0.0, tokens=0)])
    assert b.medians == {}


# ---------------------------------------------------------------- R-07: a broken guard means outcome 0

async def test_a_run_that_breaks_a_guard_stores_outcome_zero(factory):
    def call(tool, **args):
        return {"tool_calls": [{"name": tool.replace(".", "__"), "arguments": args}]}

    script = [call("fs.write_file", path="service/config/staging.ini",
                   content="[server]\nport = 8100\n\n[database]\nhost = db-staging.internal\nname = shop_staging\n"),
              call("fs.write_file", path="service/config/prod.ini", content="[database]\nhost = db-staging.internal\n"),
              {"content": "done"}]
    rec = await run_one(factory, factory.tasks.get("ops-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted", backend=ScriptedBackend(script))
    assert rec.status.value == "FAIL" and rec.composite == 0.0
    assert any(v.critical for v in rec.violations)
    assert rec.scores.outcome == 0.0                              # was 1.0: the staging fix itself was right


# ---------------------------------------------------------------- O-01: held-out evaluation with no scored evidence

def test_held_out_evaluation_survives_a_recipe_with_only_unscored_evidence(factory, p0_tasks):
    from harness_factory.provision.provisioner import run_key_for, task_class
    from harness_factory.select.policy import plan_evaluation

    t = factory.tasks.get(p0_tasks[0])
    k = run_key_for(t, factory.recipes["v2-base"], "scripted/replay", "scripted", 0, declared=factory.declared,
                    providers=factory.providers)
    r = row(task=k.task_version, th=k.task_hash, rh=k.recipe_hash, model=k.canonical_model, provider="scripted",
            status="INCOMPLETE", outcome=0.0, cls=task_class(t, factory.declared), recipe="v2-base")
    r["run_key"] = k.as_str()
    plan = plan_evaluation(factory, [r], "scripted/replay", "scripted")       # ZeroDivisionError before the fix
    assert plan.picks["global"]


# ---------------------------------------------------------------- L-11: rows of an edited task stop counting

def test_rows_of_an_edited_task_definition_are_left_out():
    from harness_factory.results.store import current_task_rows
    old, new, other = row(th="old"), row(th="new", seed=1), row(task="gone@1", th="x")
    kept = current_task_rows([old, new, other], {"t1@1": "new"})
    assert kept == [new, other]                                   # a task no longer in the catalog is kept


def test_selector_cells_use_only_the_current_task_definition(factory, p0_tasks):
    from harness_factory.provision.provisioner import run_key_for, task_class
    from harness_factory.select.selector import _current_cells

    t = factory.tasks.get(p0_tasks[0])
    k = run_key_for(t, factory.recipes["v2-base"], "scripted/replay", "scripted", 0, declared=factory.declared,
                    providers=factory.providers)

    def stored(task_hash, seed):
        r = row(task=k.task_version, th=task_hash, rh=k.recipe_hash, model=k.canonical_model, provider="scripted",
                seed=seed, cls=task_class(t, factory.declared), recipe="v2-base")
        parts = k.as_str().split("|")
        parts[1], parts[5] = task_hash, str(seed)
        r["run_key"] = "|".join(parts)
        return r

    rows = [stored(k.task_hash, 0), stored("edited-away", 1), stored("edited-away", 2)]
    cells = _current_cells(factory, rows, CostBaseline.from_rows(rows), "scripted/replay", "scripted")
    [cell] = cells.values()
    assert cell.e.n == 1


def test_results_cells_leave_out_edited_task_rows(tmp_path):
    st = store_with(tmp_path, row(th="old"), row(th="new", seed=1))
    _, [cell] = st.aggregate(task_hashes={"t1@1": "new"})
    assert cell.e.n == 1
    _, [cell] = st.aggregate()
    assert cell.e.n == 2                                          # without the catalog, nothing to compare with


# ---------------------------------------------------------------- L-12: one E everywhere; Inspect skips unscored

def test_report_section_2_uses_the_same_e_as_the_cells(factory):
    rows = [row(task="fs-01@1", seed=0, recipe="v2-base"),
            row(task="fs-01@1", seed=1, recipe="v2-base", status="PARTIAL", outcome=0.5),
            row(task="fs-01@1", seed=0, recipe="v2-base", fault="fx", status="PARTIAL", outcome=0.25)]
    b = CostBaseline.from_rows(rows)
    [cell] = aggregate_rows(rows, b)
    assert cell.robustness is not None and cell.robustness < 1
    text = build_report(factory, rows, b, with_selector=False)
    line = next(ln for ln in text.splitlines() if ln.startswith("| `v2-base`"))
    e0_col, e_col = line.split("|")[-4].strip(), line.split("|")[-3].strip()
    assert e0_col == f"{cell.mean_e0:.3f}" and e_col == f"{cell.e.mean:.3f}"


@pytest.mark.parametrize("status", UNSCORED)
def test_inspect_leaves_unscored_runs_out_of_its_means(status):
    pytest.importorskip("inspect_ai")
    from harness_factory.integrations import inspect_task

    rec = {"status": status, "composite": 0.0, "scores": {"outcome": 0.0, "trajectory": 1.0}}
    assert all(math.isnan(v) for v in inspect_task.score_values(rec).values())
    rec = {"status": "PARTIAL", "composite": 0.4, "scores": {"outcome": 0.5, "trajectory": 1.0}}
    assert inspect_task.score_values(rec) == {"e0": 0.4, "outcome": 0.5, "trajectory": 1.0, "success": 0.0}
