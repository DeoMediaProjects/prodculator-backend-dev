"""create_incentive_programs_table

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-03-04 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "incentive_programs" not in existing:
        op.create_table(
            "incentive_programs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("territory", sa.Text(), nullable=True),
            sa.Column("program", sa.Text(), nullable=True),
            sa.Column("rate", sa.Text(), nullable=True),
            sa.Column("cap", sa.Text(), nullable=True),
            sa.Column("last_updated", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("auto_sync_enabled", sa.Boolean(), nullable=True),
            sa.Column("last_auto_check", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("incentive_programs")
