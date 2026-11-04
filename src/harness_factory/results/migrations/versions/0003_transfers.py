"""Transfers: frozen runner transfer checks (ADR 0016).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03

One row per `hfx transfer run` verdict: does a recipe's score carry over from the native runner to a framework
runner. Before Alembic, `metadata.create_all` made this table on the runner-axis branch, so a store from that
code may already have it; like 0001 and 0002, this revision adopts it.

The table is a frozen copy, not imported from store.py: this revision must not move when store.py does.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _transfers(md: sa.MetaData) -> sa.Table:
    return sa.Table(
        "transfers", md,
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("runtime", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Index("ix_transfers_runtime", "runtime"),
    )


def upgrade() -> None:
    table = _transfers(sa.MetaData())
    if context.is_offline_mode():                                       # `alembic upgrade --sql`: a new store
        op.create_table(table.name, *(sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
                                      for c in table.columns))
        op.create_index("ix_transfers_runtime", table.name, ["runtime"])
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table.name not in insp.get_table_names():
        table.create(bind)                                              # with its index
        return
    if "ix_transfers_runtime" not in {ix["name"] for ix in insp.get_indexes(table.name)}:
        op.create_index("ix_transfers_runtime", table.name, ["runtime"])


def downgrade() -> None:
    op.drop_index("ix_transfers_runtime", table_name="transfers")
    op.drop_table("transfers")
