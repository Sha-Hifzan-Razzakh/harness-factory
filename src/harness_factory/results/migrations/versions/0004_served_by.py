"""served_by: the upstream endpoint(s) that served a run's model calls (#124, A5).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11

A router (OpenRouter) can serve one model id from several upstream providers. A run records which ones answered
its calls ("DeepInfra", or "DeepInfra,Together" when fallbacks were allowed), next to `served_id`. Nullable: older
runs and providers that don't say are NULL. Adopts the column if a store already has it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():                                       # `alembic upgrade --sql`: a new store
        op.add_column("runs", sa.Column("served_by", sa.String))
        return
    if "served_by" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("runs")}:
        op.add_column("runs", sa.Column("served_by", sa.String))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:                         # SQLite can't drop columns in place
        batch.drop_column("served_by")
