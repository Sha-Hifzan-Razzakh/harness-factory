"""ResultsStore (B8): one row per run. SQLite locally, Postgres in compose (same code, HF_RESULTS_URL)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)

from harness_factory import settings
from harness_factory.results.model import CellAggregate, EffectivenessRecord
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
)


class ResultsStore:
    def __init__(self, url: str | None = None) -> None:
        settings.ensure_dirs()
        self.engine = create_engine(url or settings.RESULTS_URL)
        metadata.create_all(self.engine)

    def add(self, rec: EffectivenessRecord) -> int:
        k = rec.run_key
        row = dict(
            run_key=k.as_str(), run_short=k.short(), task_version=k.task_version, task_class=rec.task_class,
            recipe_id=rec.recipe_id, recipe_hash=k.recipe_hash, canonical_model=k.canonical_model,
            provider=k.provider, seed=k.seed, status=rec.status.value, outcome=rec.scores.outcome,
            trajectory=rec.scores.trajectory, composite=rec.composite, tokens_in=rec.cost.tokens_in,
            tokens_out=rec.cost.tokens_out, model_calls=rec.cost.model_calls, tool_calls=rec.cost.tool_calls,
            wall_s=rec.cost.wall_s, usd=rec.cost.usd,
            violations=json.loads(json.dumps([v.model_dump() for v in rec.violations])),
            grades=json.loads(json.dumps(rec.grades, default=str)), trace_path=rec.trace_path,
            created_at=datetime.now(UTC),
        )
        with self.engine.begin() as c:
            return c.execute(insert(runs).values(**row)).inserted_primary_key[0]

    def recent(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(runs).order_by(runs.c.id.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def aggregate(self) -> list[tuple[str, str, CellAggregate]]:
        """Per cell (task class x recipe x model): mean composite, spread, bootstrap CI, pass rate."""
        groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        with self.engine.connect() as c:
            for r in c.execute(select(runs)).mappings():
                groups[(r["task_class"], r["recipe_hash"], r["canonical_model"])].append(dict(r))
        out = []
        for (tc, rh, m), rows in sorted(groups.items()):
            agg = CellAggregate.from_values(f"{tc}|{rh}|{m}", [r["composite"] for r in rows],
                                            [r["status"] == RunStatus.SUCCESS.value for r in rows])
            out.append((rows[0]["recipe_id"], m, agg))
        return out
