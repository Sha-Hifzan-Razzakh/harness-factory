"""ResultsStore (B8): one row per run. SQLite locally, Postgres in compose (same code, HF_RESULTS_URL)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    inspect,
    select,
    text,
)

from harness_factory import settings
from harness_factory.results.cost import CostBaseline, is_clean, weight_profile_of
from harness_factory.results.model import CellAggregate, EffectivenessRecord, FamilyScores, ScoreWeights
from harness_factory.shapes.result import RunStatus

metadata = MetaData()

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_key", String, nullable=False, index=True),
    Column("run_short", String(12), nullable=False),
    Column("task_version", String, nullable=False),
    Column("task_class", String, nullable=False, index=True),
    Column("recipe_id", String, nullable=False),
    Column("recipe_hash", String, nullable=False),
    Column("canonical_model", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("outcome", Float, nullable=False),
    Column("trajectory", Float, nullable=False),
    Column("composite", Float, nullable=False),
    Column("tokens_in", Integer, nullable=False),
    Column("tokens_out", Integer, nullable=False),
    Column("model_calls", Integer, nullable=False),
    Column("tool_calls", Integer, nullable=False),
    Column("wall_s", Float, nullable=False),
    Column("usd", Float, nullable=False),
    Column("violations", JSON, nullable=False),
    Column("grades", JSON, nullable=False),
    Column("trace_path", String),
    Column("created_at", DateTime, nullable=False),
    Column("output", Text),                                  # added after v1: nullable, see _migrate()
    Column("transcript_path", String),
    Column("fault", String),                                 # fault profile id; NULL = clean run
    Column("tools_hash", String),                            # env without the provider model id (pooling, P2)
    Column("served_id", String),                             # provider model id that served the run
)

equivalences = Table(
    "equivalences", metadata,                                # cross-provider calibrations (P2), frozen
    Column("id", String(12), primary_key=True),              # content hash of what was compared and found
    Column("canonical_model", String, nullable=False, index=True),
    Column("record", JSON, nullable=False),                  # results.calibration.Equivalence
    Column("created_at", DateTime, nullable=False),
)

cost_baselines = Table(
    "cost_baselines", metadata,
    Column("id", String(12), primary_key=True),          # content hash of basis + medians
    Column("basis", String, nullable=False),            # display summary; per-provider bases below
    Column("medians", JSON, nullable=False),
    Column("bases", JSON),                              # added after v1: nullable, see _migrate()
    Column("counts", JSON, nullable=False),
    Column("note", String, nullable=False),
    Column("created_at", DateTime, nullable=False),
)


def record_row(rec: EffectivenessRecord) -> dict:
    """One EffectivenessRecord as a results-store row. `composite` is E0: the score before cost."""
    k = rec.run_key
    return dict(
        run_key=k.as_str(), run_short=k.short(), task_version=k.task_version, task_class=rec.task_class,
        recipe_id=rec.recipe_id, recipe_hash=k.recipe_hash, canonical_model=k.canonical_model,
        provider=k.provider, seed=k.seed, status=rec.status.value, outcome=rec.scores.outcome,
        trajectory=rec.scores.trajectory, composite=rec.composite, tokens_in=rec.cost.tokens_in,
        tokens_out=rec.cost.tokens_out, model_calls=rec.cost.model_calls, tool_calls=rec.cost.tool_calls,
        wall_s=rec.cost.wall_s, usd=rec.cost.usd,
        violations=json.loads(json.dumps([v.model_dump() for v in rec.violations])),
        grades=json.loads(json.dumps(rec.grades, default=str)), trace_path=rec.trace_path,
        created_at=datetime.now(UTC), output=rec.output, transcript_path=rec.transcript_path, fault=rec.fault,
        tools_hash=rec.tools_hash, served_id=rec.served_id,
    )


def env_of(row: dict) -> str:
    """env_hash from a stored run key (rows written before env_hash existed have 7 fields)."""
    parts = str(row.get("run_key", "")).split("|")
    return parts[7] if len(parts) > 7 else "none"


class CellResult(BaseModel):
    """One cell (task class x recipe x model x provider x environment), scored against a cost baseline.

    Provider and environment hash are part of the cell until cross-provider equivalence is calibrated (P2):
    the same canonical model on two providers, or before and after a tools.yaml / providers.yaml edit, is not
    yet known to be the same experiment.
    """
    task_class: str
    recipe_id: str
    recipe_hash: str
    canonical_model: str
    provider: str = ""
    env_hash: str = "none"
    e: CellAggregate                  # cost-adjusted E over scored runs: mean, spread, ci95, pass rate
    mean_e0: float                    # before cost
    mean_c: float                     # efficiency penalty input, 0..1
    excluded: int = 0                 # INCOMPLETE / ERROR runs: infrastructure or setup, not the harness
    robustness: float | None = None   # R: faulted / clean mean outcome, capped at 1; None until faulted runs exist
    n_faulted: int = 0                # scored faulted runs R was measured on
    tokens: int
    usd: float


# Runs that say nothing about the harness: provider down or out of quota (INCOMPLETE), or a setup problem / crash
# (ERROR). They are reported per cell, never averaged in as zeros.
UNSCORED = frozenset({RunStatus.INCOMPLETE.value, RunStatus.ERROR.value})


def _cell_key(r: dict, pools: dict | None = None) -> tuple[str, str, str, str, str]:
    """pools: (canonical model, provider, served id) -> pool label, for providers calibrated as equivalent
    (results.calibration.pools). A pooled run's cell is the pool and its provider-neutral tool environment."""
    label = (pools or {}).get((r["canonical_model"], r.get("provider", ""), r.get("served_id")))
    if label is not None and r.get("tools_hash"):
        return (r["task_class"], r["recipe_hash"], r["canonical_model"], label, f"tools:{r['tools_hash']}")
    return (r["task_class"], r["recipe_hash"], r["canonical_model"], r.get("provider", ""), env_of(r))


def robustness(clean: list[dict], faulted: list[dict]) -> float | None:
    """R = mean outcome under faults / mean outcome clean, capped at 1 (scored runs only).

    The requirements define R with pass rates; at a few seeds per cell a pass rate is 0, 0.5 or 1, so R would
    jump. Mean outcome gives the same answer when outcomes are 0/1 and also credits partial recovery."""
    if not clean or not faulted:
        return None
    clean_o = sum(r["outcome"] for r in clean) / len(clean)
    if clean_o <= 0:
        return None                                   # nothing to degrade from: R is undefined, not 0 or 1
    return min(1.0, (sum(r["outcome"] for r in faulted) / len(faulted)) / clean_o)


def _e0_with_r(r: dict, rob: float | None) -> float:
    """A run's composite recomputed once its cell's R is known (stored composite = E0 without R)."""
    if rob is None:
        return float(r["composite"])
    critical = any(v.get("critical") for v in (r.get("violations") or []))
    weights = ScoreWeights.for_profile(weight_profile_of(r["task_class"]))
    return weights.composite(FamilyScores(outcome=r["outcome"], trajectory=r["trajectory"], robustness=rob),
                             critical)


def aggregate_rows(rows: list[dict], baseline: CostBaseline, pools: dict | None = None) -> list[CellResult]:
    """Cells from clean runs; each cell's faulted runs only feed its R. Unscored runs are counted, never averaged.
    pools: see _cell_key; C stays per provider (each run against its own provider's median) before pooling."""
    clean_groups: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    faulted_groups: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    for r in rows:
        (clean_groups if is_clean(r) else faulted_groups)[_cell_key(r, pools)].append(r)
    out = []
    for key, all_rs in sorted(clean_groups.items(),
                              key=lambda kv: (kv[0][0], kv[1][0]["recipe_id"], kv[0][2], kv[0][3], kv[0][4])):
        tc, rh, m, prov, env = key
        rs = [r for r in all_rs if r["status"] not in UNSCORED]
        faulted = [r for r in faulted_groups.get(key, []) if r["status"] not in UNSCORED]
        rob = robustness(rs, faulted)
        e0s = [_e0_with_r(r, rob) for r in rs]
        n = len(rs) or 1
        out.append(CellResult(
            task_class=tc, recipe_id=all_rs[0]["recipe_id"], recipe_hash=rh, canonical_model=m, provider=prov,
            env_hash=env,
            e=CellAggregate.from_values(f"{tc}|{rh}|{m}|{prov}|{env}",
                                        [baseline.adjusted(r, e0) for r, e0 in zip(rs, e0s, strict=True)],
                                        [r["status"] == RunStatus.SUCCESS.value for r in rs]),
            mean_e0=sum(e0s) / n,
            mean_c=sum(baseline.c(r) or 0.0 for r in rs) / n,
            excluded=len(all_rs) - len(rs),
            robustness=rob, n_faulted=len(faulted),
            tokens=sum(r["tokens_in"] + r["tokens_out"] for r in all_rs), usd=sum(r["usd"] for r in all_rs)))
    return out


def _baseline_from_row(r) -> CostBaseline:
    # Baselines saved before per-provider medians have no `bases`; they cannot be read as per-provider medians.
    if not r["bases"]:
        raise ValueError(f"baseline {r['id']} predates per-provider medians; save a new one")
    return CostBaseline(bases=r["bases"], medians=r["medians"], counts=r["counts"], note=r["note"],
                        created_at=r["created_at"])


class ResultsStore:
    def __init__(self, url: str | None = None) -> None:
        settings.ensure_dirs()
        self.engine = create_engine(url or settings.RESULTS_URL)
        metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Additive only: add nullable columns that newer code defines and an older store lacks."""
        for table in (runs, cost_baselines, equivalences):
            existing = {c["name"] for c in inspect(self.engine).get_columns(table.name)}
            with self.engine.begin() as c:
                for col in table.columns:
                    if col.name not in existing and col.nullable:
                        c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" '
                                       f'{col.type.compile(dialect=self.engine.dialect)}'))

    def add(self, rec: EffectivenessRecord) -> int:
        with self.engine.begin() as c:
            return c.execute(insert(runs).values(**record_row(rec))).inserted_primary_key[0]

    def recent(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(runs).order_by(runs.c.id.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def rows(self, latest: bool = True) -> list[dict]:
        """Stored runs, oldest first. latest=True keeps only the newest row per RunKey: a rerun (resume, or the
        same campaign run twice) replaces its earlier result instead of being counted twice."""
        with self.engine.connect() as c:
            all_rows = [dict(r) for r in c.execute(select(runs).order_by(runs.c.id)).mappings()]
        if not latest:
            return all_rows
        newest = {r["run_key"]: r for r in all_rows}
        return [r for r in all_rows if newest[r["run_key"]] is r]

    def scored_keys(self) -> set[str]:
        """RunKeys whose newest result is scored (anything but INCOMPLETE / ERROR): done, skip on resume."""
        return {r["run_key"] for r in self.rows() if r["status"] not in UNSCORED}

    def live_baseline(self) -> CostBaseline:
        return CostBaseline.from_rows(self.rows(), note="live")

    def save_baseline(self, baseline: CostBaseline) -> str:
        """Freeze a baseline. Idempotent: the id is a content hash, so saving the same medians twice is a no-op."""
        if self.get_baseline(baseline.id) is None:
            with self.engine.begin() as c:
                c.execute(insert(cost_baselines).values(
                    id=baseline.id, basis=baseline.basis, bases=baseline.bases, medians=baseline.medians,
                    counts=baseline.counts, note=baseline.note, created_at=baseline.created_at))
        return baseline.id

    def get_baseline(self, baseline_id: str) -> CostBaseline | None:
        with self.engine.connect() as c:
            r = c.execute(select(cost_baselines).where(cost_baselines.c.id == baseline_id)).mappings().first()
        if r is None:
            return None
        return _baseline_from_row(r)

    def list_baselines(self) -> list[CostBaseline]:
        with self.engine.connect() as c:
            rs = c.execute(select(cost_baselines).order_by(cost_baselines.c.created_at)).mappings().all()
        return [_baseline_from_row(r) for r in rs if r["bases"]]       # pre-per-provider rows are skipped

    def legacy_baselines(self) -> list[str]:
        """Ids of baselines saved before per-provider medians: unreadable, listed so the CLI can say so."""
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(select(cost_baselines.c.id).where(cost_baselines.c.bases.is_(None)))]

    def stale_tasks(self) -> dict[str, int]:
        """task_version -> number of distinct task hashes stored for it (> 1 = the task changed between runs)."""
        hashes: dict[str, set[str]] = defaultdict(set)
        for r in self.rows():
            hashes[r["task_version"]].add(str(r["run_key"]).split("|")[1])
        return {t: len(h) for t, h in sorted(hashes.items()) if len(h) > 1}

    def aggregate(self, baseline: CostBaseline | None = None,
                  pools: dict | None = None) -> tuple[CostBaseline, list[CellResult]]:
        """Per cell (task class x recipe x model), cost-adjusted. Without a baseline, the live one is used.
        pools: pool calibrated-equivalent providers (results.calibration.pools)."""
        rows = self.rows()
        baseline = baseline or CostBaseline.from_rows(rows, note="live")
        return baseline, aggregate_rows(rows, baseline, pools)

    def save_equivalence(self, eq) -> str:
        """Freeze a calibration (results.calibration.Equivalence). Idempotent: the id is a content hash."""
        with self.engine.begin() as c:
            if c.execute(select(equivalences.c.id).where(equivalences.c.id == eq.id)).first() is None:
                c.execute(insert(equivalences).values(id=eq.id, canonical_model=eq.canonical_model,
                                                      record=eq.model_dump(mode="json"), created_at=eq.created_at))
        return eq.id

    def equivalences(self) -> list[dict]:
        """Saved calibrations as dicts, oldest first (results.calibration.Equivalence.model_validate reads them)."""
        with self.engine.connect() as c:
            rs = c.execute(select(equivalences.c.record).order_by(equivalences.c.created_at)).all()
        return [r[0] for r in rs]
