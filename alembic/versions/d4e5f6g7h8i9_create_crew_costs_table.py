"""create_crew_costs_table

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-03-04 12:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "crew_costs" not in existing:
        op.create_table(
            "crew_costs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("territory", sa.Text(), nullable=True),
            sa.Column("role", sa.Text(), nullable=True),
            sa.Column("category", sa.Text(), nullable=True),
            sa.Column("day_rate", sa.Float(), nullable=True),
            sa.Column("week_rate", sa.Float(), nullable=True),
            sa.Column("union", sa.Text(), nullable=True),
            sa.Column("last_updated", sa.Text(), nullable=True),
            sa.Column("source", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("crew_costs")
