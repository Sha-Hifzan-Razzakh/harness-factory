"""Baseline: the results schema as it was before Alembic (runs, cost_baselines).

Revision ID: 0001
Revises:
Create Date: 2026-11-02

Stores created before Alembic have these tables but no alembic_version. They are adopted here instead of
stamped blind: a missing table is created, and a nullable column the old code added later (output,
transcript_path, fault, bases) is added if the store predates it. That is exactly what the old in-code
migration (`ResultsStore._migrate`, additive only) did, so every store it could open opens here too.

The tables are a frozen copy, not imported from store.py: the baseline must not move when store.py does.
Every later change is its own revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _tables() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "runs", md,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_key", sa.String, nullable=False),
        sa.Column("run_short", sa.String(12), nullable=False),
        sa.Column("task_version", sa.String, nullable=False),
        sa.Column("task_class", sa.String, nullable=False),
        sa.Column("recipe_id", sa.String, nullable=False),
        sa.Column("recipe_hash", sa.String, nullable=False),
        sa.Column("canonical_model", sa.String, nullable=False),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("seed", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("outcome", sa.Float, nullable=False),
        sa.Column("trajectory", sa.Float, nullable=False),
        sa.Column("composite", sa.Float, nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False),
        sa.Column("tokens_out", sa.Integer, nullable=False),
        sa.Column("model_calls", sa.Integer, nullable=False),
        sa.Column("tool_calls", sa.Integer, nullable=False),
        sa.Column("wall_s", sa.Float, nullable=False),
        sa.Column("usd", sa.Float, nullable=False),
        sa.Column("violations", sa.JSON, nullable=False),
        sa.Column("grades", sa.JSON, nullable=False),
        sa.Column("trace_path", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("output", sa.Text),
        sa.Column("transcript_path", sa.String),
        sa.Column("fault", sa.String),
        sa.Index("ix_runs_run_key", "run_key"),
        sa.Index("ix_runs_task_class", "task_class"),
    )
    sa.Table(
        "cost_baselines", md,
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("basis", sa.String, nullable=False),
        sa.Column("medians", sa.JSON, nullable=False),
        sa.Column("bases", sa.JSON),
        sa.Column("counts", sa.JSON, nullable=False),
        sa.Column("note", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    return md


def _col(c: sa.Column) -> sa.Column:
    return sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, autoincrement=c.autoincrement)


def upgrade() -> None:
    md = _tables()
    if context.is_offline_mode():                                       # `alembic upgrade --sql`: a new store
        for table in md.sorted_tables:
            op.create_table(table.name, *(_col(c) for c in table.columns))
            for ix in table.indexes:
                op.create_index(ix.name, table.name, [c.name for c in ix.columns])
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for table in md.sorted_tables:
        if table.name not in existing:
            table.create(bind)                                          # with its indexes
            continue
        # A pre-Alembic store: add what the old additive migration would have added, nothing else.
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable:
                op.add_column(table.name, _col(col))
                have.add(col.name)
        indexed = {ix["name"] for ix in insp.get_indexes(table.name)}
        for ix in table.indexes:
            if ix.name not in indexed and all(c.name in have for c in ix.columns):
                op.create_index(ix.name, table.name, [c.name for c in ix.columns])


def downgrade() -> None:
    op.drop_table("cost_baselines")
    op.drop_index("ix_runs_task_class", table_name="runs")
    op.drop_index("ix_runs_run_key", table_name="runs")
    op.drop_table("runs")
