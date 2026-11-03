"""Shape × model analysis (hfx analyze, ANL-01): intervals, winners, crossovers, SQL views, and a scripted campaign."""

import random

import polars as pl
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from typer.testing import CliRunner

from harness_factory.results import analysis as anl
from harness_factory.results.cost import CostBaseline
from harness_factory.results.report import paired_delta
from harness_factory.results.store import ResultsStore, aggregate_rows
from harness_factory.runner.runner import plan, run_campaign

NO_COST = CostBaseline(bases={}, medians={}, counts={})          # C = 0 for every run, so E = the stored composite
SS, KP, UP = "SINGLE_STEP|READ|R0", "KNOWN_PATH|REVERSIBLE|R1", "UNKNOWN_PATH|READ|R0"
ARMS = anl.Arms(shapes={"v1": "V1", "v2": "V2"},
                plan_len={"s1@1": 1, "s2@1": 1, "s3@1": 1, "k1@1": 3, "k2@1": 3, "k3@1": 4, "u1@1": 6, "u2@1": 6})


def row(task: str, recipe: str, e: float, *, seed: int = 0, cls: str = KP, model: str = "m",
        status: str = "SUCCESS") -> dict:
    """A results-store row as ResultsStore.rows() returns it (only the fields aggregation reads)."""
    return dict(run_key=f"{task}|th|{recipe}-h|{model}|scripted|{seed}|none|env", task_version=task, task_class=cls,
                recipe_id=recipe, recipe_hash=f"{recipe}-h", canonical_model=model, provider="scripted", seed=seed,
                status=status, outcome=e, trajectory=1.0, composite=e, tokens_in=100, tokens_out=0, usd=0.0,
                violations=[], grades=[])


def synthetic() -> list[dict]:
    """V1 clearly best on single-step tasks, V2 clearly best on known-path tasks, mixed on unknown-path ones.
    Model m2 solves nothing with either shape (a tie everywhere)."""
    rows = []
    for seed in (0, 1):
        for t, (v1, v2) in {"s1@1": (0.90, 0.80), "s2@1": (0.95, 0.85), "s3@1": (0.92, 0.83)}.items():
            rows += [row(t, "v1", v1, seed=seed, cls=SS), row(t, "v2", v2, seed=seed, cls=SS)]
        for t, (v1, v2) in {"k1@1": (0.10, 0.90), "k2@1": (0.0, 0.85), "k3@1": (0.20, 0.95)}.items():
            rows += [row(t, "v1", v1, seed=seed), row(t, "v2", v2, seed=seed)]
        for t, (v1, v2) in {"u1@1": (0.20, 0.50), "u2@1": (0.60, 0.40)}.items():
            rows += [row(t, "v1", v1, seed=seed, cls=UP), row(t, "v2", v2, seed=seed, cls=UP)]
        for t in ("s1@1", "k1@1"):
            rows += [row(t, r, 0.0, seed=seed, cls=SS if t[0] == "s" else KP, model="m2") for r in ("v1", "v2")]
    return rows


def _winner(df: pl.DataFrame, **where) -> dict:
    hits = df.filter(pl.all_horizontal(pl.col(k) == v for k, v in where.items())).to_dicts()
    assert len(hits) == 1, hits
    return hits[0]


@pytest.mark.parametrize("seed", range(8))
def test_scipy_paired_interval_agrees_with_paired_delta(seed):
    rng = random.Random(seed)
    pairs = [(rng.random(), rng.random()) for _ in range(rng.randint(3, 25))]
    lo, hi = anl.bootstrap_ci([v - b for b, v in pairs])
    ref = paired_delta(pairs)["ci"]
    tol = 0.1 * (hi - lo)                    # two bootstraps of the same percentile interval: Monte Carlo noise only
    assert abs(lo - ref[0]) <= tol and abs(hi - ref[1]) <= tol


def test_degenerate_intervals():
    assert anl.bootstrap_ci([0.4]) == (0.4, 0.4)
    assert anl.bootstrap_ci([0.7, 0.7, 0.7]) == pytest.approx((0.7, 0.7))


def test_winners_per_model_and_class():
    a = anl.analyze(synthetic(), NO_COST, ARMS)
    ss = _winner(a.class_winners, model="m", task_class=SS)
    assert (ss["winner_shape"], ss["verdict"], ss["method"], ss["n_pairs"], ss["n_tasks"]) == ("V1", "clear",
                                                                                               "paired", 6, 3)
    assert ss["delta"] == pytest.approx(0.0967, abs=1e-3) and ss["delta_ci_lo"] > 0
    kp = _winner(a.class_winners, model="m", task_class=KP)
    assert (kp["winner_shape"], kp["verdict"]) == ("V2", "clear")
    up = _winner(a.class_winners, model="m", task_class=UP)
    assert (up["winner_shape"], up["verdict"]) == ("V2", "not clear") and up["delta_ci_lo"] < 0 < up["delta_ci_hi"]
    tie = _winner(a.class_winners, model="m2", task_class=SS)
    assert tie["verdict"] == "tie" and tie["top_set"] == "v1,v2"
    # every paired comparison carries report.paired_delta's interval next to scipy's
    assert a.interval_agreement() <= 0.1 * max(c["delta_ci_hi"] - c["delta_ci_lo"]
                                               for c in a.class_comparisons.to_dicts()) + 1e-12


def test_arm_means_are_the_aggregation_cells():
    rows = synthetic()
    a = anl.analyze(rows, NO_COST, ARMS)
    cells = {(c.canonical_model, c.task_class, c.recipe_id): c for c in aggregate_rows(rows, NO_COST)}
    for r in a.by_class.to_dicts():
        cell = cells[(r["model"], r["task_class"], r["recipe_id"])]
        assert r["mean_e"] == pytest.approx(cell.e.mean, abs=1e-12) and r["n"] == cell.e.n


def test_crossover_by_depth_and_plan_length():
    a = anl.analyze(synthetic(), NO_COST, ARMS)
    depth = a.by_depth.filter((pl.col("axis") == "depth") & (pl.col("model") == "m"))
    assert depth["bucket"].unique(maintain_order=True).to_list() == ["SINGLE_STEP", "KNOWN_PATH", "UNKNOWN_PATH"]
    cx = a.crossover.filter(pl.col("model") == "m").to_dicts()
    by_axis = {(c["axis"], c["from_bucket"], c["to_bucket"]): c for c in cx}
    d = by_axis[("depth", "SINGLE_STEP", "KNOWN_PATH")]
    assert (d["from_shape"], d["to_shape"], d["significant"]) == ("V1", "V2", True)
    assert ("depth", "KNOWN_PATH", "UNKNOWN_PATH") not in by_axis           # V2 stays best (not clearly)
    p = by_axis[("plan_len", "1", "3")]
    assert (p["from_shape"], p["to_shape"]) == ("V1", "V2")
    assert a.crossover.filter(pl.col("model") == "m2").is_empty()           # a tie is never a crossover
    md = anl.to_markdown(a, "T")
    assert md.startswith("# T") and "## 2. Crossover by step depth" in md
    assert "V1 `v1` → V2 `v2` | yes" in md and "tie (v1, v2)" in md


def test_one_task_is_never_a_clear_win():
    rows = [row("s1@1", r, e, seed=s, cls=SS) for s in range(5) for r, e in (("v1", 0.9), ("v2", 0.1))]
    w = anl.analyze(rows, NO_COST, ARMS).class_winners.to_dicts()[0]
    assert (w["winner_shape"], w["n_pairs"], w["n_tasks"], w["verdict"]) == ("V1", 5, 1, "not clear")


def test_unpaired_when_runs_do_not_pair():
    rows = [row(f"s{i}@1", "v1", 0.9 + 0.01 * i, seed=0, cls=SS) for i in (1, 2, 3)]
    rows += [row(f"s{i}@1", "v2", 0.1 + 0.01 * i, seed=1, cls=SS) for i in (1, 2, 3)]
    w = anl.analyze(rows, NO_COST, ARMS).class_winners.to_dicts()[0]
    assert (w["winner_shape"], w["method"], w["n_pairs"], w["verdict"]) == ("V1", "unpaired", 0, "clear")


def test_unscored_and_faulted_runs_are_left_out():
    rows = synthetic() + [row("s1@1", "v2", 1.0, seed=7, cls=SS, status="INCOMPLETE")]
    faulted = row("s1@1", "v2", 1.0, seed=8, cls=SS)
    faulted["run_key"] = faulted["run_key"].replace("|none|", "|f1|")
    a = anl.analyze([*rows, faulted], NO_COST, ARMS)
    assert a.runs.height == len(synthetic()) and 7 not in a.runs["seed"] and 8 not in a.runs["seed"]


def test_views_on_sqlite_are_idempotent_and_leave_runs_alone(tmp_path):
    store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    a = anl.analyze(synthetic(), NO_COST, ARMS)
    for _ in range(2):
        assert anl.materialize(store.engine, a) == list(anl.VIEWS)
    with store.engine.connect() as c:
        n = {v: c.execute(text(f"SELECT COUNT(*) FROM {v}")).scalar() for v in anl.VIEWS}
        best = c.execute(text("SELECT shape FROM v_shape_model_class WHERE model = 'm' AND task_class = :c "
                              "AND is_winner"), {"c": KP}).scalars().all()
        sig = c.execute(text("SELECT from_shape, to_shape FROM v_depth_crossover WHERE axis = 'depth' "
                             "AND significant")).all()
    assert n == {"v_shape_model_class": a.by_class.height, "v_shape_depth": a.by_depth.height,
                 "v_shape_winners": a.class_winners.height + a.depth_winners.height,
                 "v_depth_crossover": a.crossover.height}
    assert best == ["V2"] and sig == [("V1", "V2")]
    assert store.rows() == [] and store.list_baselines() == []


def test_analysis_tables_compile_for_postgres():
    for t in anl.ANL_TABLES.values():
        ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
        assert f"CREATE TABLE {t.name}" in ddl and "generated_at TIMESTAMP" in ddl
        assert ("BOOLEAN" in ddl) == any(str(c.type) == "BOOLEAN" for c in t.columns)


def test_no_runs_still_renders(tmp_path):
    a = anl.analyze([], NO_COST, ARMS)
    assert "No scored runs" in anl.to_markdown(a)
    assert anl.materialize(create_engine(f"sqlite:///{tmp_path / 'e.db'}"), a) == list(anl.VIEWS)


async def test_analysis_of_a_scripted_campaign(factory, p0_tasks, tmp_path, monkeypatch):
    """V1 (one call) cannot finish the known-path tasks; V2 (tool loop) can: V2 wins there, clearly."""
    await run_campaign(factory, plan(factory, ["v1-base", "v2-base"], "scripted", ["scripted/replay"],
                                     task_ids=p0_tasks), concurrency=4)
    rows = anl.current_rows(factory, factory.store.rows())
    assert len(rows) == 2 * len(p0_tasks)
    arms = anl.arms_of(factory, ["v1-base", "v2-base"])
    assert set(anl.arms_of(factory).shapes.values()) == {"V1", "V2", "V3", "V4", "V5"}
    a = anl.analyze(rows, CostBaseline.from_rows(rows), arms)
    kp = _winner(a.depth_winners, axis="depth", bucket="KNOWN_PATH")
    assert (kp["winner_recipe"], kp["verdict"], kp["n_tasks"]) == ("v2-base", "clear", 3)
    assert set(a.runs["plan_len"].to_list()) == {1, 2}
    assert a.interval_agreement() <= 0.05

    from harness_factory.cli import main as cli
    monkeypatch.setattr(cli, "_factory", lambda store=True: factory)
    out = tmp_path / "anl"
    res = CliRunner().invoke(cli.app, ["analyze", "--out", str(out), "--views", "--recipes", "v1-base,v2-base"])
    assert res.exit_code == 0, res.output
    assert {p.name for p in out.iterdir()} == {"analysis.md", *anl.CSV_FILES}
    assert "## 1. Shape × model by task class" in (out / "analysis.md").read_text()
    assert pl.read_csv(out / "shape_model_class.csv").height == a.by_class.height
    with factory.store.engine.connect() as c:
        assert c.execute(text("SELECT winner_recipe FROM v_shape_winners WHERE scope = 'depth' "
                              "AND bucket = 'KNOWN_PATH'")).scalar() == "v2-base"
    res = CliRunner().invoke(cli.app, ["analyze", "--recipes", "nope"])
    assert res.exit_code != 0
