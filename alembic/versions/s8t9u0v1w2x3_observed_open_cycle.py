"""Record officially observed-open calls without inventing an exact opening date.

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s8t9u0v1w2x3"
down_revision = "r7s8t9u0v1w2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = {column["name"] for column in sa.inspect(conn).get_columns("opportunity_cycles")}
    if "observed_open_on" not in columns:
        op.add_column("opportunity_cycles", sa.Column("observed_open_on", sa.Date(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    columns = {column["name"] for column in sa.inspect(conn).get_columns("opportunity_cycles")}
    if "observed_open_on" not in columns:
        return
    if conn.execute(
        sa.text("SELECT 1 FROM opportunity_cycles WHERE observed_open_on IS NOT NULL LIMIT 1")
    ).first():
        raise RuntimeError("Refusing to discard observed-open evidence; export and review it first")
    op.drop_column("opportunity_cycles", "observed_open_on")
