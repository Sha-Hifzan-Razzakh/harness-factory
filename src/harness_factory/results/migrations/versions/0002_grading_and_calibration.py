"""Grading and calibration: what main added to the results schema while Alembic was in review.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03

- runs: grader_versions, endstate_path (#48), judge_usage (#49), tools_hash, served_id (#35), all nullable.
- equivalences (#35): cross-provider calibrations, frozen.
- regrades (#48): re-grades of stored runs; never read by aggregation.
- judge_calibrations (#49): rubric judge calibration records.

Before Alembic, `ResultsStore._migrate` added these columns and `metadata.create_all` these tables, so a store
from that code may already have them. Like 0001, this revision adopts what is there: a missing table is created,
a missing column added, and nothing that exists is changed.

The tables are a frozen copy, not imported from store.py: this revision must not move when store.py does.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RUNS_COLUMNS = (
    sa.Column("grader_versions", sa.JSON),
    sa.Column("endstate_path", sa.String),
    sa.Column("judge_usage", sa.JSON),
    sa.Column("tools_hash", sa.String),
    sa.Column("served_id", sa.String),
)


def _tables() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "equivalences", md,
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("canonical_model", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Index("ix_equivalences_canonical_model", "canonical_model"),
    )
    sa.Table(
        "regrades", md,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("run_key", sa.String, nullable=False),
        sa.Column("task_version", sa.String, nullable=False),
        sa.Column("task_hash", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("outcome", sa.Float, nullable=False),
        sa.Column("trajectory", sa.Float, nullable=False),
        sa.Column("composite", sa.Float, nullable=False),
        sa.Column("violations", sa.JSON, nullable=False),
        sa.Column("grades", sa.JSON, nullable=False),
        sa.Column("grader_versions", sa.JSON, nullable=False),
        sa.Column("changed", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("judge_usage", sa.JSON),
        sa.Index("ix_regrades_run_id", "run_id"),
    )
    sa.Table(
        "judge_calibrations", md,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("judge_hash", sa.String, nullable=False),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("set_name", sa.String, nullable=False),
        sa.Column("set_hash", sa.String, nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("failed", sa.Integer, nullable=False),
        sa.Column("kappa", sa.Float),
        sa.Column("mae", sa.Float),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Index("ix_judge_calibrations_judge_hash", "judge_hash"),
    )
    return md


def _col(c: sa.Column) -> sa.Column:
    return sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, autoincrement=c.autoincrement)


def upgrade() -> None:
    md = _tables()
    if context.is_offline_mode():                                       # `alembic upgrade --sql`: a new store
        for col in RUNS_COLUMNS:
            op.add_column("runs", _col(col))
        for table in md.sorted_tables:
            op.create_table(table.name, *(_col(c) for c in table.columns))
            for ix in table.indexes:
                op.create_index(ix.name, table.name, [c.name for c in ix.columns])
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    have = {c["name"] for c in insp.get_columns("runs")}
    for col in RUNS_COLUMNS:
        if col.name not in have:
            op.add_column("runs", _col(col))
    existing = set(insp.get_table_names())
    for table in md.sorted_tables:
        if table.name not in existing:
            table.create(bind)                                          # with its indexes
            continue
        # A pre-Alembic store: add what the old additive migration would have added, nothing else.
        cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in cols and col.nullable:
                op.add_column(table.name, _col(col))
                cols.add(col.name)
        indexed = {ix["name"] for ix in insp.get_indexes(table.name)}
        for ix in table.indexes:
            if ix.name not in indexed and all(c.name in cols for c in ix.columns):
                op.create_index(ix.name, table.name, [c.name for c in ix.columns])


def downgrade() -> None:
    op.drop_index("ix_judge_calibrations_judge_hash", table_name="judge_calibrations")
    op.drop_table("judge_calibrations")
    op.drop_index("ix_regrades_run_id", table_name="regrades")
    op.drop_table("regrades")
    op.drop_index("ix_equivalences_canonical_model", table_name="equivalences")
    op.drop_table("equivalences")
    with op.batch_alter_table("runs") as batch:                         # SQLite can't drop columns in place
        for col in reversed(RUNS_COLUMNS):
            batch.drop_column(col.name)
