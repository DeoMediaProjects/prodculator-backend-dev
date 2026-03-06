"""add_record_label_to_pending_changes

Revision ID: d738ccebf985
Revises: c627bbdae874
Create Date: 2026-03-06 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d738ccebf985"
down_revision = "c627bbdae874"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("pending_changes")}
    if "record_label" not in columns:
        op.add_column(
            "pending_changes",
            sa.Column("record_label", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("pending_changes", "record_label")
