"""Shape × model analysis (ANL-01, ANL-02): which harness shape wins per model and task class, and where the
best shape changes with step depth. `hfx analyze`.

Built on polars (frames) and scipy (scipy.stats.bootstrap, percentile intervals), per FWK-01. Scoring is not
re-implemented: cells come from `store.aggregate_rows`, and each run's E is the same E those cells average
(its E0 with the cell's R folded in, cost-adjusted against one `CostBaseline`). `run_frame` checks that the
per-run means equal the cell means, so this view cannot drift from `hfx results --cells` or the selector.

Views:
  1. Shape × model by task class   mean E with a 95% bootstrap interval per arm (one recipe of one shape),
                                   and the winner per (model, class)
  2. Crossover by step depth       the same per depth bucket: the task class's expected depth (SINGLE_STEP <
                                   KNOWN_PATH < UNKNOWN_PATH) and the reference plan length; a crossover is where
                                   the best arm changes between adjacent buckets
  3. SQL views on the results DB   `v_shape_model_class`, `v_shape_winners`, `v_shape_depth`, `v_depth_crossover`
                                   over tables `anl_*` that each run replaces (`materialize`), for Superset

How a winner is decided: the arm with the highest mean E. It is "clear" only when it beats every other arm: the
95% interval of the paired difference (same task and seed, winner minus other) lies above 0. Pairing removes the
task-to-task spread, as in report §3, and the interval agrees with `report.paired_delta` (kept per comparison as
`pd_ci_lo` / `pd_ci_hi`). Arms with fewer than 2 pairs are compared unpaired: clear only if the two arms'
intervals don't overlap. Either way a clear win needs evidence from at least 2 tasks (seeds of one task are not
independent evidence about a class). Arms with the same mean are a tie. A crossover is where the arms tied at the
top of two adjacent buckets have nothing in common; it is "significant" when both winners are clear.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, text
from sqlalchemy.engine import Engine

from harness_factory.results.cost import CostBaseline, is_clean
from harness_factory.results.report import _cls, _table, paired_delta
from harness_factory.results.store import UNSCORED, _e0_with_r, aggregate_rows, env_of

DEPTHS = ("SINGLE_STEP", "KNOWN_PATH", "UNKNOWN_PATH")
AXES = ("depth", "plan_len")
MIN_PAIRS = 2
MIN_TASKS = 2
EPS = 1e-9
N_RESAMPLES = 9999

RUN_SCHEMA = {
    "task_version": pl.String, "seed": pl.Int64, "task_class": pl.String, "depth": pl.String,
    "plan_len": pl.Int64, "recipe_id": pl.String, "recipe_hash": pl.String, "env_hash": pl.String,
    "shape": pl.String, "model": pl.String, "provider": pl.String, "status": pl.String,
    "e0": pl.Float64, "e": pl.Float64,
}

CROSSOVER_SCHEMA = {
    **{k: pl.String for k in ("axis", "model", "provider", "from_bucket", "to_bucket", "from_shape", "to_shape",
                              "from_recipe", "to_recipe", "from_verdict", "to_verdict")},
    "significant": pl.Boolean,
}


# ---------------------------------------------------------------- catalog facts the analysis needs


@dataclass(frozen=True)
class Arms:
    """What is compared: recipe id -> shape label (V1..V5), and task_version -> reference plan length."""
    shapes: dict[str, str]
    plan_len: dict[str, int] = field(default_factory=dict)


def shape_label(variant: str) -> str:
    """V2_TOOL_LOOP -> V2."""
    return variant.split("_", 1)[0]


def arms_of(f, recipes: Iterable[str] | None = None) -> Arms:
    """The shape recipes (no `base:`, i.e. not a one-axis matrix recipe) unless recipe ids are given."""
    from harness_factory.tasks.reference import load_reference
    ids = list(recipes) if recipes else sorted(rid for rid, r in f.recipes.items() if not r.base)
    unknown = [rid for rid in ids if rid not in f.recipes]
    if unknown:
        raise KeyError(f"unknown recipe(s) {unknown}; known: {sorted(f.recipes)}")
    plan_len = {}
    for t in f.tasks.all(include_held_out=True):
        try:
            plan_len[t.key()] = len(load_reference(t).plan)
        except (FileNotFoundError, ValueError):
            continue
    return Arms(shapes={rid: shape_label(f.recipes[rid].variant.value) for rid in ids}, plan_len=plan_len)


def current_rows(f, rows: list[dict]) -> list[dict]:
    """Rows whose recipe hash and tool-environment hash are current (what the selector counts as evidence);
    rows of recipes or provider models no longer in the catalog are left out."""
    current: dict[tuple[str, str, str], tuple[str, str] | None] = {}
    out = []
    for r in rows:
        k = (r["recipe_id"], r["provider"], r["canonical_model"])
        if k not in current:
            recipe = f.recipes.get(r["recipe_id"])
            try:
                served = f.providers.served(r["provider"], r["canonical_model"])
            except KeyError:
                served = None
            current[k] = None if recipe is None or served is None else (
                recipe.content_hash(), f.declared.env_hash(recipe.tools.mcp_servers, served.provider_model_id))
        if current[k] is not None and (r["recipe_hash"], env_of(r)) == current[k]:
            out.append(r)
    return out


# ---------------------------------------------------------------- statistics


def bootstrap_ci(values: Iterable[float], seed: int = 0, n_resamples: int = N_RESAMPLES) -> tuple[float, float]:
    """95% percentile bootstrap interval of the mean (scipy.stats.bootstrap). One value, or all values equal:
    the interval is the value itself (there is no spread to resample)."""
    a = np.asarray(list(values), dtype=float)
    if a.size == 0:
        return (float("nan"), float("nan"))
    if a.size == 1 or np.ptp(a) <= EPS:
        m = float(a.mean())
        return (m, m)
    res = stats.bootstrap((a,), np.mean, n_resamples=n_resamples, confidence_level=0.95, method="percentile",
                          vectorized=True, rng=np.random.default_rng(seed))
    return (float(res.confidence_interval.low), float(res.confidence_interval.high))


# ---------------------------------------------------------------- per-run frame


def run_frame(rows: list[dict], baseline: CostBaseline, arms: Arms, models: list[str] | None = None) -> pl.DataFrame:
    """One row per scored clean run of a compared recipe, with the E its cell averages. Pass current rows
    (`current_rows`): an arm is a recipe id, so two hashes of one recipe would pool."""
    rows = [r for r in rows if r["recipe_id"] in arms.shapes and (not models or r["canonical_model"] in models)]
    cell_of = {(c.task_class, c.recipe_hash, c.canonical_model, c.provider, c.env_hash): c
               for c in aggregate_rows(rows, baseline)}
    recs = []
    for r in rows:
        if not is_clean(r) or r["status"] in UNSCORED:
            continue
        cell = cell_of[(r["task_class"], r["recipe_hash"], r["canonical_model"], r["provider"], env_of(r))]
        e0 = _e0_with_r(r, cell.robustness)          # the cell's R folded in, exactly as aggregate_rows does
        recs.append(dict(
            task_version=r["task_version"], seed=int(r["seed"]), task_class=r["task_class"],
            depth=r["task_class"].split("|")[0], plan_len=arms.plan_len.get(r["task_version"]),
            recipe_id=r["recipe_id"], recipe_hash=r["recipe_hash"], env_hash=env_of(r),
            shape=arms.shapes[r["recipe_id"]], model=r["canonical_model"], provider=r["provider"],
            status=r["status"], e0=e0, e=baseline.adjusted(r, e0)))
    df = pl.DataFrame(recs, schema=RUN_SCHEMA)
    # Same E as the aggregation: every cell mean must come back from the per-run values.
    got = {tuple(t[:5]): t[5] for t in df.group_by(["task_class", "recipe_hash", "model", "provider", "env_hash"])
           .agg(pl.col("e").mean()).iter_rows()}
    for k, c in cell_of.items():
        if c.e.n and abs(got.get(k, float("nan")) - c.e.mean) > 1e-9:
            raise RuntimeError(f"per-run E disagrees with aggregate_rows for cell {k}")
    return df


# ---------------------------------------------------------------- arms and winners


def arm_table(runs: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Per group and arm: n, mean E, 95% interval, mean E0, pass rate. Sorted by group, then mean E (best first)."""
    g = runs.group_by([*by, "shape", "recipe_id"], maintain_order=True).agg(
        n=pl.len(), mean_e=pl.col("e").mean(), mean_e0=pl.col("e0").mean(),
        pass_rate=(pl.col("status") == "SUCCESS").mean(), values=pl.col("e"))
    cis = [bootstrap_ci(v) for v in g["values"].to_list()]
    g = g.with_columns(ci_lo=pl.Series([c[0] for c in cis], dtype=pl.Float64),
                       ci_hi=pl.Series([c[1] for c in cis], dtype=pl.Float64)).drop("values")
    return g.sort([*by, "mean_e", "shape", "recipe_id"], descending=[*([False] * len(by)), True, False, False])


def _compare(win: pl.DataFrame, other: pl.DataFrame, win_ci: tuple[float, float],
             other_ci: tuple[float, float]) -> dict:
    """Winner vs one other arm, paired by (task_version, seed) where both have a run (report.paired_delta's
    pairing). Clear needs the interval above 0 and pairs from at least MIN_TASKS tasks: seeds of one task are
    not independent evidence about a class."""
    pairs_df = win.select("task_version", "seed", w="e").join(
        other.select("task_version", "seed", o="e"), on=["task_version", "seed"], how="inner"
    ).sort("task_version", "seed")
    pairs = [(o, w) for w, o in zip(pairs_df["w"].to_list(), pairs_df["o"].to_list(), strict=True)]
    n_tasks = pairs_df["task_version"].n_unique()
    if len(pairs) >= MIN_PAIRS:
        deltas = [w - o for o, w in pairs]
        lo, hi = bootstrap_ci(deltas)
        pd = paired_delta(pairs)
        return dict(method="paired", n_pairs=len(pairs), n_tasks=n_tasks, delta=float(np.mean(deltas)),
                    delta_ci_lo=lo, delta_ci_hi=hi, pd_ci_lo=pd["ci"][0], pd_ci_hi=pd["ci"][1], wins=pd["wins"],
                    losses=pd["losses"], ties=pd["ties"], clear=lo > EPS and n_tasks >= MIN_TASKS)
    delta = float(win["e"].mean() - other["e"].mean())
    n_tasks = min(win["task_version"].n_unique(), other["task_version"].n_unique())
    return dict(method="unpaired", n_pairs=len(pairs), n_tasks=n_tasks, delta=delta, delta_ci_lo=None,
                delta_ci_hi=None, pd_ci_lo=None, pd_ci_hi=None, wins=None, losses=None, ties=None,
                clear=win_ci[0] > other_ci[1] + EPS and n_tasks >= MIN_TASKS)


def winners(runs: pl.DataFrame, arms: pl.DataFrame, by: list[str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Winner per group and its comparisons with every other arm.

    Returns (winners, comparisons). Verdicts:
      clear       every comparison is clear (see _compare)
      not clear   at least one is not
      tie         another arm has the same mean E (`top_set` lists every arm tied at the top)
      only arm    nothing else ran in the group
    The contender shown is the arm whose comparison is weakest (lowest interval bound): the one that came closest.
    """
    win_rows, cmp_rows = [], []
    for grp, arm_rows in arms.group_by(by, maintain_order=True):
        grp = dict(zip(by, grp, strict=True))
        ranked = arm_rows.sort(["mean_e", "shape", "recipe_id"], descending=[True, False, False]).to_dicts()
        top = ranked[0]
        top_set = [a["recipe_id"] for a in ranked if abs(a["mean_e"] - top["mean_e"]) <= EPS]
        in_grp = runs.filter(pl.all_horizontal(pl.col(k) == v for k, v in grp.items()))
        win_runs = in_grp.filter(pl.col("recipe_id") == top["recipe_id"])
        comps = []
        for a in ranked[1:]:
            c = _compare(win_runs, in_grp.filter(pl.col("recipe_id") == a["recipe_id"]),
                         (top["ci_lo"], top["ci_hi"]), (a["ci_lo"], a["ci_hi"]))
            c.update(grp, winner_recipe=top["recipe_id"], winner_shape=top["shape"], other_recipe=a["recipe_id"],
                     other_shape=a["shape"])
            comps.append(c)
        cmp_rows += comps
        closest = min(comps, key=lambda c: c["delta_ci_lo"] if c["delta_ci_lo"] is not None else c["delta"],
                      default={})
        verdict = ("only arm" if not comps else "tie" if len(top_set) > 1
                   else "clear" if all(c["clear"] for c in comps) else "not clear")
        win_rows.append({
            **grp, "winner_shape": top["shape"], "winner_recipe": top["recipe_id"], "mean_e": top["mean_e"],
            "n": top["n"], "arms": len(ranked), "top_set": ",".join(top_set),
            "contender_shape": closest.get("other_shape"), "contender_recipe": closest.get("other_recipe"),
            **{k: closest.get(k) for k in ("delta", "delta_ci_lo", "delta_ci_hi", "n_pairs", "n_tasks", "method")},
            "verdict": verdict,
        })
    return pl.DataFrame(win_rows, infer_schema_length=None), pl.DataFrame(cmp_rows, infer_schema_length=None)


# ---------------------------------------------------------------- crossover by depth


def _bucketed(runs: pl.DataFrame, axis: str) -> pl.DataFrame:
    if axis == "depth":
        order = {d: i for i, d in enumerate(DEPTHS)}
        return runs.with_columns(axis=pl.lit("depth"), bucket=pl.col("depth"),
                                 bucket_order=pl.col("depth").replace_strict(order, default=len(DEPTHS),
                                                                              return_dtype=pl.Int64))
    return runs.filter(pl.col("plan_len").is_not_null()).with_columns(
        axis=pl.lit("plan_len"), bucket=pl.col("plan_len").cast(pl.String), bucket_order=pl.col("plan_len"))


def crossovers(depth_winners: pl.DataFrame) -> pl.DataFrame:
    """Where the best arm changes between adjacent buckets (per model, provider and axis)."""
    out: list[dict] = []
    if depth_winners.is_empty():
        return pl.DataFrame(out, schema=CROSSOVER_SCHEMA)
    for (axis, model, provider), g in depth_winners.group_by(["axis", "model", "provider"], maintain_order=True):
        seq = g.sort("bucket_order").to_dicts()
        for a, b in itertools.pairwise(seq):
            if not set(a["top_set"].split(",")) & set(b["top_set"].split(",")):     # a tie is no crossover
                out.append(dict(axis=axis, model=model, provider=provider, from_bucket=a["bucket"],
                                to_bucket=b["bucket"], from_shape=a["winner_shape"], to_shape=b["winner_shape"],
                                from_recipe=a["winner_recipe"], to_recipe=b["winner_recipe"],
                                from_verdict=a["verdict"], to_verdict=b["verdict"],
                                significant=a["verdict"] == "clear" and b["verdict"] == "clear"))
    return pl.DataFrame(out, schema=CROSSOVER_SCHEMA)


# ---------------------------------------------------------------- the analysis


@dataclass
class Analysis:
    baseline_id: str
    runs: pl.DataFrame              # per run
    by_class: pl.DataFrame          # arms per (model, provider, task_class)
    class_winners: pl.DataFrame
    class_comparisons: pl.DataFrame
    by_depth: pl.DataFrame          # arms per (axis, model, provider, bucket)
    depth_winners: pl.DataFrame
    depth_comparisons: pl.DataFrame
    crossover: pl.DataFrame
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def interval_agreement(self) -> float:
        """Largest gap between a scipy paired interval endpoint and report.paired_delta's (0 if none paired)."""
        gaps = [0.0]
        for df in (self.class_comparisons, self.depth_comparisons):
            if df.is_empty() or "pd_ci_lo" not in df.columns:
                continue
            p = df.filter(pl.col("method") == "paired")
            if not p.is_empty():
                gaps.append(float(max((p["delta_ci_lo"] - p["pd_ci_lo"]).abs().max(),
                                      (p["delta_ci_hi"] - p["pd_ci_hi"]).abs().max())))
        return max(gaps)


def analyze(rows: list[dict], baseline: CostBaseline, arms: Arms, models: list[str] | None = None) -> Analysis:
    runs = run_frame(rows, baseline, arms, models)
    cls_by = ["model", "provider", "task_class"]
    by_class = arm_table(runs, cls_by)
    cw, cc = winners(runs, by_class, cls_by)
    bucketed = pl.concat([_bucketed(runs, a) for a in AXES]) if not runs.is_empty() else runs.with_columns(
        axis=pl.lit(None, pl.String), bucket=pl.lit(None, pl.String), bucket_order=pl.lit(None, pl.Int64))
    dep_by = ["axis", "model", "provider", "bucket", "bucket_order"]
    by_depth = arm_table(bucketed, dep_by).sort(
        ["axis", "model", "provider", "bucket_order", "mean_e"], descending=[False, False, False, False, True])
    dw, dc = winners(bucketed, by_depth, dep_by)
    if not dw.is_empty():
        dw = dw.sort(["axis", "model", "provider", "bucket_order"])
    return Analysis(baseline_id=baseline.id, runs=runs, by_class=by_class, class_winners=cw, class_comparisons=cc,
                    by_depth=by_depth, depth_winners=dw, depth_comparisons=dc, crossover=crossovers(dw))


# ---------------------------------------------------------------- markdown and CSV


def _f(x, fmt: str = ".3f") -> str:
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else format(x, fmt)


def _arm_cell(r: dict) -> str:
    return f"{r['mean_e']:.3f} [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}] n={r['n']}"


def _pivot(arms: pl.DataFrame, by: list[str], label) -> tuple[list[str], list[list[str]]]:
    shapes = sorted({(r["shape"], r["recipe_id"]) for r in arms.iter_rows(named=True)})
    head = [*label(None), *(f"{s} `{rid}`" for s, rid in shapes)]
    body = []
    for grp, g in arms.group_by(by, maintain_order=True):
        cell = {(r["shape"], r["recipe_id"]): _arm_cell(r) for r in g.iter_rows(named=True)}
        body.append([*label(dict(zip(by, grp, strict=True))), *(cell.get(s, "-") for s in shapes)])
    return head, body


def _verdict(r: dict) -> str:
    if r["verdict"] == "only arm":
        return "only arm"
    if r["verdict"] == "tie":
        return f"tie ({r['top_set'].replace(',', ', ')})"
    tasks = f"{r['n_tasks']} task{'s' if r['n_tasks'] != 1 else ''}"
    ci = (f"Δ {r['delta']:+.3f} [{r['delta_ci_lo']:+.3f}, {r['delta_ci_hi']:+.3f}], {r['n_pairs']} pairs, {tasks}"
          if r["method"] == "paired" else f"Δ {r['delta']:+.3f}, unpaired, {tasks}")
    return f"{r['verdict']} vs {r['contender_shape']} ({ci})"


def to_markdown(a: Analysis, title: str = "Shape × model analysis") -> str:
    runs = a.runs
    lines = [f"# {title}", "",
             f"Generated {a.generated_at:%Y-%m-%d %H:%M} UTC by `hfx analyze`. Cost baseline `{a.baseline_id}`. "
             f"{runs.height} scored clean runs; arms: "
             + (", ".join(f"{s} `{r}`" for s, r in sorted(set(zip(runs["shape"].to_list(),
                                                               runs["recipe_id"].to_list(), strict=True))))
                or "-") + ".", "",
             ("Cells: mean E [95% bootstrap interval] n=runs. A winner is *clear* when the paired interval of its "
             "difference with every other arm (same task and seed) lies above 0 and the pairs cover at least "
             f"{MIN_TASKS} tasks; a *tie* is another arm with the same mean."), ""]
    if runs.is_empty():
        return "\n".join([*lines, "No scored runs of the compared recipes.", ""])

    lines += ["## 1. Shape × model by task class", ""]
    head, body = _pivot(a.by_class, ["model", "provider", "task_class"],
                        lambda g: ["model", "task class"] if g is None else [f"`{g['model']}` on {g['provider']}",
                                                                              _cls(g["task_class"])])
    lines += [_table(head, body), "", "### Winner per model and class", ""]
    lines += [_table(["model", "task class", "winner", "mean E", "verdict"], [
        [f"`{r['model']}`", _cls(r["task_class"]), f"{r['winner_shape']} `{r['winner_recipe']}`",
         f"{r['mean_e']:.3f}", _verdict(r)] for r in a.class_winners.iter_rows(named=True)]), ""]

    lines += ["## 2. Crossover by step depth", ""]
    for axis, name in (("depth", "expected depth"), ("plan_len", "reference plan length (tool calls)")):
        arms = a.by_depth.filter(pl.col("axis") == axis)
        if arms.is_empty():
            continue
        head, body = _pivot(arms, ["model", "provider", "bucket_order", "bucket"],
                            lambda g, n=name: ["model", n] if g is None else [f"`{g['model']}`", g["bucket"]])
        win = {(r["model"], r["bucket"]): r for r in a.depth_winners.filter(pl.col("axis") == axis)
               .iter_rows(named=True)}
        head += ["winner", "verdict"]
        for row, grp in zip(body, arms.select("model", "bucket").unique(maintain_order=True).iter_rows(),
                            strict=True):
            w = win[grp]
            row += [f"{w['winner_shape']} `{w['winner_recipe']}`", _verdict(w)]
        lines += [f"### By {name}", "", _table(head, body), ""]
        cx = a.crossover.filter(pl.col("axis") == axis)
        if cx.is_empty():
            lines += ["No crossover: the best arm is the same in every bucket.", ""]
        else:
            lines += [_table(["model", "from", "to", "best shape changes", "significant"], [
                [f"`{r['model']}`", r["from_bucket"], r["to_bucket"],
                 f"{r['from_shape']} `{r['from_recipe']}` → {r['to_shape']} `{r['to_recipe']}`",
                 "yes" if r["significant"] else f"no ({r['from_verdict']} → {r['to_verdict']})"]
                for r in cx.iter_rows(named=True)]), ""]
    lines += ["## 3. Method", "",
              (f"Intervals: scipy.stats.bootstrap, percentile, {N_RESAMPLES} resamples, seed 0. Paired intervals "
              f"agree with `report.paired_delta` within {a.interval_agreement():.4f} (largest endpoint gap). "
              f"Runs: scored (not INCOMPLETE / ERROR), clean (faulted runs only feed R), current recipe and "
              f"environment hashes. E is the cell E of `hfx results --cells`."), ""]
    return "\n".join(lines)


CSV_FILES = {
    "shape_model_class.csv": "by_class", "shape_class_winners.csv": "class_winners",
    "shape_class_comparisons.csv": "class_comparisons", "shape_depth.csv": "by_depth",
    "shape_depth_winners.csv": "depth_winners", "shape_depth_comparisons.csv": "depth_comparisons",
    "depth_crossover.csv": "crossover", "runs.csv": "runs",
}


def write_outputs(a: Analysis, out_dir: Path, title: str = "Shape × model analysis") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "analysis.md"]
    written[0].write_text(to_markdown(a, title) + "\n")
    for name, attr in CSV_FILES.items():
        p = out_dir / name
        getattr(a, attr).write_csv(p)
        written.append(p)
    return written


# ---------------------------------------------------------------- SQL views for Superset

_meta = MetaData()


def _stamp() -> list[Column]:
    return [Column("baseline_id", String(12), nullable=False), Column("generated_at", DateTime, nullable=False)]


ANL_TABLES = {
    "anl_shape_model_class": Table(
        "anl_shape_model_class", _meta,
        Column("model", String, nullable=False), Column("provider", String, nullable=False),
        Column("task_class", String, nullable=False), Column("depth", String, nullable=False),
        Column("shape", String, nullable=False), Column("recipe_id", String, nullable=False),
        Column("n", Integer, nullable=False), Column("mean_e", Float, nullable=False),
        Column("ci_lo", Float), Column("ci_hi", Float), Column("mean_e0", Float), Column("pass_rate", Float),
        Column("is_winner", Boolean, nullable=False), *_stamp()),
    "anl_shape_winners": Table(
        "anl_shape_winners", _meta,
        Column("scope", String, nullable=False),                 # class | depth | plan_len
        Column("model", String, nullable=False), Column("provider", String, nullable=False),
        Column("bucket", String, nullable=False),                # the task class, or the depth bucket
        Column("bucket_order", Integer),
        Column("winner_shape", String, nullable=False), Column("winner_recipe", String, nullable=False),
        Column("mean_e", Float, nullable=False), Column("n", Integer, nullable=False),
        Column("contender_shape", String), Column("contender_recipe", String), Column("delta", Float),
        Column("delta_ci_lo", Float), Column("delta_ci_hi", Float), Column("n_pairs", Integer),
        Column("method", String), Column("verdict", String, nullable=False), *_stamp()),
    "anl_shape_depth": Table(
        "anl_shape_depth", _meta,
        Column("axis", String, nullable=False), Column("model", String, nullable=False),
        Column("provider", String, nullable=False), Column("bucket", String, nullable=False),
        Column("bucket_order", Integer, nullable=False), Column("shape", String, nullable=False),
        Column("recipe_id", String, nullable=False), Column("n", Integer, nullable=False),
        Column("mean_e", Float, nullable=False), Column("ci_lo", Float), Column("ci_hi", Float),
        Column("pass_rate", Float), Column("is_winner", Boolean, nullable=False), *_stamp()),
    "anl_depth_crossover": Table(
        "anl_depth_crossover", _meta,
        Column("axis", String, nullable=False), Column("model", String, nullable=False),
        Column("provider", String, nullable=False), Column("from_bucket", String, nullable=False),
        Column("to_bucket", String, nullable=False), Column("from_shape", String, nullable=False),
        Column("to_shape", String, nullable=False), Column("from_recipe", String, nullable=False),
        Column("to_recipe", String, nullable=False), Column("from_verdict", String, nullable=False),
        Column("to_verdict", String, nullable=False), Column("significant", Boolean, nullable=False), *_stamp()),
}

# The interface Superset reads. Plain SELECTs, so the same text works on SQLite and Postgres.
VIEWS = {
    "v_shape_model_class": "anl_shape_model_class",
    "v_shape_winners": "anl_shape_winners",
    "v_shape_depth": "anl_shape_depth",
    "v_depth_crossover": "anl_depth_crossover",
}


def _rows_for(a: Analysis) -> dict[str, list[dict]]:
    stamp = {"baseline_id": a.baseline_id, "generated_at": a.generated_at}
    cw = {(r["model"], r["provider"], r["task_class"]): r["winner_recipe"]
          for r in a.class_winners.iter_rows(named=True)}
    dw = {(r["axis"], r["model"], r["provider"], r["bucket"]): r["winner_recipe"]
          for r in a.depth_winners.iter_rows(named=True)}
    cls_cols = [c.name for c in ANL_TABLES["anl_shape_model_class"].columns]
    dep_cols = [c.name for c in ANL_TABLES["anl_shape_depth"].columns]
    win_cols = [c.name for c in ANL_TABLES["anl_shape_winners"].columns]
    out: dict[str, list[dict]] = {k: [] for k in ANL_TABLES}
    for r in a.by_class.iter_rows(named=True):
        r = {**r, **stamp, "depth": r["task_class"].split("|")[0],
             "is_winner": cw.get((r["model"], r["provider"], r["task_class"])) == r["recipe_id"]}
        out["anl_shape_model_class"].append({k: r.get(k) for k in cls_cols})
    for r in a.by_depth.iter_rows(named=True):
        r = {**r, **stamp, "is_winner": dw.get((r["axis"], r["model"], r["provider"], r["bucket"])) == r["recipe_id"]}
        out["anl_shape_depth"].append({k: r.get(k) for k in dep_cols})
    for r in a.class_winners.iter_rows(named=True):
        r = {**r, **stamp, "scope": "class", "bucket": r["task_class"], "bucket_order": None}
        out["anl_shape_winners"].append({k: r.get(k) for k in win_cols})
    for r in a.depth_winners.iter_rows(named=True):
        r = {**r, **stamp, "scope": r["axis"]}
        out["anl_shape_winners"].append({k: r.get(k) for k in win_cols})
    for r in a.crossover.iter_rows(named=True):
        out["anl_depth_crossover"].append({**r, **stamp})
    return out


def materialize(engine: Engine, a: Analysis) -> list[str]:
    """Replace the analysis tables with this analysis and (re)create the views over them, in one transaction.
    Idempotent: running it twice leaves the same tables and views. Never touches `runs` or `cost_baselines`."""
    data = _rows_for(a)
    with engine.begin() as c:
        for v in VIEWS:
            c.execute(text(f"DROP VIEW IF EXISTS {v}"))
        _meta.drop_all(c, checkfirst=True)            # derived snapshots: drop so a column change needs no ALTER
        _meta.create_all(c)
        for name, rs in data.items():
            if rs:
                c.execute(ANL_TABLES[name].insert(), rs)
        for v, table in VIEWS.items():
            cols = ", ".join(col.name for col in ANL_TABLES[table].columns)
            c.execute(text(f"CREATE VIEW {v} AS SELECT {cols} FROM {table}"))
    return list(VIEWS)
